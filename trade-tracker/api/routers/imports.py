"""
Import router.

  POST /import/trades   — universal endpoint, auto-detects any CSV/XLSX/XLSM/TSV format
  GET  /import/history  — list all past imports (audit log)

Legacy paths kept for compatibility:
  POST /import/ibkr     → same as /import/trades
  POST /import/fidelity → same as /import/trades
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

import db
from models.schemas import FidelityImportResponse
from services.universal_parser import auto_parse, persist_ibkr_conids
from services.fidelity_parser import extract_positions_snapshot

router = APIRouter(prefix="/import", tags=["imports"])
logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = ("csv", "xlsx", "xlsm", "xls", "tsv", "txt")


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
# Unified endpoint
# ---------------------------------------------------------------------------

@router.post("/trades", response_model=FidelityImportResponse)
async def upload_trades(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pool=Depends(get_pool),
):
    """
    Universal trade importer — drop any CSV, XLSX, XLSM, XLS, or TSV file.

    Auto-detects format:
      • IBKR Activity Statement
      • Fidelity Activity / Positions
      • Simple portfolio  (Ticker | Date Acquired | Amount | Price Acquired)

    Account ID is extracted from the file automatically.
    Duplicate trades are skipped.
    IBKR activity statements additionally populate the symbol→conid table.
    Performance snapshots rebuild in the background after a successful import.
    """
    fname = file.filename or "upload"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in _SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Supported file types: {', '.join('.' + e for e in _SUPPORTED_EXTENSIONS)}",
        )

    raw_bytes = await file.read()

    # Create audit record — store a readable text preview.
    # For binary formats (xlsx/xlsm), just record the filename;
    # null bytes (0x00 in Excel binaries) are rejected by PostgreSQL UTF-8.
    if ext in ("xlsx", "xlsm", "xls"):
        preview = f"[binary {ext.upper()} file — {len(raw_bytes):,} bytes]"
    else:
        try:
            preview = raw_bytes[:200_000].decode("utf-8", errors="replace").replace("\x00", "")
        except Exception:
            preview = ""

    import_id = await pool.fetchval(
        """
        INSERT INTO fidelity_imports (filename, account_id, raw_csv, status, source)
        VALUES ($1, 'detecting', $2, 'pending', 'auto')
        RETURNING id
        """,
        fname, preview,
    )

    try:
        trades, errors, account_id, source_label, text = auto_parse(raw_bytes, fname, import_id)
    except Exception as exc:
        logger.exception("Parse failed for %s", fname)
        await pool.execute(
            "UPDATE fidelity_imports SET status='error', error_message=$1 WHERE id=$2",
            f"Parse failed: {exc}", import_id,
        )
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")

    parse_error_count = len(errors)

    success_count, insert_errors = await _insert_trades(pool, trades, source_label)
    errors.extend(insert_errors)
    error_count = len(errors)

    # For Fidelity positions files: store the rich snapshot data (current value,
    # total gain/loss, last price, etc.) directly so the portfolio view can serve
    # the exact numbers Fidelity computed rather than recomputing from trades.
    positions_written = 0
    if source_label == "fidelity":
        try:
            pos_rows = extract_positions_snapshot(text, account_id, import_id)
            for p in pos_rows:
                await pool.execute(
                    """
                    INSERT INTO imported_positions
                        (import_id, account_id, symbol, quantity, last_price,
                         current_value, today_gain_loss, today_gl_pct,
                         total_gain_loss, total_gl_pct, cost_basis_total,
                         avg_cost, source, snapshot_date, updated_at)
                    VALUES ($1,$2,$3,$4,$5, $6,$7,$8, $9,$10,$11, $12,'fidelity',CURRENT_DATE,NOW())
                    ON CONFLICT (account_id, symbol) DO UPDATE SET
                        import_id        = EXCLUDED.import_id,
                        quantity         = EXCLUDED.quantity,
                        last_price       = EXCLUDED.last_price,
                        current_value    = EXCLUDED.current_value,
                        today_gain_loss  = EXCLUDED.today_gain_loss,
                        today_gl_pct     = EXCLUDED.today_gl_pct,
                        total_gain_loss  = EXCLUDED.total_gain_loss,
                        total_gl_pct     = EXCLUDED.total_gl_pct,
                        cost_basis_total = EXCLUDED.cost_basis_total,
                        avg_cost         = EXCLUDED.avg_cost,
                        snapshot_date    = EXCLUDED.snapshot_date,
                        updated_at       = NOW()
                    """,
                    p["import_id"], p["account_id"], p["symbol"],
                    float(p["quantity"]) if p["quantity"] else None,
                    float(p["last_price"]) if p["last_price"] else None,
                    float(p["current_value"]) if p["current_value"] else None,
                    float(p["today_gain_loss"]) if p["today_gain_loss"] else None,
                    float(p["today_gl_pct"]) if p["today_gl_pct"] else None,
                    float(p["total_gain_loss"]) if p["total_gain_loss"] else None,
                    float(p["total_gl_pct"]) if p["total_gl_pct"] else None,
                    float(p["cost_basis_total"]) if p["cost_basis_total"] else None,
                    float(p["avg_cost"]) if p["avg_cost"] else None,
                )
            positions_written = len(pos_rows)
            logger.info("Stored %d position snapshots for account %s", positions_written, account_id)
        except Exception as exc:
            logger.warning("Position snapshot storage failed: %s", exc)

    # If this was an IBKR Activity Statement, mine the Financial Instrument
    # Information section and persist symbol → conid mappings so market_data
    # can look them up without a round-trip to /trsrv/stocks.
    conids_written = 0
    if source_label == "ibkr":
        try:
            conids_written = await persist_ibkr_conids(pool, text)
        except Exception as exc:
            logger.warning("conid persistence failed: %s", exc)

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

    logger.info(
        "Import %d [%s / %s]: %d inserted, %d errors, %d conids",
        import_id, source_label, account_id, success_count, error_count, conids_written,
    )

    if success_count > 0:
        background_tasks.add_task(_run_backfill, pool)

    imported_at = await pool.fetchval(
        "SELECT imported_at FROM fidelity_imports WHERE id=$1", import_id
    )
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
