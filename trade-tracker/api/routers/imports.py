"""
Fidelity CSV import router.

Endpoints:
  POST /import/fidelity          - upload Fidelity trade history CSV
  GET  /import/fidelity          - list all past imports (audit log)
  GET  /import/fidelity/{id}     - details for a specific import
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

import db
from models.schemas import FidelityImportResponse
from services.fidelity_parser import parse_fidelity_csv
from services.ibkr_parser import parse_ibkr_csv

router = APIRouter(prefix="/import", tags=["imports"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


@router.post("/fidelity", response_model=FidelityImportResponse)
async def upload_fidelity_csv(
    file: UploadFile = File(..., description="Fidelity Activity & Orders CSV export"),
    account_id: str = Form(..., description="Account ID to tag these trades with (e.g. FIDELITY_MAIN)"),
    pool=Depends(get_pool),
):
    """
    Upload a Fidelity trade history CSV.

    How to export from Fidelity:
    1. Log in to Fidelity → Accounts & Trade → Portfolio
    2. Select account → Activity & Orders tab
    3. Click 'Download' → choose CSV format
    4. Upload that file here.

    Trades are parsed and inserted into the trades table.
    Labels are NOT set on import - use PATCH /trades/{id}/label to label them.
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
        lines = []
        for row in rows:
            lines.append(",".join(
                (str(cell) if cell is not None else "") for cell in row
            ))
        csv_text = "\n".join(lines)
    else:
        try:
            csv_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            csv_text = raw_bytes.decode("latin-1")

    # Create import audit row first (we need the import_id for FK)
    import_id = await pool.fetchval(
        """
        INSERT INTO fidelity_imports (filename, account_id, raw_csv, status)
        VALUES ($1, $2, $3, 'pending')
        RETURNING id
        """,
        file.filename, account_id, csv_text,
    )

    # Parse CSV
    trades, errors = parse_fidelity_csv(csv_text, account_id, import_id)

    success_count = 0
    error_count = len(errors)

    # Insert parsed trades
    for trade in trades:
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
                trade.source,
                trade.account_id,
                trade.trade_date,
                trade.symbol,
                trade.side,
                trade.quantity,
                trade.price,
                trade.commission,
                trade.gross_amount,
                trade.net_amount,
                trade.label,
                trade.is_hedge,
                trade.notes,
                json.dumps(trade.raw_data) if trade.raw_data else None,
                import_id,
            )
            success_count += 1
        except Exception as exc:
            logger.error("Failed to insert trade %s %s: %s", trade.symbol, trade.trade_date, exc)
            errors.append(f"DB insert failed for {trade.symbol} on {trade.trade_date}: {exc}")
            error_count += 1

    # Determine final status
    if success_count == 0 and error_count > 0:
        status = "error"
        error_msg = "; ".join(errors[:5])  # first 5 errors
    elif error_count > 0:
        status = "partial"
        error_msg = f"{error_count} rows failed. First errors: " + "; ".join(errors[:3])
    else:
        status = "success"
        error_msg = None

    # Update import audit row
    await pool.execute(
        """
        UPDATE fidelity_imports
        SET status = $1, row_count = $2, success_count = $3, error_count = $4, error_message = $5
        WHERE id = $6
        """,
        status, len(trades) + error_count, success_count, error_count, error_msg, import_id,
    )

    logger.info(
        "Fidelity import %d: %d/%d rows succeeded, status=%s",
        import_id, success_count, len(trades), status,
    )

    return FidelityImportResponse(
        import_id=import_id,
        filename=file.filename,
        account_id=account_id,
        status=status,
        row_count=len(trades) + error_count,
        success_count=success_count,
        error_count=error_count,
        error_message=error_msg,
        imported_at=await pool.fetchval(
            "SELECT imported_at FROM fidelity_imports WHERE id = $1", import_id
        ),
    )


@router.post("/ibkr", response_model=FidelityImportResponse)
async def upload_ibkr_csv(
    file: UploadFile = File(..., description="IBKR Activity Statement CSV export"),
    account_id: str = Form(..., description="Account ID to tag these trades with (e.g. IBKR_U1234567)"),
    pool=Depends(get_pool),
):
    """
    Upload an IBKR Activity Statement CSV for historical trade import.

    How to export from IBKR Client Portal:
    1. Log in → Performance & Reports → Activity Statements
    2. Set your date range (use 'Custom' and go back as far as needed)
    3. Format: CSV → Run → Download
    4. Upload that file here.

    This is a one-time upload for historical data. New trades sync automatically
    every hour via the IBKR API — no further uploads needed going forward.

    Duplicate trades (same symbol, date, quantity, price) are skipped automatically.
    """
    if not file.filename or not file.filename.endswith(".csv"):
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

    success_count = 0
    error_count = len(errors)

    for trade in trades:
        # Deduplicate: skip if a trade with same key fields already exists
        existing = await pool.fetchval(
            """
            SELECT id FROM trades
            WHERE source = 'ibkr' AND account_id = $1 AND symbol = $2
              AND side = $3 AND ABS(quantity - $4) < 0.0001 AND ABS(price - $5) < 0.0001
              AND trade_date::date = $6::date
            LIMIT 1
            """,
            trade.account_id, trade.symbol, trade.side,
            float(trade.quantity), float(trade.price), trade.trade_date,
        )
        if existing:
            skipped = error_count  # track quietly
            continue

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
                import_id,
            )
            success_count += 1
        except Exception as exc:
            logger.error("Failed to insert IBKR trade %s %s: %s", trade.symbol, trade.trade_date, exc)
            errors.append(f"DB insert failed for {trade.symbol} on {trade.trade_date}: {exc}")
            error_count += 1

    if success_count == 0 and error_count > 0:
        status = "error"
        error_msg = "; ".join(errors[:5])
    elif error_count > 0:
        status = "partial"
        error_msg = f"{error_count} rows failed. First errors: " + "; ".join(errors[:3])
    else:
        status = "success"
        error_msg = None

    await pool.execute(
        """
        UPDATE fidelity_imports
        SET status = $1, row_count = $2, success_count = $3, error_count = $4, error_message = $5
        WHERE id = $6
        """,
        status, len(trades) + error_count, success_count, error_count, error_msg, import_id,
    )

    logger.info("IBKR CSV import %d: %d/%d rows succeeded, status=%s", import_id, success_count, len(trades), status)

    return FidelityImportResponse(
        import_id=import_id,
        filename=file.filename,
        account_id=account_id,
        status=status,
        row_count=len(trades) + error_count,
        success_count=success_count,
        error_count=error_count,
        error_message=error_msg,
        imported_at=await pool.fetchval("SELECT imported_at FROM fidelity_imports WHERE id = $1", import_id),
    )


@router.get("/fidelity", response_model=list[FidelityImportResponse])
async def list_imports(pool=Depends(get_pool)):
    """List all past CSV imports — both Fidelity and IBKR history uploads (most recent first)."""
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


@router.get("/fidelity/{import_id}", response_model=FidelityImportResponse)
async def get_import(import_id: int, pool=Depends(get_pool)):
    row = await pool.fetchrow(
        """
        SELECT id, filename, account_id, status, row_count,
               success_count, error_count, error_message, imported_at
        FROM fidelity_imports WHERE id = $1
        """,
        import_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Import {import_id} not found")
    return FidelityImportResponse(
        import_id=row["id"],
        filename=row["filename"],
        account_id=row["account_id"],
        status=row["status"],
        row_count=row["row_count"],
        success_count=row["success_count"] or 0,
        error_count=row["error_count"] or 0,
        error_message=row["error_message"],
        imported_at=row["imported_at"],
    )
