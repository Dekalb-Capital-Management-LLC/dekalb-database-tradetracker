"""
Portfolio router.

  GET  /portfolio/summary       - combined + per-account P&L
  GET  /portfolio/positions     - current positions with live P&L
  POST /portfolio/refresh-prices - fetch live prices via yfinance, update P&L, write snapshot
  GET  /portfolio/performance   - NAV time series (+ SPY overlay)
  GET  /portfolio/metrics       - beta, sharpe, alpha, max drawdown
  GET  /portfolio/snapshots     - raw daily NAV snapshot rows
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import db
from models.schemas import (
    AccountSummary,
    PerformancePoint,
    PortfolioMetrics,
    PortfolioSnapshotResponse,
    PortfolioSummary,
    PositionSummary,
)
from services import portfolio_metrics

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _compute_positions(pool, account_id: Optional[str] = None) -> list[PositionSummary]:
    """
    Primary path: read from imported_positions (populated by upload + refresh-prices).
    Fallback: compute from trades table with no live prices if no imported_positions exist.
    """
    params = [account_id] if account_id else []
    snap_rows = await pool.fetch(
        f"""
        SELECT ip.symbol, ip.account_id, ip.quantity, ip.last_price,
               ip.current_value, ip.total_gain_loss, ip.total_gl_pct,
               ip.cost_basis_total, ip.avg_cost,
               t.label
        FROM imported_positions ip
        LEFT JOIN LATERAL (
            SELECT MAX(label) AS label FROM trades
            WHERE account_id = ip.account_id AND symbol = ip.symbol
        ) t ON TRUE
        {"WHERE ip.account_id = $1" if account_id else ""}
        ORDER BY ip.symbol
        """,
        *params,
    )

    if snap_rows:
        positions: list[PositionSummary] = []
        for r in snap_rows:
            qty = Decimal(str(r["quantity"])) if r["quantity"] else Decimal("0")
            if qty <= 0:
                continue
            avg_cost = Decimal(str(r["avg_cost"])).quantize(Decimal("0.0001")) if r["avg_cost"] else None
            current_price = Decimal(str(r["last_price"])).quantize(Decimal("0.0001")) if r["last_price"] else None
            market_value = Decimal(str(r["current_value"])).quantize(Decimal("0.01")) if r["current_value"] else None
            unreal = Decimal(str(r["total_gain_loss"])).quantize(Decimal("0.01")) if r["total_gain_loss"] else None
            unreal_pct = Decimal(str(r["total_gl_pct"])).quantize(Decimal("0.0001")) if r["total_gl_pct"] else None
            positions.append(PositionSummary(
                symbol=r["symbol"],
                account_id=r["account_id"],
                quantity=qty,
                avg_cost=avg_cost,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=unreal,
                unrealized_pnl_pct=unreal_pct,
                label=r["label"],
            ))
        return positions

    # Fallback: trades table only, no live prices
    rows = await pool.fetch(
        f"""
        SELECT account_id, symbol,
               SUM(CASE WHEN side='BUY' THEN quantity ELSE -quantity END) AS net_qty,
               SUM(CASE WHEN side='BUY' THEN quantity*price ELSE 0 END) /
                   NULLIF(SUM(CASE WHEN side='BUY' THEN quantity ELSE 0 END), 0) AS avg_cost,
               MAX(label) AS label
        FROM trades
        {"WHERE account_id=$1" if account_id else ""}
        GROUP BY account_id, symbol
        HAVING SUM(CASE WHEN side='BUY' THEN quantity ELSE -quantity END) > 0.00001
        ORDER BY symbol
        """,
        *params,
    )
    positions = []
    for row in rows:
        qty = Decimal(str(row["net_qty"]))
        avg_cost = Decimal(str(row["avg_cost"])) if row["avg_cost"] else None
        positions.append(PositionSummary(
            symbol=row["symbol"],
            account_id=row["account_id"],
            quantity=qty,
            avg_cost=avg_cost,
            current_price=None,
            market_value=None,
            unrealized_pnl=None,
            unrealized_pnl_pct=None,
            label=row["label"],
        ))
    return positions


async def _account_summary(pool, account_id: str) -> AccountSummary:
    snap_total = await pool.fetchrow(
        """
        SELECT SUM(current_value) AS equity_value,
               SUM(total_gain_loss) AS unrealized_pnl
        FROM imported_positions WHERE account_id=$1
        """,
        account_id,
    )

    if snap_total and snap_total["equity_value"] is not None:
        equity_value = Decimal(str(snap_total["equity_value"])).quantize(Decimal("0.01"))
        unrealized_pnl = Decimal(str(snap_total["unrealized_pnl"] or 0)).quantize(Decimal("0.01"))
    else:
        positions = await _compute_positions(pool, account_id)
        equity_value = sum((p.market_value or Decimal(0)) for p in positions)
        unrealized_pnl = sum((p.unrealized_pnl or Decimal(0)) for p in positions)

    # Realized P&L: proceeds from sells minus their proportional cost basis
    realized_row = await pool.fetchrow(
        """
        WITH by_symbol AS (
            SELECT symbol,
                SUM(CASE WHEN side='SELL' THEN quantity   ELSE 0 END) AS sold_qty,
                SUM(CASE WHEN side='SELL' THEN gross_amount ELSE 0 END) AS sell_proceeds,
                NULLIF(SUM(CASE WHEN side='BUY' THEN quantity ELSE 0 END), 0) AS buy_qty,
                SUM(CASE WHEN side='BUY' THEN quantity*price ELSE 0 END) AS buy_cost
            FROM trades WHERE account_id=$1 GROUP BY symbol
            HAVING SUM(CASE WHEN side='SELL' THEN quantity ELSE 0 END) > 0
        )
        SELECT COALESCE(SUM(sell_proceeds - sold_qty * (buy_cost/buy_qty)), 0) AS realized_pnl
        FROM by_symbol WHERE buy_qty IS NOT NULL
        """,
        account_id,
    )
    realized = Decimal(str(realized_row["realized_pnl"])).quantize(Decimal("0.01"))

    source_row = await pool.fetchrow(
        "SELECT source FROM trades WHERE account_id=$1 LIMIT 1", account_id
    )
    snap_row = await pool.fetchrow(
        """
        SELECT total_nav, daily_pnl, daily_pnl_pct FROM portfolio_snapshots
        WHERE account_id=$1 ORDER BY snapshot_date DESC LIMIT 1
        """,
        account_id,
    )

    return AccountSummary(
        account_id=account_id,
        source=source_row["source"] if source_row else "portfolio",
        total_nav=Decimal(str(snap_row["total_nav"])) if snap_row else None,
        cash_balance=None,
        equity_value=Decimal(str(equity_value)).quantize(Decimal("0.01")),
        day_pnl=Decimal(str(snap_row["daily_pnl"])) if snap_row and snap_row["daily_pnl"] else None,
        day_pnl_pct=Decimal(str(snap_row["daily_pnl_pct"])) if snap_row and snap_row["daily_pnl_pct"] else None,
        total_realized_pnl=realized,
        total_unrealized_pnl=Decimal(str(unrealized_pnl)).quantize(Decimal("0.01")),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(pool=Depends(get_pool)):
    try:
        account_rows = await pool.fetch(
            "SELECT DISTINCT account_id FROM trades ORDER BY account_id"
        )
        account_ids = [r["account_id"] for r in account_rows]

        accounts = [await _account_summary(pool, a) for a in account_ids]
        positions = await _compute_positions(pool)

        combined_equity = sum((a.equity_value or Decimal(0)) for a in accounts)
        combined_unrealized = sum((a.total_unrealized_pnl or Decimal(0)) for a in accounts)
        combined_realized = sum((a.total_realized_pnl or Decimal(0)) for a in accounts)
        combined_day_pnl = sum((a.day_pnl or Decimal(0)) for a in accounts)

        snap_row = await pool.fetchrow(
            """
            SELECT total_nav, daily_pnl_pct FROM portfolio_snapshots
            WHERE account_id IS NULL ORDER BY snapshot_date DESC LIMIT 1
            """
        )

        return PortfolioSummary(
            accounts=accounts,
            combined_nav=Decimal(str(snap_row["total_nav"])) if snap_row else None,
            combined_equity_value=combined_equity.quantize(Decimal("0.01")),
            combined_day_pnl=combined_day_pnl.quantize(Decimal("0.01")) if combined_day_pnl else None,
            combined_day_pnl_pct=Decimal(str(snap_row["daily_pnl_pct"])) if snap_row and snap_row["daily_pnl_pct"] else None,
            total_realized_pnl=combined_realized.quantize(Decimal("0.01")),
            total_unrealized_pnl=combined_unrealized.quantize(Decimal("0.01")),
            positions=positions,
            as_of=datetime.utcnow(),
        )
    except Exception as exc:
        logger.error("portfolio summary error: %s", exc)
        raise HTTPException(status_code=500, detail="Error computing portfolio summary")


@router.get("/positions", response_model=list[PositionSummary])
async def get_positions(
    account_id: Optional[str] = Query(None),
    pool=Depends(get_pool),
):
    try:
        return await _compute_positions(pool, account_id)
    except Exception as exc:
        logger.error("positions error: %s", exc)
        raise HTTPException(status_code=500, detail="Error computing positions")


@router.post("/refresh-prices")
async def refresh_prices(pool=Depends(get_pool)):
    """
    Fetch live prices from Yahoo Finance for every symbol in imported_positions.
    Recomputes current_value and total_gain_loss = (price - avg_cost) * qty.
    Then writes a portfolio_snapshot for today.
    """
    import yfinance as yf

    rows = await pool.fetch(
        "SELECT account_id, symbol, quantity, avg_cost, cost_basis_total FROM imported_positions"
    )
    if not rows:
        return {"updated": 0, "message": "No positions — import a file first"}

    symbols = list({r["symbol"] for r in rows})
    prices: dict[str, float] = {}
    errors: list[str] = []

    for sym in symbols:
        try:
            info = yf.Ticker(sym).info
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if price:
                prices[sym] = float(price)
        except Exception as exc:
            errors.append(f"{sym}: {exc}")

    updated = 0
    for r in rows:
        sym = r["symbol"]
        price = prices.get(sym)
        if price is None:
            continue
        qty = float(r["quantity"] or 0)
        cost_basis = float(r["cost_basis_total"] or 0)
        current_value = qty * price
        total_gl = current_value - cost_basis
        total_gl_pct = (total_gl / cost_basis * 100) if cost_basis else None
        await pool.execute(
            """
            UPDATE imported_positions
            SET last_price=$1, current_value=$2, total_gain_loss=$3,
                total_gl_pct=$4, snapshot_date=CURRENT_DATE, updated_at=NOW()
            WHERE account_id=$5 AND symbol=$6
            """,
            price, current_value, total_gl, total_gl_pct,
            r["account_id"], sym,
        )
        updated += 1

    # Auto-write a snapshot so performance graph works
    if updated > 0:
        try:
            from services.portfolio_metrics import upsert_snapshot
            account_rows = await pool.fetch(
                "SELECT DISTINCT account_id FROM imported_positions"
            )
            combined_nav = Decimal(0)
            today = date.today()
            for ar in account_rows:
                acct_id = ar["account_id"]
                snap_total = await pool.fetchrow(
                    "SELECT SUM(current_value) AS nav FROM imported_positions WHERE account_id=$1",
                    acct_id,
                )
                nav = Decimal(str(snap_total["nav"] or 0))
                prev = await pool.fetchrow(
                    """
                    SELECT total_nav FROM portfolio_snapshots
                    WHERE account_id=$1 AND snapshot_date < $2
                    ORDER BY snapshot_date DESC LIMIT 1
                    """,
                    acct_id, today,
                )
                prev_nav = Decimal(str(prev["total_nav"])) if prev else None
                await upsert_snapshot(pool, today, nav, acct_id, nav, prev_nav)
                combined_nav += nav

            prev_comb = await pool.fetchrow(
                """
                SELECT total_nav FROM portfolio_snapshots
                WHERE account_id IS NULL AND snapshot_date < $1
                ORDER BY snapshot_date DESC LIMIT 1
                """,
                today,
            )
            await upsert_snapshot(pool, today, combined_nav, None, combined_nav,
                                  Decimal(str(prev_comb["total_nav"])) if prev_comb else None)
        except Exception as exc:
            logger.warning("Auto-snapshot after refresh-prices failed: %s", exc)

    logger.info("refresh-prices: %d/%d symbols updated, %d errors", updated, len(symbols), len(errors))
    return {"updated": updated, "total_symbols": len(symbols), "prices_found": len(prices), "errors": errors[:10]}


@router.get("/performance", response_model=list[PerformancePoint])
async def get_performance(
    period: str = Query("ytd"),
    account_id: Optional[str] = Query(None),
    pool=Depends(get_pool),
):
    from services.portfolio_metrics import _period_bounds, get_performance_series
    start, end = _period_bounds(period)
    try:
        return await get_performance_series(pool, start, end, account_id)
    except Exception as exc:
        logger.error("performance series error: %s", exc)
        raise HTTPException(status_code=500, detail="Error computing performance series")


@router.get("/metrics", response_model=PortfolioMetrics)
async def get_metrics(
    period: str = Query("ytd"),
    account_id: Optional[str] = Query(None),
    pool=Depends(get_pool),
):
    try:
        return await portfolio_metrics.calculate_metrics(pool, period, account_id)
    except Exception as exc:
        logger.error("metrics error: %s", exc)
        raise HTTPException(status_code=500, detail="Error computing portfolio metrics")


@router.get("/snapshots", response_model=list[PortfolioSnapshotResponse])
async def get_snapshots(
    account_id: Optional[str] = Query(None),
    limit: int = Query(365, ge=1, le=3650),
    pool=Depends(get_pool),
):
    try:
        if account_id:
            rows = await pool.fetch(
                "SELECT * FROM portfolio_snapshots WHERE account_id=$1 ORDER BY snapshot_date DESC LIMIT $2",
                account_id, limit,
            )
        else:
            rows = await pool.fetch(
                "SELECT * FROM portfolio_snapshots WHERE account_id IS NULL ORDER BY snapshot_date DESC LIMIT $1",
                limit,
            )
        return [dict(r) for r in rows]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Error fetching snapshots")
