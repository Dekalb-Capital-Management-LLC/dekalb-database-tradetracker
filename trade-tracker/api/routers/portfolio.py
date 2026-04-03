"""
Portfolio router.

Endpoints:
  GET /portfolio/summary      - combined + per-account P&L snapshot
  GET /portfolio/positions    - current open positions with live P&L
  GET /portfolio/performance  - NAV time series for performance graph (+ SPY overlay)
  GET /portfolio/metrics      - beta, std dev, sharpe, alpha, max drawdown
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
from services import market_data, portfolio_metrics

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _compute_positions(pool, account_id: Optional[str] = None) -> list[PositionSummary]:
    """
    Derive current positions from the trades table using FIFO-style quantity netting.
    BUY adds quantity, SELL subtracts.
    Also calculates avg_cost as weighted average of buy fills.
    """
    condition = "AND account_id = $1" if account_id else ""
    params = [account_id] if account_id else []

    rows = await pool.fetch(
        f"""
        SELECT
            account_id,
            symbol,
            SUM(CASE WHEN side = 'BUY'  THEN quantity ELSE -quantity END) AS net_quantity,
            SUM(CASE WHEN side = 'BUY'  THEN quantity * price ELSE 0 END) /
                NULLIF(SUM(CASE WHEN side = 'BUY' THEN quantity ELSE 0 END), 0) AS avg_cost,
            MAX(label) AS label
        FROM trades
        {"WHERE account_id = $1" if account_id else ""}
        GROUP BY account_id, symbol
        HAVING SUM(CASE WHEN side = 'BUY' THEN quantity ELSE -quantity END) > 0.00001
        ORDER BY symbol
        """,
        *params,
    )

    positions: list[PositionSummary] = []
    for row in rows:
        symbol = row["symbol"]
        qty = Decimal(str(row["net_quantity"]))
        avg_cost = Decimal(str(row["avg_cost"])) if row["avg_cost"] else None

        # Fetch current price (cached by market_data service)
        quote = market_data.get_quote(symbol)
        current_price = quote.price if quote else None

        market_value = (qty * current_price).quantize(Decimal("0.01")) if current_price else None
        cost_basis = (qty * avg_cost).quantize(Decimal("0.01")) if avg_cost else None

        unrealized_pnl = None
        unrealized_pnl_pct = None
        if market_value is not None and cost_basis is not None and cost_basis != 0:
            unrealized_pnl = (market_value - cost_basis).quantize(Decimal("0.01"))
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100).quantize(Decimal("0.0001"))

        positions.append(
            PositionSummary(
                symbol=symbol,
                account_id=row["account_id"],
                quantity=qty,
                avg_cost=avg_cost,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
                label=row["label"],
            )
        )
    return positions


async def _account_summary(pool, account_id: str) -> AccountSummary:
    # Realized P&L: for each symbol, (sell proceeds) - (avg_cost × sold_qty).
    # Only counts shares that have actually been sold.
    # avg_cost is weighted average of all buy fills for that symbol.
    realized_row = await pool.fetchrow(
        """
        WITH by_symbol AS (
            SELECT
                symbol,
                SUM(CASE WHEN side = 'SELL' THEN net_amount  ELSE 0 END) AS sell_proceeds,
                SUM(CASE WHEN side = 'SELL' THEN quantity     ELSE 0 END) AS sold_qty,
                NULLIF(SUM(CASE WHEN side = 'BUY' THEN quantity ELSE 0 END), 0) AS total_buy_qty,
                SUM(CASE WHEN side = 'BUY' THEN gross_amount + commission ELSE 0 END) AS total_buy_cost
            FROM trades
            WHERE account_id = $1
            GROUP BY symbol
            HAVING SUM(CASE WHEN side = 'SELL' THEN quantity ELSE 0 END) > 0
        )
        SELECT COALESCE(
            SUM(sell_proceeds - sold_qty * (total_buy_cost / total_buy_qty)),
            0
        ) AS realized_pnl
        FROM by_symbol
        WHERE total_buy_qty IS NOT NULL
        """,
        account_id,
    )

    source_row = await pool.fetchrow(
        "SELECT source FROM trades WHERE account_id = $1 LIMIT 1",
        account_id,
    )

    positions = await _compute_positions(pool, account_id)
    equity_value = sum((p.market_value or Decimal(0)) for p in positions)
    unrealized_pnl = sum((p.unrealized_pnl or Decimal(0)) for p in positions)

    # Latest snapshot for today's P&L
    snap_row = await pool.fetchrow(
        """
        SELECT total_nav, daily_pnl, daily_pnl_pct
        FROM portfolio_snapshots
        WHERE account_id = $1
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        account_id,
    )

    return AccountSummary(
        account_id=account_id,
        source=source_row["source"] if source_row else "ibkr",
        total_nav=Decimal(str(snap_row["total_nav"])) if snap_row else None,
        cash_balance=None,  # requires IBKR gateway or manual entry
        equity_value=Decimal(str(equity_value)).quantize(Decimal("0.01")),
        day_pnl=Decimal(str(snap_row["daily_pnl"])) if snap_row and snap_row["daily_pnl"] else None,
        day_pnl_pct=Decimal(str(snap_row["daily_pnl_pct"])) if snap_row and snap_row["daily_pnl_pct"] else None,
        total_realized_pnl=Decimal(str(realized_row["realized_pnl"])).quantize(Decimal("0.01")),
        total_unrealized_pnl=Decimal(str(unrealized_pnl)).quantize(Decimal("0.01")),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(pool=Depends(get_pool)):
    """
    Combined portfolio overview across all accounts.
    Shows per-account breakdown + totals.
    """
    try:
        account_rows = await pool.fetch(
            "SELECT DISTINCT account_id FROM trades ORDER BY account_id"
        )
        account_ids = [r["account_id"] for r in account_rows]

        accounts = []
        for acct_id in account_ids:
            accounts.append(await _account_summary(pool, acct_id))

        positions = await _compute_positions(pool)

        combined_equity = sum(((a.equity_value or Decimal(0)) for a in accounts), Decimal(0))
        combined_unrealized = sum(((a.total_unrealized_pnl or Decimal(0)) for a in accounts), Decimal(0))
        combined_realized = sum(((a.total_realized_pnl or Decimal(0)) for a in accounts), Decimal(0))
        combined_day_pnl = sum(((a.day_pnl or Decimal(0)) for a in accounts), Decimal(0))

        # Latest combined NAV snapshot
        snap_row = await pool.fetchrow(
            """
            SELECT total_nav, daily_pnl_pct
            FROM portfolio_snapshots
            WHERE account_id IS NULL
            ORDER BY snapshot_date DESC
            LIMIT 1
            """
        )

        combined_nav = Decimal(str(snap_row["total_nav"])) if snap_row else None
        combined_day_pnl_pct = Decimal(str(snap_row["daily_pnl_pct"])) if snap_row and snap_row["daily_pnl_pct"] else None

        return PortfolioSummary(
            accounts=accounts,
            combined_nav=combined_nav,
            combined_equity_value=combined_equity.quantize(Decimal("0.01")),
            combined_day_pnl=combined_day_pnl.quantize(Decimal("0.01")) if combined_day_pnl else None,
            combined_day_pnl_pct=combined_day_pnl_pct,
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
    account_id: Optional[str] = Query(None, description="Filter by account"),
    pool=Depends(get_pool),
):
    """
    Current open positions with live P&L.
    Quantities are netted from trade history (BUY - SELL).
    Prices fetched from yfinance (or IBKR if gateway enabled).
    """
    try:
        return await _compute_positions(pool, account_id)
    except Exception as exc:
        logger.error("positions error: %s", exc)
        raise HTTPException(status_code=500, detail="Error computing positions")


@router.get("/performance", response_model=list[PerformancePoint])
async def get_performance(
    period: str = Query("ytd", description="ytd | 1y | 6m | 3m | 1m"),
    account_id: Optional[str] = Query(None, description="Filter by account (None = combined)"),
    pool=Depends(get_pool),
):
    """
    NAV time series for performance graph, including SPY overlay data.
    Frontend can use this to draw portfolio vs SPY lines.
    """
    from services.portfolio_metrics import _period_bounds, get_performance_series
    start, end = _period_bounds(period)
    try:
        return await get_performance_series(pool, start, end, account_id)
    except Exception as exc:
        logger.error("performance series error: %s", exc)
        raise HTTPException(status_code=500, detail="Error computing performance series")


@router.get("/metrics", response_model=PortfolioMetrics)
async def get_metrics(
    period: str = Query("ytd", description="ytd | 1y | 6m | 3m | 1m"),
    account_id: Optional[str] = Query(None, description="Filter by account (None = combined)"),
    pool=Depends(get_pool),
):
    """
    Quantitative portfolio metrics:
    - Beta vs SPY
    - Annualized standard deviation
    - Sharpe ratio
    - Alpha
    - Max drawdown
    - Win rate (% of SELL trades profitable)
    All calculated over the requested period using stored daily NAV snapshots.
    """
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
    """Raw daily NAV snapshots. Useful for debugging metric calculations."""
    try:
        if account_id:
            rows = await pool.fetch(
                """
                SELECT * FROM portfolio_snapshots
                WHERE account_id = $1
                ORDER BY snapshot_date DESC LIMIT $2
                """,
                account_id, limit,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT * FROM portfolio_snapshots
                WHERE account_id IS NULL
                ORDER BY snapshot_date DESC LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("snapshots error: %s", exc)
        raise HTTPException(status_code=500, detail="Error fetching snapshots")


@router.post("/snapshots/backfill", tags=["portfolio"])
async def backfill_snapshots(
    start_date: Optional[date] = Query(None, description="Start date (default: first trade)"),
    end_date: Optional[date] = Query(None, description="End date (default: today)"),
    pool=Depends(get_pool),
):
    """
    Generate (or regenerate) daily NAV snapshots across the full trade history.

    This powers the performance graph and all portfolio metrics.
    Run once after importing trades, then daily snapshots update automatically.
    Uses yfinance for historical close prices — takes ~10-30 seconds for large histories.
    Safe to re-run: existing rows are overwritten with latest data.
    """
    try:
        result = await portfolio_metrics.backfill_snapshots(pool, start_date, end_date)
        return result
    except Exception as exc:
        logger.error("backfill error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Backfill failed: {exc}")


@router.post("/snapshots/generate", tags=["portfolio"])
async def generate_snapshot(
    snapshot_date: Optional[date] = Query(None, description="Date to generate for (default: today)"),
    pool=Depends(get_pool),
):
    """
    Compute and store a NAV snapshot for the given date (default: today).

    This endpoint powers all portfolio metrics and performance graphs.
    Run it once per trading day (e.g. via a nightly cron) to keep the
    performance history up to date.

    What it does:
    - Derives each account's equity value from current positions (weighted avg cost x live price)
    - Fetches SPY close for the date (for benchmark overlay)
    - Writes one row per account + one combined row to portfolio_snapshots
    - Subsequent calls for the same date UPSERT (safe to re-run)
    """
    from services.portfolio_metrics import upsert_snapshot

    target_date = snapshot_date or date.today()

    try:
        account_rows = await pool.fetch(
            "SELECT DISTINCT account_id FROM trades ORDER BY account_id"
        )
        account_ids = [r["account_id"] for r in account_rows]

        if not account_ids:
            raise HTTPException(status_code=422, detail="No trades found — import trades first before generating snapshots")

        generated = []
        combined_equity = Decimal(0)
        combined_nav = Decimal(0)

        for acct_id in account_ids:
            # Previous snapshot for daily P&L calc
            prev_snap = await pool.fetchrow(
                """
                SELECT total_nav FROM portfolio_snapshots
                WHERE account_id = $1 AND snapshot_date < $2
                ORDER BY snapshot_date DESC LIMIT 1
                """,
                acct_id, target_date,
            )
            prev_nav = Decimal(str(prev_snap["total_nav"])) if prev_snap else None

            # Derive current equity from position quantities x live prices
            positions = await _compute_positions(pool, acct_id)
            equity = sum((p.market_value or Decimal(0)) for p in positions)

            # For NAV: equity + (cash is unknown unless IBKR gateway is on, so use equity as proxy)
            nav = equity

            await upsert_snapshot(
                pool=pool,
                snapshot_date=target_date,
                total_nav=nav,
                account_id=acct_id,
                equity_value=equity,
                prev_nav=prev_nav,
            )
            combined_equity += equity
            combined_nav += nav
            generated.append(acct_id)

        # Combined portfolio snapshot (account_id = None)
        prev_combined = await pool.fetchrow(
            """
            SELECT total_nav FROM portfolio_snapshots
            WHERE account_id IS NULL AND snapshot_date < $1
            ORDER BY snapshot_date DESC LIMIT 1
            """,
            target_date,
        )
        prev_combined_nav = Decimal(str(prev_combined["total_nav"])) if prev_combined else None

        await upsert_snapshot(
            pool=pool,
            snapshot_date=target_date,
            total_nav=combined_nav,
            account_id=None,
            equity_value=combined_equity,
            prev_nav=prev_combined_nav,
        )

        logger.info("Generated snapshots for %s: accounts=%s", target_date, generated)
        return {
            "snapshot_date": target_date.isoformat(),
            "accounts_processed": generated,
            "combined_nav": float(combined_nav),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("snapshot generation error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error generating snapshot: {exc}")
