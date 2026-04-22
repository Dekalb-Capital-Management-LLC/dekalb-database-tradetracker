"""
Portfolio metrics calculations.

Provides:
- NAV-based return series from portfolio_snapshots
- Beta (portfolio vs SPY) for rolling 12-month and YTD periods
- Annualized standard deviation
- Sharpe ratio (risk-free rate = 0 for simplicity; can be updated)
- Alpha, max drawdown, win rate
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import asyncpg

from models.schemas import PerformancePoint, PortfolioMetrics

logger = logging.getLogger(__name__)

RISK_FREE_RATE_ANNUAL = 0.0   # update to e.g. 0.05 for 5% T-bill rate


# ---------------------------------------------------------------------------
# Helper math
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def _std_dev(values: list[float]) -> float:
    return math.sqrt(_variance(values))


def _covariance(x: list[float], y: list[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mx, my = _mean(x), _mean(y)
    return sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (len(x) - 1)


def _beta(portfolio_returns: list[float], benchmark_returns: list[float]) -> Optional[float]:
    var_bm = _variance(benchmark_returns)
    if var_bm == 0:
        return None
    cov = _covariance(portfolio_returns, benchmark_returns)
    return cov / var_bm


def _max_drawdown(nav_series: list[float]) -> float:
    """Maximum peak-to-trough drawdown as a negative percentage."""
    if len(nav_series) < 2:
        return 0.0
    peak = nav_series[0]
    max_dd = 0.0
    for nav in nav_series:
        if nav > peak:
            peak = nav
        if peak == 0:
            continue  # skip — nav=0 means no price data available that day
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd * 100  # as percentage


# ---------------------------------------------------------------------------
# Data fetching from DB
# ---------------------------------------------------------------------------

async def _fetch_snapshots(
    pool: asyncpg.Pool,
    start: date,
    end: date,
    account_id: Optional[str] = None,
) -> list[asyncpg.Record]:
    """
    Fetch portfolio snapshots in date range.
    If account_id is None, returns combined totals (account_id IS NULL rows).
    """
    if account_id:
        rows = await pool.fetch(
            """
            SELECT snapshot_date, total_nav, daily_pnl_pct, spy_daily_pct, spy_close
            FROM portfolio_snapshots
            WHERE account_id = $1
              AND snapshot_date BETWEEN $2 AND $3
            ORDER BY snapshot_date ASC
            """,
            account_id, start, end,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT snapshot_date, total_nav, daily_pnl_pct, spy_daily_pct, spy_close
            FROM portfolio_snapshots
            WHERE account_id IS NULL
              AND snapshot_date BETWEEN $1 AND $2
            ORDER BY snapshot_date ASC
            """,
            start, end,
        )
    return rows


# ---------------------------------------------------------------------------
# Snapshot upsert (called by nightly job or on-demand)
# ---------------------------------------------------------------------------

