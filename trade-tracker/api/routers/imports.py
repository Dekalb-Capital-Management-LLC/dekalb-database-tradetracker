"""
Import router.

  POST /import/trades   — upload .xlsx portfolio file (multi-sheet: Ticker | Date | Amount | Price)
  GET  /import/history  — list past imports
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

import db
from models.schemas import (
    FidelityImportResponse,
    ImportCommitPosition,
    ImportCommitRequest,
    ImportPreviewResponse,
    LatestImportSummary,
    PositionDiffRow,
)
from services.fidelity_parser import parse_fidelity_csv
from services.universal_parser import parse_portfolio_xlsx

router = APIRouter(prefix="/import", tags=["imports"])
logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = ("csv", "xlsx", "xlsm", "xls", "tsv", "txt")

# Short-lived server-side staging for the preview -> commit wizard flow.
# Avoids round-tripping the full parsed trade list (with raw_data blobs)
# through the client just to send it straight back on commit.
_PREVIEW_TTL_SECONDS = 1800
_preview_cache: dict[str, dict] = {}


def _stage_preview(filename: str, raw_bytes: bytes, trades: list, account_ids: list[str]) -> str:
    now = time.time()
    for key, entry in list(_preview_cache.items()):
        if entry["expires_at"] < now:
            del _preview_cache[key]
    preview_id = uuid.uuid4().hex
    _preview_cache[preview_id] = {
        "expires_at": now + _PREVIEW_TTL_SECONDS,
        "filename": filename,
        "raw_bytes": raw_bytes,
        "trades": trades,
        "account_ids": account_ids,
    }
    return preview_id


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
        VALUES ($1, 'PORTFOLIO', $2, 'pending', 'fidelity')
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
            VALUES ($1,'PORTFOLIO',$2,$3,$4,$5,'fidelity',CURRENT_DATE,NOW())
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


@router.post("/preview", response_model=ImportPreviewResponse)
async def preview_import(
    file: UploadFile = File(...),
    account_id: str = Form(""),
    pool=Depends(get_pool),
):
    """
    Parse a .xlsx (custom Ticker/Date/Amount/Price sheet) or .csv (real
    Fidelity Positions export) file and diff it against the currently cached
    positions, without writing anything to the database. The frontend shows
    this diff for review/edits, then calls /import/commit with the returned
    preview_id to actually apply it.

    For .xlsx, account_id is required (the sheet has no account info of its
    own). For .csv, account_id is an optional override — Fidelity's export
    carries its own per-row Account Name/Number, including across multiple
    accounts in one file, which we use unless the caller forces one account.
    """
    fname = file.filename or "upload"
    is_csv = fname.lower().endswith(".csv")
    is_xlsx = fname.lower().endswith((".xlsx", ".xlsm"))
    if not (is_csv or is_xlsx):
        raise HTTPException(status_code=400, detail="Only .csv (Fidelity export) or .xlsx / .xlsm files are supported")

    override_acct = (account_id or "").strip().upper() or None
    raw_bytes = await file.read()

    try:
        if is_csv:
            csv_text = raw_bytes.decode("utf-8-sig")
            trades, errors, _ = parse_fidelity_csv(csv_text, account_id=override_acct, import_id=0)
            if override_acct:
                for t in trades:
                    t.account_id = override_acct
        else:
            acct = override_acct or "PORTFOLIO"
            trades, errors = parse_portfolio_xlsx(raw_bytes, import_id=0, account_id=acct)
    except Exception as exc:
        logger.exception("Preview parse failed for %s", fname)
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")

    new_agg: dict[tuple[str, str], dict] = {}
    for t in trades:
        key = (t.account_id, t.symbol)
        if key not in new_agg:
            new_agg[key] = {"qty": 0.0, "cost": 0.0}
        new_agg[key]["qty"] += float(t.quantity)
        new_agg[key]["cost"] += float(t.quantity) * float(t.price)

    account_ids = sorted({t.account_id for t in trades}) or ([override_acct] if override_acct else [])
    old_rows = await pool.fetch(
        "SELECT account_id, symbol, quantity, avg_cost FROM imported_positions WHERE account_id = ANY($1)",
        account_ids,
    ) if account_ids else []
    old_qty = {(r["account_id"], r["symbol"]): float(r["quantity"] or 0) for r in old_rows}

    diff: list[PositionDiffRow] = []
    positions: list[ImportCommitPosition] = []
    for key in sorted(set(new_agg) | set(old_qty)):
        acct, sym = key
        prev_qty = old_qty.get(key, 0.0)
        if key in new_agg and new_agg[key]["qty"] > 0:
            new_qty = new_agg[key]["qty"]
            avg_cost = new_agg[key]["cost"] / new_qty
            positions.append(ImportCommitPosition(account_id=acct, symbol=sym, quantity=new_qty, avg_cost=avg_cost))
        else:
            new_qty = 0.0
            avg_cost = 0.0
        if prev_qty == new_qty:
            continue
        diff.append(PositionDiffRow(
            account_id=acct, symbol=sym, old_quantity=prev_qty, new_quantity=new_qty,
            delta=new_qty - prev_qty, avg_cost=avg_cost,
        ))

    preview_id = _stage_preview(fname, raw_bytes, trades, account_ids)
    return ImportPreviewResponse(
        preview_id=preview_id, account_ids=account_ids, filename=fname,
        diff=diff, positions=positions, errors=errors,
    )


@router.post("/commit", response_model=FidelityImportResponse)
async def commit_import(
    body: ImportCommitRequest,
    background_tasks: BackgroundTasks,
    pool=Depends(get_pool),
):
    """Apply a previously-previewed import (with any user edits to quantities)."""
    staged = _preview_cache.get(body.preview_id)
    if staged is None or staged["expires_at"] < time.time():
        _preview_cache.pop(body.preview_id, None)
        raise HTTPException(status_code=404, detail="Preview expired — please re-upload the file")

    fname = staged["filename"]
    trades = staged["trades"]
    account_ids = staged["account_ids"]
    raw_b64 = base64.b64encode(staged["raw_bytes"]).decode("ascii")
    source = trades[0].source if trades else "fidelity"
    # account_id on the audit row is informational only; leave NULL when the
    # file spans multiple accounts rather than picking one arbitrarily.
    primary_account = account_ids[0] if len(account_ids) == 1 else None

    import_id = await pool.fetchval(
        """
        INSERT INTO fidelity_imports (filename, account_id, raw_csv, status, source)
        VALUES ($1, $2, $3, 'pending', $4)
        RETURNING id
        """,
        fname, primary_account, raw_b64, source,
    )

    success = 0
    errors: list[str] = []
    for trade in trades:
        existing = await pool.fetchval(
            """
            SELECT id FROM trades
            WHERE source=$1 AND account_id=$2 AND symbol=$3
              AND ABS(quantity - $4) < 0.0001 AND ABS(price - $5) < 0.0001
              AND trade_date::date = $6::date
            LIMIT 1
            """,
            trade.source, trade.account_id, trade.symbol, float(trade.quantity),
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
                import_id,
            )
            success += 1
        except Exception as exc:
            errors.append(f"{trade.symbol}: {exc}")

    if account_ids:
        await pool.execute("DELETE FROM imported_positions WHERE account_id = ANY($1)", account_ids)
    for pos in body.positions:
        if pos.quantity <= 0:
            continue
        cost_basis = pos.quantity * pos.avg_cost
        await pool.execute(
            """
            INSERT INTO imported_positions
                (import_id, account_id, symbol, quantity, avg_cost,
                 cost_basis_total, source, snapshot_date, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,CURRENT_DATE,NOW())
            ON CONFLICT (account_id, symbol) DO UPDATE SET
                import_id        = EXCLUDED.import_id,
                quantity         = EXCLUDED.quantity,
                avg_cost         = EXCLUDED.avg_cost,
                cost_basis_total = EXCLUDED.cost_basis_total,
                snapshot_date    = EXCLUDED.snapshot_date,
                updated_at       = NOW()
            """,
            import_id, pos.account_id, pos.symbol, pos.quantity, pos.avg_cost, cost_basis, source,
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
        SET status=$1, row_count=$2, success_count=$3, error_count=$4, error_message=$5
        WHERE id=$6
        """,
        status, len(trades), success, error_count, error_msg, import_id,
    )

    logger.info("Commit %d (%s): %d inserted, %d errors, %d positions",
                import_id, ", ".join(account_ids) or "—", success, error_count, len(body.positions))

    if success > 0:
        background_tasks.add_task(_run_backfill, pool)

    _preview_cache.pop(body.preview_id, None)

    imported_at = await pool.fetchval(
        "SELECT imported_at FROM fidelity_imports WHERE id=$1", import_id
    )
    return FidelityImportResponse(
        import_id=import_id,
        filename=fname,
        account_id=primary_account,
        status=status,
        row_count=len(trades),
        success_count=success,
        error_count=error_count,
        error_message=error_msg,
        imported_at=imported_at,
    )


