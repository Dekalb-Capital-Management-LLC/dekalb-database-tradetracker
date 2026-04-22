"""
Import router.

  POST /import/trades   — upload .xlsx portfolio file (multi-sheet: Ticker | Date | Amount | Price)
  GET  /import/history  — list past imports
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

import db
from models.schemas import FidelityImportResponse
from services.universal_parser import parse_portfolio_xlsx

router = APIRouter(prefix="/import", tags=["imports"])
logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = ("csv", "xlsx", "xlsm", "xls", "tsv", "txt")


def get_pool():
    return db.get_pool()


async def _run_backfill(pool):
    try:
        from services.portfolio_metrics import backfill_snapshots
        await backfill_snapshots(pool)
    except Exception as exc:
        logger.error("Post-import backfill failed: %s", exc)


@router.post("/trades", response_model=FidelityImportResponse)
async def upload_trades(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pool=Depends(get_pool),
):
    """
    Upload a portfolio .xlsx file (any number of sheets).
    Each sheet: Ticker | Date Acquired | Amount | Price Acquired
    Duplicate rows are skipped. Positions are aggregated per symbol and
    stored in imported_positions so the dashboard shows correct P&L.
    """
    fname = file.filename or "upload"
    if not fname.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Only .xlsx / .xlsm files are supported")

    raw_bytes = await file.read()
    preview = f"[binary XLSX — {len(raw_bytes):,} bytes]"

    import_id = await pool.fetchval(
        """
        INSERT INTO fidelity_imports (filename, account_id, raw_csv, status, source)
        VALUES ($1, 'PORTFOLIO', $2, 'pending', 'portfolio')
        RETURNING id
        """,
        fname, preview,
    )

    try:
        trades, errors = parse_portfolio_xlsx(raw_bytes, import_id)
    except Exception as exc:
        logger.exception("Parse failed for %s", fname)
        await pool.execute(
            "UPDATE fidelity_imports SET status='error', error_message=$1 WHERE id=$2",
            f"Parse failed: {exc}", import_id,
        )
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")

    # Clear old positions for this account so re-imports don't duplicate
    await pool.execute("DELETE FROM imported_positions WHERE account_id = 'PORTFOLIO'")

    # Insert trades (skip exact duplicates)
    success = 0
    for trade in trades:
        existing = await pool.fetchval(
            """
            SELECT id FROM trades
            WHERE source='portfolio' AND account_id=$1 AND symbol=$2
              AND ABS(quantity - $3) < 0.0001 AND ABS(price - $4) < 0.0001
              AND trade_date::date = $5::date
            LIMIT 1
            """,
            trade.account_id, trade.symbol, float(trade.quantity),
            float(trade.price), trade.trade_date,
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
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
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
            errors.append(f"{trade.symbol}: {exc}")

    # Aggregate by symbol for imported_positions (weighted avg cost)
    agg: dict[str, dict] = {}
    for t in trades:
        sym = t.symbol
        if sym not in agg:
            agg[sym] = {"qty": 0.0, "cost": 0.0}
        agg[sym]["qty"] += float(t.quantity)
        agg[sym]["cost"] += float(t.quantity) * float(t.price)

    for sym, data in agg.items():
        qty = data["qty"]
        if qty <= 0:
            continue
        avg_cost = data["cost"] / qty
        cost_basis = qty * avg_cost
        await pool.execute(
            """
            INSERT INTO imported_positions
                (import_id, account_id, symbol, quantity, avg_cost,
                 cost_basis_total, source, snapshot_date, updated_at)
            VALUES ($1,'PORTFOLIO',$2,$3,$4,$5,'portfolio',CURRENT_DATE,NOW())
            ON CONFLICT (account_id, symbol) DO UPDATE SET
                import_id        = EXCLUDED.import_id,
                quantity         = EXCLUDED.quantity,
                avg_cost         = EXCLUDED.avg_cost,
                cost_basis_total = EXCLUDED.cost_basis_total,
                snapshot_date    = EXCLUDED.snapshot_date,
                updated_at       = NOW()
            """,
            import_id, sym, qty, avg_cost, cost_basis,
        )

    error_count = len(errors)
    if success == 0 and error_count > 0:
        status = "error"
        error_msg = "; ".join(errors[:5])
    elif error_count > 0:
        status = "partial"
        error_msg = f"{error_count} rows skipped"
    else:
        status = "success"
        error_msg = None

    await pool.execute(
        """
        UPDATE fidelity_imports
        SET status=$1, account_id='PORTFOLIO', source='portfolio',
            row_count=$2, success_count=$3, error_count=$4, error_message=$5
        WHERE id=$6
        """,
        status, len(trades), success, error_count, error_msg, import_id,
    )

    logger.info("Import %d: %d inserted, %d errors, %d symbols aggregated",
                import_id, success, error_count, len(agg))

    if success > 0:
        background_tasks.add_task(_run_backfill, pool)

    imported_at = await pool.fetchval(
        "SELECT imported_at FROM fidelity_imports WHERE id=$1", import_id
    )
    return FidelityImportResponse(
        import_id=import_id,
        filename=fname,
        account_id="PORTFOLIO",
        status=status,
        row_count=len(trades),
        success_count=success,
        error_count=error_count,
        error_message=error_msg,
        imported_at=imported_at,
    )


# Legacy aliases kept for any existing frontend calls
@router.post("/ibkr", response_model=FidelityImportResponse)
async def upload_ibkr(bg: BackgroundTasks, file: UploadFile = File(...), pool=Depends(get_pool)):
    return await upload_trades(bg, file, pool)


@router.post("/fidelity", response_model=FidelityImportResponse)
async def upload_fidelity(bg: BackgroundTasks, file: UploadFile = File(...), pool=Depends(get_pool)):
    return await upload_trades(bg, file, pool)


@router.get("/history", response_model=list[FidelityImportResponse])
async def list_imports(pool=Depends(get_pool)):
    rows = await pool.fetch(
        """
        SELECT id, filename, account_id, status, row_count,
               success_count, error_count, error_message, imported_at
        FROM fidelity_imports
        ORDER BY imported_at DESC LIMIT 100
        """
    )
    return [
        FidelityImportResponse(
            import_id=r["id"], filename=r["filename"], account_id=r["account_id"],
            status=r["status"], row_count=r["row_count"],
            success_count=r["success_count"] or 0, error_count=r["error_count"] or 0,
            error_message=r["error_message"], imported_at=r["imported_at"],
        )
        for r in rows
    ]


# Keep old /import/fidelity GET for compat
@router.get("/fidelity", response_model=list[FidelityImportResponse])
async def list_imports_legacy(pool=Depends(get_pool)):
    return await list_imports(pool)