async def upsert_snapshot(
    pool: asyncpg.Pool,
    snapshot_date: date,
    total_nav: Decimal,
    account_id: Optional[str],
    equity_value: Optional[Decimal] = None,
    prev_nav: Optional[Decimal] = None,
    cash_balance: Optional[Decimal] = None,
) -> None:
    """Upsert a portfolio NAV snapshot with SPY close from yfinance."""
    import yfinance as yf

    # Fetch SPY close for this date via yfinance
    spy_close: Optional[Decimal] = None
    spy_daily_pct: Optional[Decimal] = None
    try:
        spy_hist = yf.Ticker("SPY").history(start=snapshot_date.isoformat(), end=(snapshot_date + timedelta(days=5)).isoformat())
        if not spy_hist.empty:
            spy_close = Decimal(str(round(float(spy_hist["Close"].iloc[0]), 4)))
            if len(spy_hist) > 1 or prev_nav is not None:
                # Get previous trading day close for daily pct
                spy_prev = yf.Ticker("SPY").history(
                    start=(snapshot_date - timedelta(days=7)).isoformat(),
                    end=snapshot_date.isoformat(),
                )
                if not spy_prev.empty:
                    prev_spy = Decimal(str(round(float(spy_prev["Close"].iloc[-1]), 4)))
                    if prev_spy > 0:
                        spy_daily_pct = ((spy_close - prev_spy) / prev_spy * 100).quantize(Decimal("0.000001"))
    except Exception as exc:
        logger.warning("SPY fetch failed for %s: %s", snapshot_date, exc)

    daily_pnl: Optional[Decimal] = None
    daily_pnl_pct: Optional[Decimal] = None
    if prev_nav and prev_nav > 0:
        daily_pnl = total_nav - prev_nav
        daily_pnl_pct = (daily_pnl / prev_nav * 100).quantize(Decimal("0.000001"))

    if account_id is None:
        conflict_clause = "ON CONFLICT (snapshot_date) WHERE account_id IS NULL"
    else:
        conflict_clause = "ON CONFLICT (snapshot_date, account_id) WHERE account_id IS NOT NULL"

    await pool.execute(
        f"""
        INSERT INTO portfolio_snapshots
            (snapshot_date, account_id, total_nav, cash_balance, equity_value,
             daily_pnl, daily_pnl_pct, spy_close, spy_daily_pct)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        {conflict_clause}
        DO UPDATE SET
            total_nav     = EXCLUDED.total_nav,
            cash_balance  = EXCLUDED.cash_balance,
            equity_value  = EXCLUDED.equity_value,
            daily_pnl     = EXCLUDED.daily_pnl,
            daily_pnl_pct = EXCLUDED.daily_pnl_pct,
            spy_close     = EXCLUDED.spy_close,
            spy_daily_pct = EXCLUDED.spy_daily_pct
        """,
        snapshot_date, account_id, total_nav, cash_balance, equity_value,
        daily_pnl, daily_pnl_pct, spy_close, spy_daily_pct,
    )
    logger.info("Upserted snapshot %s account=%s nav=%s spy=%s", snapshot_date, account_id, total_nav, spy_close)


# ---------------------------------------------------------------------------
# Performance series (for graph)
# ---------------------------------------------------------------------------

async def get_performance_series(
    pool: asyncpg.Pool,
    start: date,
    end: date,
    account_id: Optional[str] = None,
) -> list[PerformancePoint]:
    rows = await _fetch_snapshots(pool, start, end, account_id)
    if not rows:
        return []

    # Build cumulative returns from first day
    base_nav = float(rows[0]["total_nav"])
    points: list[PerformancePoint] = []
    spy_base: Optional[float] = None

    for i, row in enumerate(rows):
        nav = float(row["total_nav"])
        port_cum = ((nav - base_nav) / base_nav * 100) if base_nav else None

        spy_daily = float(row["spy_daily_pct"]) if row["spy_daily_pct"] else None

        # Accumulate SPY cumulative return
        if i == 0:
            spy_cum = 0.0
            spy_base = float(row["spy_close"]) if row["spy_close"] else None
        elif spy_base and row["spy_close"]:
            spy_cum = (float(row["spy_close"]) - spy_base) / spy_base * 100
        else:
            spy_cum = None

        points.append(
            PerformancePoint(
                date=row["snapshot_date"],
                portfolio_nav=Decimal(str(round(nav, 2))),
                portfolio_pct_change=(
                    Decimal(str(round(float(row["daily_pnl_pct"]), 6)))
                    if row["daily_pnl_pct"] else None
                ),
                spy_pct_change=Decimal(str(round(spy_daily, 6))) if spy_daily is not None else None,
                spy_cumulative_pct=Decimal(str(round(spy_cum, 4))) if spy_cum is not None else None,
                portfolio_cumulative_pct=Decimal(str(round(port_cum, 4))) if port_cum is not None else None,
            )
        )
    return points


# ---------------------------------------------------------------------------
# Metrics calculation
# ---------------------------------------------------------------------------

