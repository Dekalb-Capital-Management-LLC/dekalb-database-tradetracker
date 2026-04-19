"""
Import router.

  POST /import/trades   — universal endpoint, auto-detects any CSV/XLSX/TSV format
  GET  /import/history  — list all past imports (audit log)

Legacy paths kept for compatibility:
  POST /import/ibkr     → same as /import/trades
  POST /import/fidelity → same as /import/trades
"""
from __future__ import annotations

import io as _io
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

import db
from models.schemas import FidelityImportResponse
from services.universal_parser import auto_parse

router = APIRouter(prefix="/import", tags=["imports"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

async def _run_backfill(pool):
    try:
        from services.portfolio_metrics import backfill_snapshots
        result = await backfill_snapshots(pool)
        logger.info("Post-import backfill complete: %s", result)
    except Exception as exc:
        logger.error("Post-import backfill failed: %s", exc)


# ---------------------------------------------------------------------------
# Core insert with dedup
# ---------------------------------------------------------------------------

async def _insert_trades(pool, trades, source_label: str):
    """Insert trades, skipping exact duplicates. Returns (success_count, errors)."""
    success = 0
    errors: list[str] = []

    for trade in trades:
        existing = await pool.fetchval(
            """
            SELECT id FROM trades
            WHERE source = $1 AND account_id = $2 AND symbol = $3
              AND side = $4
              AND ABS(quantity - $5) < 0.0001
              AND ABS(price   - $6) < 0.0001
              AND trade_date::date = $7::date
            LIMIT 1
            """,
            trade.source, trade.account_id, trade.symbol, trade.side,
            float(trade.quantity), float(trade.price), trade.trade_date,
        )
        if existing:
            continue

        try:
            await pool.execute(
                """
                INSERT INTO trades
                    (source, account_id, trade_date, symbol, side,
                     quantity, price, commission, gross_amount, net_amount,
                     label, is_hedge, notes, raw_data, fidelity_import_id)
                VALUES ($1,$2,$3,$4,$5, $6,$7,$8,$9,$10, $11,$12,$13,$14,$15)
                """,
                trade.source, trade.account_id, trade.trade_date, trade.symbol, trade.side,
                float(trade.quantity), float(trade.price), float(trade.commission),
                float(trade.gross_amount), float(trade.net_amount),
                trade.label, trade.is_hedge, trade.notes,
                json.dumps(trade.raw_data) if trade.raw_data else None,
                trade.fidelity_import_id,
            )
            success += 1
        except Exception as exc:
            logger.error("Insert failed %s %s %s: %s", source_label, trade.symbol, trade.trade_date, exc)
            errors.append(f"{trade.symbol} {trade.trade_date}: {exc}")

    return success, errors


# ---------------------------------------------------------------------------
# File reading helper (handles CSV + XLSX + TSV)
# ---------------------------------------------------------------------------

def _read_as_text(raw_bytes: bytes, filename: str) -> str:
    fname = filename.lower()
    if fname.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(_io.BytesIO(raw_bytes), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        # Detect if source data is tab-separated (unlikely in xlsx but handle it)
        return "\n".join(
            "\t".join("" if cell is None else str(cell) for cell in row)
            for row in rows
        )
    # CSV / TSV / plain text
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Unified endpoint
# ---------------------------------------------------------------------------

@router.post("/trades", response_model=FidelityImportResponse)
async def upload_trades(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pool=Depends(get_pool),
):
    """
    Universal trade importer — drop any CSV, XLSX, or TSV file.

    Auto-detects format:
      • IBKR Activity Statement CSV
      • Fidelity Activity / Positions CSV or XLSX
      • Simple portfolio CSV  (Ticker | Date Acquired | Amount | Price Acquired)

    Account ID is extracted from the file automatically.
    Duplicate trades are skipped.
    Performance snapshots rebuild in the background after a successful import.
    """
    fname = file.filename or "upload"
    ext = fname.rsplit(".", 1)[-1].lower()
    if ext not in ("csv", "xlsx", "tsv", "txt"):
        raise HTTPException(status_code=400, detail="Supported file types: .csv, .xlsx, .tsv")

    raw_bytes = await file.read()
    text = _read_as_text(raw_bytes, fname)

    # Create audit record (account_id filled in after parse)
    import_id = await pool.fetchval(
        """
        INSERT INTO fidelity_imports (filename, account_id, raw_csv, status, source)
        VALUES ($1, 'detecting', $2, 'pending', 'auto')
        RETURNING id
        """,
        fname, text,
    )

    trades, errors, account_id, source_label = auto_parse(text, import_id)
    parse_error_count = len(errors)

    success_count, insert_errors = await _insert_trades(pool, trades, source_label)
    errors.extend(insert_errors)
    error_count = len(errors)

    if success_count == 0 and error_count > 0:
        status = "error"
        error_msg = "; ".join(errors[:5])
    elif error_count > 0:
        status = "partial"
        error_msg = f"{error_count} rows skipped. First: " + "; ".join(errors[:3])
    else:
        status = "success"
        error_msg = None

    await pool.execute(
        """
        UPDATE fidelity_imports
        SET status=$1, account_id=$2, source=$3,
            row_count=$4, success_count=$5, error_count=$6, error_message=$7
        WHERE id=$8
        """,
        status, account_id, source_label,
        len(trades) + parse_error_count, success_count, error_count, error_msg,
        import_id,
    )

    logger.info("Import %d [%s / %s]: %d inserted, %d errors", import_id, source_label, account_id, success_count, error_count)

    if success_count > 0:
        background_tasks.add_task(_run_backfill, pool)

    imported_at = await pool.fetchval("SELECT imported_at FROM fidelity_imports WHERE id=$1", import_id)
    return FidelityImportResponse(
        import_id=import_id,
        filename=fname,
        account_id=account_id,
        status=status,
        row_count=len(trades) + parse_error_count,
        success_count=success_count,
        error_count=error_count,
        error_message=error_msg,
        imported_at=imported_at,
    )


# ---------------------------------------------------------------------------
# Legacy endpoints — route through the same logic
# ---------------------------------------------------------------------------

@router.post("/ibkr", response_model=FidelityImportResponse)
async def upload_ibkr(background_tasks: BackgroundTasks, file: UploadFile = File(...), pool=Depends(get_pool)):
    return await upload_trades(background_tasks, file, pool)


@router.post("/fidelity", response_model=FidelityImportResponse)
async def upload_fidelity(background_tasks: BackgroundTasks, file: UploadFile = File(...), pool=Depends(get_pool)):
    return await upload_trades(background_tasks, file, pool)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@router.get("/history", response_model=list[FidelityImportResponse])
async def list_imports(pool=Depends(get_pool)):
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


@router.get("/fidelity", response_model=list[FidelityImportResponse])
async def list_imports_legacy(pool=Depends(get_pool)):
    return await list_imports(pool)
