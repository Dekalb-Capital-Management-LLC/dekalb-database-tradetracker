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
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import config
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


def _dec(v) -> Decimal:
    """Coerce to Decimal — sum() on empty iterables returns int 0."""
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _extract_summary_amount(summary: dict, field: str) -> Optional[Decimal]:
    entry = summary.get(field, {})
    if isinstance(entry, dict) and entry.get("amount") is not None:
        return Decimal(str(entry["amount"]))
    return None


def _ibkr_positions(account_id: Optional[str] = None) -> list[PositionSummary]:
    """Live positions from IBKR — prices and P&L included in API response."""
    from services.ibkr_client import ibkr_client

    acct = account_id or config.IBKR_ACCOUNT_ID
    if not config.IBKR_ENABLED or not acct or not ibkr_client.is_connected:
        return []

    positions: list[PositionSummary] = []
    for p in ibkr_client.live_positions(acct):
        mkt_val = Decimal(str(p["market_value"]))
        upnl = Decimal(str(p["unrealized_pnl"]))
        cost = Decimal(str(p["cost_basis"]))
        upnl_pct = (upnl / cost * 100).quantize(Decimal("0.0001")) if cost else None
        positions.append(
            PositionSummary(
                symbol=p["symbol"],
                account_id=acct,
                quantity=Decimal(str(p["quantity"])),
                avg_cost=Decimal(str(p["avg_cost"])),
                current_price=Decimal(str(p["market_price"])),
                market_value=mkt_val,
                unrealized_pnl=upnl,
                unrealized_pnl_pct=upnl_pct,
                label=None,
            )
        )
    return positions


async def _ibkr_portfolio_summary(pool) -> PortfolioSummary:
    """Build summary from live IBKR when trades table is empty."""
    from services.ibkr_client import ibkr_client

    acct = config.IBKR_ACCOUNT_ID
    positions = _ibkr_positions(acct)
    raw = ibkr_client.get_account_summary(acct) or {}

    equity = _extract_summary_amount(raw, "equitywithloanvalue") or sum(
        (p.market_value or Decimal(0) for p in positions), Decimal(0)
    )
    nav = _extract_summary_amount(raw, "netliquidation") or equity
    cash = _extract_summary_amount(raw, "totalcashvalue")
    unrealized = sum((p.unrealized_pnl or Decimal(0) for p in positions), Decimal(0))
    realized = _ibkr_realized_pnl(acct) or Decimal(0)

    account = AccountSummary(
        account_id=acct,
        source="ibkr",
        total_nav=nav,
        cash_balance=cash,
        equity_value=equity,
        day_pnl=None,
        day_pnl_pct=None,
        total_realized_pnl=realized,
        total_unrealized_pnl=unrealized.quantize(Decimal("0.01")),
    )

    return PortfolioSummary(
        accounts=[account],
        combined_nav=nav,
        combined_equity_value=equity.quantize(Decimal("0.01")),
        combined_day_pnl=None,
        combined_day_pnl_pct=None,
        total_realized_pnl=realized,
        total_unrealized_pnl=unrealized.quantize(Decimal("0.01")),
        positions=positions,
        as_of=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ibkr_realized_pnl(account_id: str) -> Optional[Decimal]:
    """Realized P&L from IBKR account summary or sum of position realizedPnl."""
    from services.ibkr_client import ibkr_client

    if not config.IBKR_ENABLED or not ibkr_client.is_connected:
        return None

    summary = ibkr_client.get_account_summary(account_id) or {}
    for key in ("realizedpnl", "RealizedPnL", "realizedPnl"):
        entry = summary.get(key)
        if isinstance(entry, dict) and entry.get("amount") is not None:
            return Decimal(str(entry["amount"])).quantize(Decimal("0.01"))

    total = sum(float(p.get("realizedPnl") or 0) for p in ibkr_client.get_positions(account_id))
    return Decimal(str(total)).quantize(Decimal("0.01"))


async def _compute_realized_pnl_fifo(pool, account_id: str) -> Decimal:
    """FIFO realized P&L from closed lots in the trades table."""
    rows = await pool.fetch(
        """
        SELECT symbol, side, quantity, price, commission, trade_date, id
        FROM trades
        WHERE account_id = $1
        ORDER BY trade_date, id
        """,
        account_id,
    )
    lots: dict[str, list[list[float]]] = defaultdict(list)
    realized = Decimal(0)

    for row in rows:
        sym = row["symbol"].upper()
        qty = float(row["quantity"])
        price = float(row["price"])
        commission = float(row["commission"] or 0)

        if row["side"] == "BUY":
            cost_per_share = (qty * price + commission) / qty if qty else 0
            lots[sym].append([qty, cost_per_share])
        else:
            remaining = qty
            while remaining > 0.0001 and lots[sym]:
                lot_qty, lot_cost = lots[sym][0]
                take = min(remaining, lot_qty)
                proceeds = take * price - (commission * take / qty if qty else 0)
                cost = take * lot_cost
                realized += Decimal(str(proceeds - cost))
                lot_qty -= take
                remaining -= take
                if lot_qty <= 0.0001:
                    lots[sym].pop(0)
                else:
                    lots[sym][0][0] = lot_qty

    return realized.quantize(Decimal("0.01"))


async def _resolve_realized_pnl(pool, account_id: str) -> Decimal:
    """Prefer IBKR realized P&L when live; otherwise FIFO from trades."""
    ibkr_val = _ibkr_realized_pnl(account_id)
    if ibkr_val is not None:
        return ibkr_val
    return await _compute_realized_pnl_fifo(pool, account_id)


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

    if not rows and config.IBKR_ENABLED:
        return _ibkr_positions(account_id)

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
    realized_pnl = await _resolve_realized_pnl(pool, account_id)

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
        total_realized_pnl=realized_pnl,
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

        if not account_ids and config.IBKR_ENABLED and config.IBKR_ACCOUNT_ID:
            return await _ibkr_portfolio_summary(pool)

        accounts = []
        for acct_id in account_ids:
            accounts.append(await _account_summary(pool, acct_id))

        positions = await _compute_positions(pool)

        combined_equity = sum((_dec(a.equity_value or 0) for a in accounts), Decimal(0))
        combined_unrealized = sum((_dec(a.total_unrealized_pnl or 0) for a in accounts), Decimal(0))
        combined_realized = sum((_dec(a.total_realized_pnl or 0) for a in accounts), Decimal(0))
        combined_day_pnl = sum((_dec(a.day_pnl or 0) for a in accounts), Decimal(0))

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
            combined_day_pnl=combined_day_pnl.quantize(Decimal("0.01")) if combined_day_pnl != 0 else None,
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
