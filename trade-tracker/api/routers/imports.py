"""
Import router.

Endpoints:
  POST /import/ibkr              - upload IBKR Activity Statement CSV
  POST /import/fidelity          - upload Fidelity trade history CSV / XLSX
  GET  /import/history           - list all past imports (audit log)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

import db
from models.schemas import FidelityImportResponse
from services.fidelity_parser import parse_fidelity_csv
from services.ibkr_parser import parse_ibkr_csv

router = APIRouter(prefix="/import", tags=["imports"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


async def _run_backfill(pool):
    """Trigger a full snapshot backfill in the background after an import."""
    try:
        from services.portfolio_metrics import backfill_snapshots
        result = await backfill_snapshots(pool)
        logger.info("Post-import backfill complete: %s", result)
    except Exception as exc:
        logger.error("Post-import backfill failed: %s", exc)


async def _insert_trades(pool, trades, source_label):
    """Insert trades with deduplication. Returns (success_count, error_list)."""
    success_count = 0
    errors = []

    for trade in trades:
        # Deduplicate by exact match on key fields
        existing = await pool.fetchval(
            """
            SELECT id FROM trades
            WHERE source = $1 AND account_id = $2 AND symbol = $3
              AND side = $4
              AND ABS(quantity - $5) < 0.0001
              AND ABS(price - $6) < 0.0001
              AND trade_date::date = $7::date
            LIMIT 1
            """,
            trade.source, trade.account_id, trade.symbol, trade.side,
            float(trade.quantity), float(trade.price), trade.trade_date,
        )
        if existing:
            continue  # skip duplicate, do not count as error

        try:
            await pool.execute(
                """
                INSERT INTO trades
                    (source, account_id, trade_date, symbol, side,
                     quantity, price, commission, gross_amount, net_amount,
                     label, is_hedge, notes, raw_data, fidelity_import_id)
                VALUES
                    ($1, $2, $3, $4, $5,
                     $6, $7, $8, $9, $10,
                     $11, $12, $13, $14, $15)
                """,
                trade.source, trade.account_id, trade.trade_date, trade.symbol, trade.side,
                float(trade.quantity), float(trade.price), float(trade.commission),
                float(trade.gross_amount), float(trade.net_amount),
                trade.label, trade.is_hedge, trade.notes,
                json.dumps(trade.raw_data) if trade.raw_data else None,
                trade.fidelity_import_id,
            )
            success_count += 1
        except Exception as exc:
            logger.error("Failed to insert %s trade %s %s: %s", source_label, trade.symbol, trade.trade_date, exc)
            errors.append(f"DB insert failed for {trade.symbol} on {trade.trade_date}: {exc}")

    return success_count, errors


@router.post("/ibkr", response_model=FidelityImportResponse)
async def upload_ibkr_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="IBKR Activity Statement CSV export"),
    account_id: str = Form(..., description="Account ID (e.g. F16173704 or IBKR_MAIN)"),
    pool=Depends(get_pool),
):
    """
    Upload an IBKR Activity Statement CSV.

    How to export:
      Client Portal → Performance & Reports → Activity Statements
      → Custom date range → Format: CSV → Run → Download

    You can upload multiple CSVs (e.g. one per year) — duplicates are skipped automatically.
    After upload, historical performance snapshots are rebuilt in the background (~30s).
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    raw_bytes = await file.read()
    try:
        csv_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        csv_text = raw_bytes.decode("latin-1")

    import_id = await pool.fetchval(
        """
        INSERT INTO fidelity_imports (filename, account_id, raw_csv, status, source)
        VALUES ($1, $2, $3, 'pending', 'ibkr')
        RETURNING id
        """,
        file.filename, account_id, csv_text,
    )

    trades, errors = parse_ibkr_csv(csv_text, account_id, import_id)
    parse_error_count = len(errors)

    success_count, insert_errors = await _insert_trades(pool, trades, "IBKR")
    errors.extend(insert_errors)
    error_count = len(errors)

    if success_count == 0 and error_count > 0:
        status = "error"
        error_msg = "; ".join(errors[:5])
    elif error_count > 0:
        status = "partial"
        error_msg = f"{error_count} rows failed. First: " + "; ".join(errors[:3])
    else:
        status = "success"
        error_msg = None

    await pool.execute(
        """
        UPDATE fidelity_imports
        SET status = $1, row_count = $2, success_count = $3, error_count = $4, error_message = $5
        WHERE id = $6
        """,
        status, len(trades) + parse_error_count, success_count, error_count, error_msg, import_id,
    )

    logger.info("IBKR CSV import %d: %d inserted, status=%s", import_id, success_count, status)

    # Rebuild historical snapshots in background so performance graph updates
    if success_count > 0:
        background_tasks.add_task(_run_backfill, pool)

    return FidelityImportResponse(
        import_id=import_id,
        filename=file.filename,
        account_id=account_id,
        status=status,
        row_count=len(trades) + parse_error_count,
        success_count=success_count,
        error_count=error_count,
        error_message=error_msg,
        imported_at=await pool.fetchval("SELECT imported_at FROM fidelity_imports WHERE id = $1", import_id),
    )


