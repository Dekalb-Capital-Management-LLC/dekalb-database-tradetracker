"""
Trade log router.

Endpoints:
  GET  /trades                  - paginated trade list with filters
  GET  /trades/{id}             - single trade detail
  PATCH /trades/{id}/label      - assign label + hedge flag
  DELETE /trades/reset          - wipe all trades + snapshots (use before switching accounts)
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

import db
from models.schemas import TradeLabelUpdate, TradeResponse

router = APIRouter(prefix="/trades", tags=["trades"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


@router.delete("/reset", summary="Wipe all trades and portfolio snapshots")
async def reset_all_data(pool=Depends(get_pool)):
    """
    Deletes all rows from trades and portfolio_snapshots.
    Use this when switching from a paper/test account to live.
    """
    try:
        await pool.execute("DELETE FROM trades")
        await pool.execute("DELETE FROM portfolio_snapshots")
        await pool.execute("DELETE FROM cash_flows")
        await pool.execute("DELETE FROM imported_positions")
        await pool.execute("DELETE FROM fidelity_imports")
        logger.info("Data reset: all trades and snapshots cleared")
        return {"message": "All trades, snapshots, cash flows, imports, and positions deleted.", "trades_remaining": 0}
    except Exception as exc:
        logger.error("reset error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Reset failed: {exc}")


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    account_id: Optional[str] = Query(None, description="Filter by account"),
    symbol: Optional[str] = Query(None, description="Filter by ticker symbol"),
    source: Optional[str] = Query(None, description="'ibkr' or 'fidelity'"),
    label: Optional[str] = Query(None, description="Filter by trade label"),
    side: Optional[str] = Query(None, description="'BUY' or 'SELL'"),
    start_date: Optional[date] = Query(None, description="Inclusive start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="Inclusive end date (YYYY-MM-DD)"),
    is_hedge: Optional[bool] = Query(None, description="Filter hedges only"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    pool=Depends(get_pool),
):
    """
    List trades with optional filters. Sorted newest first.
    """
    conditions = []
    params: list = []
    idx = 1

    if account_id:
        conditions.append(f"account_id = ${idx}")
        params.append(account_id)
        idx += 1
    if symbol:
        conditions.append(f"symbol = ${idx}")
        params.append(symbol.upper())
        idx += 1
    if source:
        conditions.append(f"source = ${idx}")
        params.append(source.lower())
        idx += 1
    if label:
        conditions.append(f"label = ${idx}")
        params.append(label)
        idx += 1
    if side:
        conditions.append(f"side = ${idx}")
        params.append(side.upper())
        idx += 1
    if is_hedge is not None:
        conditions.append(f"is_hedge = ${idx}")
        params.append(is_hedge)
        idx += 1
    if start_date:
        conditions.append(f"trade_date >= ${idx}")
        params.append(start_date)
        idx += 1
    if end_date:
        conditions.append(f"trade_date <= ${idx}::date + interval '1 day'")
        params.append(end_date)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    sql = f"""
        SELECT id, source, account_id, trade_date, symbol, side,
               quantity, price, commission, gross_amount, net_amount,
               label, is_hedge, notes, ibkr_order_id, fidelity_import_id,
               created_at, updated_at
        FROM trades
        {where}
        ORDER BY trade_date DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """

    try:
        rows = await pool.fetch(sql, *params)
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("list_trades error: %s", exc)
        raise HTTPException(status_code=500, detail="Database error fetching trades")


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(trade_id: int, pool=Depends(get_pool)):
    row = await pool.fetchrow(
        """
        SELECT id, source, account_id, trade_date, symbol, side,
               quantity, price, commission, gross_amount, net_amount,
               label, is_hedge, notes, ibkr_order_id, fidelity_import_id,
               created_at, updated_at
        FROM trades WHERE id = $1
        """,
        trade_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return dict(row)


@router.patch("/{trade_id}/label", response_model=TradeResponse)
async def update_trade_label(
    trade_id: int,
    body: TradeLabelUpdate,
    pool=Depends(get_pool),
):
    """
    Assign or update a trade's label (event-driven, hedge, long-term, short-term).
    Also allows setting is_hedge and adding notes.
    """
    updates: dict = {"label": body.label}
    if body.is_hedge is not None:
        updates["is_hedge"] = body.is_hedge
    if body.notes is not None:
        updates["notes"] = body.notes

    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    values = list(updates.values())

    sql = f"""
        UPDATE trades
        SET {set_clauses}
        WHERE id = $1
        RETURNING id, source, account_id, trade_date, symbol, side,
                  quantity, price, commission, gross_amount, net_amount,
                  label, is_hedge, notes, ibkr_order_id, fidelity_import_id,
                  created_at, updated_at
    """

    try:
        row = await pool.fetchrow(sql, trade_id, *values)
    except Exception as exc:
        logger.error("update_trade_label error: %s", exc)
        raise HTTPException(status_code=500, detail="Database error updating trade")

    if not row:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")

    logger.info("Trade %d labelled as '%s'", trade_id, body.label)
    return dict(row)