@router.delete("/positions")
async def delete_positions(account_id: str | None = None, pool=Depends(get_pool)):
    """Clear cached positions — for one account, or all accounts if omitted."""
    if account_id:
        rows = await pool.fetch(
            "DELETE FROM imported_positions WHERE account_id=$1 RETURNING symbol", account_id
        )
    else:
        rows = await pool.fetch("DELETE FROM imported_positions RETURNING symbol")
    logger.info("Deleted %d cached position(s) (account_id=%s)", len(rows), account_id or "ALL")
    return {"account_id": account_id, "deleted": len(rows)}


@router.get("/latest", response_model=LatestImportSummary)
async def latest_import(account_id: str | None = None, pool=Depends(get_pool)):
    """Last-known cached state, so the UI can show 'Last updated X · N positions'
    instead of defaulting to an empty upload prompt."""
    if account_id:
        row = await pool.fetchrow(
            "SELECT account_id, filename, imported_at FROM fidelity_imports "
            "WHERE account_id=$1 ORDER BY imported_at DESC LIMIT 1",
            account_id,
        )
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM imported_positions WHERE account_id=$1", account_id
        )
    else:
        row = await pool.fetchrow(
            "SELECT account_id, filename, imported_at FROM fidelity_imports "
            "ORDER BY imported_at DESC LIMIT 1"
        )
        count = await pool.fetchval("SELECT COUNT(*) FROM imported_positions")

    return LatestImportSummary(
        account_id=row["account_id"] if row else None,
        filename=row["filename"] if row else None,
        imported_at=row["imported_at"] if row else None,
        position_count=count or 0,
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