def _period_bounds(period: str) -> tuple[date, date]:
    today = date.today()
    if period == "ytd":
        start = date(today.year, 1, 1)
    elif period == "1y":
        start = today - timedelta(days=365)
    elif period == "6m":
        start = today - timedelta(days=182)
    elif period == "3m":
        start = today - timedelta(days=91)
    elif period == "1m":
        start = today - timedelta(days=30)
    else:
        start = date(today.year, 1, 1)  # default ytd
    return start, today


async def calculate_metrics(
    pool: asyncpg.Pool,
    period: str = "ytd",
    account_id: Optional[str] = None,
) -> PortfolioMetrics:
    start, end = _period_bounds(period)
    rows = await _fetch_snapshots(pool, start, end, account_id)

    if len(rows) < 2:
        return PortfolioMetrics(
            period=period,
            beta=None,
            std_dev_annualized=None,
            sharpe_ratio=None,
            total_return_pct=None,
            spy_return_pct=None,
            alpha=None,
            max_drawdown_pct=None,
            win_rate=None,
            as_of=datetime.utcnow(),
        )

    port_daily_returns = [
        float(r["daily_pnl_pct"]) / 100 for r in rows if r["daily_pnl_pct"] is not None
    ]
    spy_daily_returns = [
        float(r["spy_daily_pct"]) / 100 for r in rows if r["spy_daily_pct"] is not None
    ]

    nav_series = [float(r["total_nav"]) for r in rows]

    # Align lengths (in case some days missing spy data)
    min_len = min(len(port_daily_returns), len(spy_daily_returns))
    port_r = port_daily_returns[:min_len]
    spy_r = spy_daily_returns[:min_len]

    # Beta
    beta_val = _beta(port_r, spy_r)

    # Annualized std dev (assuming 252 trading days)
    std_dev_daily = _std_dev(port_daily_returns)
    std_dev_annual = std_dev_daily * math.sqrt(252) * 100 if std_dev_daily else None

    # Total return
    first_nav = float(rows[0]["total_nav"])
    last_nav = float(rows[-1]["total_nav"])
    total_return = (last_nav - first_nav) / first_nav * 100 if first_nav > 0 else None

    # SPY total return
    first_spy = float(rows[0]["spy_close"]) if rows[0]["spy_close"] else None
    last_spy = float(rows[-1]["spy_close"]) if rows[-1]["spy_close"] else None
    spy_return = (last_spy - first_spy) / first_spy * 100 if (first_spy and last_spy) else None

    # Alpha = portfolio return - beta * spy return
    alpha = None
    if total_return is not None and beta_val is not None and spy_return is not None:
        alpha = total_return - beta_val * spy_return

    # Sharpe (annualized, risk-free = 0)
    sharpe = None
    if std_dev_annual and std_dev_annual != 0 and total_return is not None:
        trading_days = len(port_daily_returns)
        annual_factor = 252 / trading_days if trading_days else 1
        annualized_return = total_return * annual_factor
        sharpe = (annualized_return - RISK_FREE_RATE_ANNUAL * 100) / std_dev_annual

    # Max drawdown
    max_dd = _max_drawdown(nav_series)

    # Win rate: % of profitable trades in the period
    win_rate_val = await _calculate_win_rate(pool, start, end, account_id)

    def _dec(v: Optional[float], places: int = 4) -> Optional[Decimal]:
        if v is None:
            return None
        return Decimal(str(round(v, places)))

    return PortfolioMetrics(
        period=period,
        beta=_dec(beta_val),
        std_dev_annualized=_dec(std_dev_annual),
        sharpe_ratio=_dec(sharpe),
        total_return_pct=_dec(total_return),
        spy_return_pct=_dec(spy_return),
        alpha=_dec(alpha),
        max_drawdown_pct=_dec(max_dd),
        win_rate=_dec(win_rate_val),
        as_of=datetime.utcnow(),
    )