@router.post("/fidelity", response_model=FidelityImportResponse)
async def upload_fidelity_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Fidelity Activity & Orders CSV or XLSX export"),
    account_id: str = Form(..., description="Account ID (e.g. FIDELITY_MAIN or Z12345678)"),
    pool=Depends(get_pool),
):
    """
    Upload a Fidelity trade history CSV or XLSX.
    Supports two formats (auto-detected): positions snapshot and activity/orders.
    Duplicates skipped automatically.
    After upload, historical performance snapshots are rebuilt in the background.
    """
    fname = (file.filename or "").lower()
    if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="File must be a .csv or .xlsx")

    raw_bytes = await file.read()

    if fname.endswith(".xlsx"):
        import io as _io
        import openpyxl
        wb = openpyxl.load_workbook(_io.BytesIO(raw_bytes), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        csv_text = "\n".join(
            ",".join((str(cell) if cell is not None else "") for cell in row)
            for row in rows
        )
    else:
        try:
            csv_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            csv_text = raw_bytes.decode("latin-1")

    import_id = await pool.fetchval(
        """
        INSERT INTO fidelity_imports (filename, account_id, raw_csv, status)
        VALUES ($1, $2, $3, 'pending')
        RETURNING id
        """,
        file.filename, account_id, csv_text,
    )

    trades, errors = parse_fidelity_csv(csv_text, account_id, import_id)
    parse_error_count = len(errors)

    success_count, insert_errors = await _insert_trades(pool, trades, "Fidelity")
    errors.extend(insert_errors)
    error_count = len(errors)

    if success_count == 0 and error_count > 0:
        status = "error"
        error_msg = "; ".join(errors[:5])
    elif error_count > 0:
        status = "partial"
        error_msg = f"{error_count} rows failed. First: " + "; ".join(errors[:3])
    else:
        status = "success"
        error_msg = None

    await pool.execute(
        """
        UPDATE fidelity_imports
        SET status = $1, row_count = $2, success_count = $3, error_count = $4, error_message = $5
        WHERE id = $6
        """,
        status, len(trades) + parse_error_count, success_count, error_count, error_msg, import_id,
    )

    logger.info("Fidelity import %d: %d inserted, status=%s", import_id, success_count, status)

    if success_count > 0:
        background_tasks.add_task(_run_backfill, pool)

    return FidelityImportResponse(
        import_id=import_id,
        filename=file.filename,
        account_id=account_id,
        status=status,
        row_count=len(trades) + parse_error_count,
        success_count=success_count,
        error_count=error_count,
        error_message=error_msg,
        imported_at=await pool.fetchval("SELECT imported_at FROM fidelity_imports WHERE id = $1", import_id),
    )


@router.get("/history", response_model=list[FidelityImportResponse])
async def list_imports(pool=Depends(get_pool)):
    """List all past CSV imports (most recent first)."""
    rows = await pool.fetch(
        """
        SELECT id, filename, account_id, status, row_count,
               success_count, error_count, error_message, imported_at
        FROM fidelity_imports
        ORDER BY imported_at DESC
        LIMIT 100
        """
    )
    return [
        FidelityImportResponse(
            import_id=r["id"],
            filename=r["filename"],
            account_id=r["account_id"],
            status=r["status"],
            row_count=r["row_count"],
            success_count=r["success_count"] or 0,
            error_count=r["error_count"] or 0,
            error_message=r["error_message"],
            imported_at=r["imported_at"],
        )
        for r in rows
    ]


# Keep legacy path working so old frontend calls don't break
@router.get("/fidelity", response_model=list[FidelityImportResponse])
async def list_imports_legacy(pool=Depends(get_pool)):
    return await list_imports(pool)