async def backfill_snapshots(
    pool: asyncpg.Pool,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict:
    """
    Generate daily historical portfolio NAV snapshots from trade history + IBKR prices.
    This is what powers the performance graph.

    Algorithm:
      1. Walk through each trading day from first trade to today
      2. Maintain running positions per account as trades are applied
      3. For each day, fetch historical close prices from IBKR
      4. NAV = SUM(qty × close_price) for all open positions
      5. Upsert snapshot rows (safe to re-run — ON CONFLICT DO UPDATE)
    """

    # Date range
    first_trade_date = await pool.fetchval(
        "SELECT MIN(trade_date::date) FROM trades"
    )
    if not first_trade_date:
        return {"error": "No trades found"}

    start = start_date or first_trade_date
    end = end_date or date.today()

    logger.info("Backfilling snapshots from %s to %s", start, end)

    # Load all trades sorted by date
    trade_rows = await pool.fetch(
        """
        SELECT account_id, symbol, side,
               quantity::float AS quantity,
               trade_date::date AS trade_date
        FROM trades
        ORDER BY trade_date ASC, id ASC
        """
    )
    if not trade_rows:
        return {"error": "No trades to process"}

    # All unique symbols and accounts
    symbols = list({r["symbol"] for r in trade_rows})
    account_ids = list({r["account_id"] for r in trade_rows})

    # Batch-fetch ALL symbols + SPY via IBKR
    from services.market_data import get_historical_bars_batch
    all_symbols_to_fetch = list(set(symbols + ["SPY"]))
    logger.info("Batch-fetching %d symbols from IBKR...", len(all_symbols_to_fetch))
    batch = await get_historical_bars_batch(pool, all_symbols_to_fetch, start, end)

    symbol_price_map: dict[str, dict[date, Decimal]] = {s: batch.get(s, {}) for s in symbols}
    spy_price_map: dict[date, Decimal] = batch.get("SPY", {})
    spy_dates = sorted(spy_price_map.keys())
    logger.info("Batch fetch complete: %d SPY bars", len(spy_dates))

    # Build list of all calendar days in range
    all_dates: list[date] = []
    cur = start
    while cur <= end:
        all_dates.append(cur)
        cur += timedelta(days=1)

    # Group trades by account
    trades_by_account: dict[str, list] = {a: [] for a in account_ids}
    for r in trade_rows:
        trades_by_account[r["account_id"]].append(r)

    snapshots_written = 0

    for acct_id in account_ids:
        acct_trades = trades_by_account[acct_id]
        positions: dict[str, float] = {}  # symbol -> net qty
        trade_idx = 0
        prev_nav: Optional[Decimal] = None

        for d in all_dates:
            # Apply all trades on or before this date
            while trade_idx < len(acct_trades) and acct_trades[trade_idx]["trade_date"] <= d:
                t = acct_trades[trade_idx]
                sym = t["symbol"]
                qty = float(t["quantity"])
                if t["side"] == "BUY":
                    positions[sym] = positions.get(sym, 0.0) + qty
                else:
                    positions[sym] = positions.get(sym, 0.0) - qty
                trade_idx += 1

            # Compute NAV for this day
            nav = Decimal("0")
            any_price = False
            for sym, qty in positions.items():
                if qty <= 0.00001:
                    continue
                px = symbol_price_map.get(sym, {}).get(d)
                if px:
                    nav += Decimal(str(round(qty, 6))) * px
                    any_price = True

            if not any_price:
                # Weekend / market holiday with no prices — skip to avoid zero-value noise
                continue

            # Pre-fetch SPY close for this date (and previous trading day for daily pct)
            spy_close_d = spy_price_map.get(d)
            spy_prev_d: Optional[Decimal] = None
            if spy_close_d is not None:
                # Find the most recent SPY date before d
                prev_spy_dates = [sd for sd in spy_dates if sd < d]
                if prev_spy_dates:
                    spy_prev_d = spy_price_map[prev_spy_dates[-1]]

            await upsert_snapshot(
                pool=pool,
                snapshot_date=d,
                total_nav=nav.quantize(Decimal("0.01")),
                account_id=acct_id,
                equity_value=nav.quantize(Decimal("0.01")),
                prev_nav=prev_nav,
                spy_close=spy_close_d,
                spy_prev_close=spy_prev_d,
            )
            prev_nav = nav
            snapshots_written += 1

    # Combined snapshot: sum of all accounts for each date
    combined_rows = await pool.fetch(
        """
        SELECT snapshot_date, SUM(total_nav) AS combined_nav
        FROM portfolio_snapshots
        WHERE account_id IS NOT NULL
          AND snapshot_date BETWEEN $1 AND $2
        GROUP BY snapshot_date
        ORDER BY snapshot_date ASC
        """,
        start, end,
    )
    prev_combined: Optional[Decimal] = None
    for row in combined_rows:
        nav = Decimal(str(row["combined_nav"]))
        d = row["snapshot_date"]
        spy_close_d = spy_price_map.get(d)
        spy_prev_d = None
        if spy_close_d is not None:
            prev_spy_dates = [sd for sd in spy_dates if sd < d]
            if prev_spy_dates:
                spy_prev_d = spy_price_map[prev_spy_dates[-1]]
        await upsert_snapshot(
            pool=pool,
            snapshot_date=d,
            total_nav=nav,
            account_id=None,
            equity_value=nav,
            prev_nav=prev_combined,
            spy_close=spy_close_d,
            spy_prev_close=spy_prev_d,
        )
        prev_combined = nav
        snapshots_written += 1

    logger.info("Backfill complete: %d snapshots written", snapshots_written)
    return {
        "start": str(start),
        "end": str(end),
        "accounts": account_ids,
        "symbols": len(symbols),
        "snapshots_written": snapshots_written,
    }


async def _calculate_win_rate(
    pool: asyncpg.Pool,
    start: date,
    end: date,
    account_id: Optional[str],
) -> Optional[float]:
    """
    Win rate: number of SELL trades with net_amount > 0 (profitable closes)
    divided by total SELL trades in period.
    This is a simplified proxy - proper P&L requires matching buys to sells (FIFO/LIFO).
    """
    try:
        if account_id:
            row = await pool.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE net_amount > 0) AS wins,
                    COUNT(*) AS total
                FROM trades
                WHERE side = 'SELL'
                  AND account_id = $1
                  AND trade_date BETWEEN $2 AND $3
                """,
                account_id, start, end,
            )
        else:
            row = await pool.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE net_amount > 0) AS wins,
                    COUNT(*) AS total
                FROM trades
                WHERE side = 'SELL'
                  AND trade_date BETWEEN $1 AND $2
                """,
                start, end,
            )
        if row and row["total"] > 0:
            return float(row["wins"]) / float(row["total"]) * 100
    except Exception as exc:
        logger.error("Win rate calculation failed: %s", exc)
    return None


async def backfill_snapshots(pool: asyncpg.Pool) -> dict:
    """
    Write/update today's snapshot from imported_positions current values.
    Called automatically after a successful import.
    """
    try:
        acct_rows = await pool.fetch("SELECT DISTINCT account_id FROM imported_positions")
        if not acct_rows:
            return {"skipped": "no imported_positions"}
        today = date.today()
        combined_nav = Decimal(0)
        for ar in acct_rows:
            acct_id = ar["account_id"]
            t = await pool.fetchrow(
                "SELECT SUM(current_value) AS nav FROM imported_positions WHERE account_id=$1",
                acct_id,
            )
            nav = Decimal(str(t["nav"] or 0))
            prev = await pool.fetchrow(
                """
                SELECT total_nav FROM portfolio_snapshots
                WHERE account_id=$1 AND snapshot_date < $2
                ORDER BY snapshot_date DESC LIMIT 1
                """,
                acct_id, today,
            )
            await upsert_snapshot(pool, today, nav, acct_id, nav,
                                  Decimal(str(prev["total_nav"])) if prev else None)
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
        return {"ok": True, "nav": float(combined_nav)}
    except Exception as exc:
        logger.error("backfill_snapshots failed: %s", exc)
        return {"error": str(exc)}
