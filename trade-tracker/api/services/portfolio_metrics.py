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
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import asyncpg

import config
from models.schemas import PerformancePoint, PortfolioMetrics
from services.market_data import get_historical_bars, get_historical_bars_batch, get_spy_history

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
            continue  # skip — nav=0 means no real data (yfinance failed that day)
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
    cash_balance: Optional[Decimal] = None,
    equity_value: Optional[Decimal] = None,
    prev_nav: Optional[Decimal] = None,
) -> None:
    """
    Upsert a portfolio NAV snapshot.
    Also fetches SPY close for the date and stores it for overlay calculations.
    """
    from services.market_data import get_historical_bars

    # Get SPY data for this date
    spy_bars = get_historical_bars("SPY", snapshot_date, snapshot_date)
    spy_close = Decimal(str(spy_bars[0].close)) if spy_bars else None

    daily_pnl: Optional[Decimal] = None
    daily_pnl_pct: Optional[Decimal] = None
    if prev_nav and prev_nav > 0:
        daily_pnl = total_nav - prev_nav
        daily_pnl_pct = (daily_pnl / prev_nav * 100).quantize(Decimal("0.000001"))

    # Fetch previous SPY close to calculate daily pct
    spy_daily_pct: Optional[Decimal] = None
    if spy_close:
        prev_spy_bars = get_historical_bars(
            "SPY",
            snapshot_date - timedelta(days=5),  # look back enough to find last trading day
            snapshot_date - timedelta(days=1),
        )
        if prev_spy_bars:
            prev_spy_close = prev_spy_bars[-1].close
            if prev_spy_close > 0:
                spy_daily_pct = (
                    (spy_close - prev_spy_close) / prev_spy_close * 100
                ).quantize(Decimal("0.000001"))

    # ON CONFLICT must reference different partial indexes depending on whether
    # account_id is NULL (combined portfolio) or NOT NULL (per-account).
    # A plain UNIQUE(date, account_id) silently fails for NULLs in PostgreSQL
    # because NULL != NULL in standard equality — so we use partial indexes.
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
    logger.info("Upserted snapshot %s account=%s nav=%s", snapshot_date, account_id, total_nav)


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
    if rows:
        return _performance_from_snapshots(rows)

    acct = account_id or config.IBKR_ACCOUNT_ID

    if config.IBKR_ENABLED and config.IBKR_ACCOUNT_ID:
        if not account_id or account_id == config.IBKR_ACCOUNT_ID:
            ibkr_pts = _ibkr_performance_series(start, end)
            if ibkr_pts:
                return ibkr_pts

    if acct:
        trade_pts = await _trades_performance_series(pool, acct, start, end)
        if trade_pts:
            return trade_pts

    return []


async def _trades_performance_series(
    pool: asyncpg.Pool,
    account_id: str,
    start: date,
    end: date,
) -> list[PerformancePoint]:
    """
    Replay synced trades to rebuild daily holdings, then mark-to-market via yfinance.
    More accurate than applying current share counts across history.
    """
    rows = await pool.fetch(
        """
        SELECT symbol, side, quantity, price, commission, trade_date
        FROM trades
        WHERE account_id = $1 AND trade_date::date <= $2
        ORDER BY trade_date, id
        """,
        account_id,
        end,
    )
    if not rows:
        return []

    symbols = sorted({r["symbol"].upper() for r in rows})
    spy_bars = get_spy_history(start, end)
    if not spy_bars:
        logger.warning("No SPY history for performance series")
        return []

    batch = get_historical_bars_batch(symbols, start, end)
    closes: dict[str, dict[date, float]] = {
        sym: {b.date: float(b.close) for b in batch.get(sym, [])}
        for sym in symbols
    }

    holdings: dict[str, float] = defaultdict(float)
    cash = 0.0
    trade_idx = 0
    points: list[PerformancePoint] = []
    spy_base = float(spy_bars[0].close)
    prev_port: Optional[float] = None
    base_port: Optional[float] = None

    for bar in spy_bars:
        d = bar.date
        while trade_idx < len(rows):
            t = rows[trade_idx]
            td = t["trade_date"].date() if hasattr(t["trade_date"], "date") else t["trade_date"]
            if td > d:
                break
            sym = t["symbol"].upper()
            qty = float(t["quantity"])
            price = float(t["price"])
            commission = float(t["commission"] or 0)
            if t["side"] == "BUY":
                cash -= qty * price + commission
                holdings[sym] += qty
            else:
                cash += qty * price - commission
                holdings[sym] -= qty
            trade_idx += 1

        equity = sum(
            holdings[sym] * closes[sym][d]
            for sym in holdings
            if holdings[sym] > 0 and d in closes.get(sym, {})
        )
        port_val = cash + equity
        if port_val <= 0:
            continue
        if base_port is None:
            base_port = port_val
        port_cum = (port_val - base_port) / base_port * 100
        spy_cum = (float(bar.close) - spy_base) / spy_base * 100
        daily_pct = ((port_val - prev_port) / prev_port * 100) if prev_port else None
        prev_port = port_val

        points.append(
            PerformancePoint(
                date=d,
                portfolio_nav=Decimal(str(round(port_val, 2))),
                portfolio_pct_change=Decimal(str(round(daily_pct, 6))) if daily_pct is not None else None,
                spy_pct_change=None,
                spy_cumulative_pct=Decimal(str(round(spy_cum, 4))),
                portfolio_cumulative_pct=Decimal(str(round(port_cum, 4))),
            )
        )

    logger.info("Built %d performance points from trade replay for %s", len(points), account_id)
    return points


def _performance_from_snapshots(rows: list) -> list[PerformancePoint]:
    """Build cumulative returns from stored NAV snapshots."""
    base_nav = float(rows[0]["total_nav"])
    points: list[PerformancePoint] = []
    spy_base: Optional[float] = None

    for i, row in enumerate(rows):
        nav = float(row["total_nav"])
        port_cum = ((nav - base_nav) / base_nav * 100) if base_nav else None
        spy_daily = float(row["spy_daily_pct"]) if row["spy_daily_pct"] else None

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


def _ibkr_performance_series(start: date, end: date) -> list[PerformancePoint]:
    """
    ponytail: backfill chart from IBKR holdings + yfinance daily closes.
    Uses current share counts across the period (approximation until trades sync fills history).
    """
    from services.ibkr_client import ibkr_client

    if not ibkr_client.is_connected:
        return []

    positions = ibkr_client.live_positions(config.IBKR_ACCOUNT_ID)
    if not positions:
        return []

    symbols = [p["symbol"].upper().split()[0] for p in positions]
    batch = get_historical_bars_batch(symbols + [config.BENCHMARK_SYMBOL], start, end)
    spy_bars = batch.get(config.BENCHMARK_SYMBOL.upper(), []) or get_spy_history(start, end)
    if not spy_bars:
        return []

    closes: dict[str, dict[date, float]] = {
        sym.upper(): {b.date: float(b.close) for b in batch.get(sym.upper(), [])}
        for sym in symbols
    }
    qty_by_sym = {p["symbol"].upper(): float(p["quantity"]) for p in positions}

    points: list[PerformancePoint] = []
    base_port: Optional[float] = None
    spy_base = float(spy_bars[0].close)
    prev_port: Optional[float] = None

    for bar in spy_bars:
        d = bar.date
        port_val = sum(
            closes[sym][d] * qty
            for sym, qty in qty_by_sym.items()
            if d in closes.get(sym, {})
        )
        if port_val <= 0:
            continue
        if base_port is None:
            base_port = port_val
        port_cum = (port_val - base_port) / base_port * 100
        spy_cum = (float(bar.close) - spy_base) / spy_base * 100
        daily_pct = ((port_val - prev_port) / prev_port * 100) if prev_port else None
        prev_port = port_val

        points.append(
            PerformancePoint(
                date=d,
                portfolio_nav=Decimal(str(round(port_val, 2))),
                portfolio_pct_change=Decimal(str(round(daily_pct, 6))) if daily_pct is not None else None,
                spy_pct_change=None,
                spy_cumulative_pct=Decimal(str(round(spy_cum, 4))),
                portfolio_cumulative_pct=Decimal(str(round(port_cum, 4))),
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


def _ibkr_period_metrics(period: str) -> PortfolioMetrics:
    """
    ponytail: no snapshots yet — estimate period return from IBKR live prices
    vs yfinance price at period start. SPY benchmark from same window.
    """
    from services.ibkr_client import ibkr_client

    acct = config.IBKR_ACCOUNT_ID
    if not ibkr_client.is_connected:
        return PortfolioMetrics(
            period=period, beta=None, std_dev_annualized=None, sharpe_ratio=None,
            total_return_pct=None, spy_return_pct=None, alpha=None,
            max_drawdown_pct=None, win_rate=None, as_of=datetime.utcnow(),
        )

    start, end = _period_bounds(period)
    positions = ibkr_client.live_positions(acct)
    if not positions:
        return PortfolioMetrics(
            period=period, beta=None, std_dev_annualized=None, sharpe_ratio=None,
            total_return_pct=None, spy_return_pct=None, alpha=None,
            max_drawdown_pct=None, win_rate=None, as_of=datetime.utcnow(),
        )

    value_now = sum(p["market_value"] for p in positions)
    symbols = [p["symbol"] for p in positions]
    history = get_historical_bars_batch(
        symbols + [config.BENCHMARK_SYMBOL], start, end
    )

    value_then = 0.0
    for p in positions:
        bars = history.get(p["symbol"].upper(), [])
        if bars:
            value_then += float(bars[0].close) * p["quantity"]
        else:
            value_then += p["cost_basis"]  # fallback: flat if no history

    total_return = ((value_now - value_then) / value_then * 100) if value_then > 0 else None

    spy_bars = history.get(config.BENCHMARK_SYMBOL.upper(), [])
    spy_return = None
    if len(spy_bars) >= 2:
        first, last = float(spy_bars[0].close), float(spy_bars[-1].close)
        if first > 0:
            spy_return = (last - first) / first * 100

    def _dec(v: Optional[float]) -> Optional[Decimal]:
        return Decimal(str(round(v, 4))) if v is not None else None

    return PortfolioMetrics(
        period=period,
        beta=None,
        std_dev_annualized=None,
        sharpe_ratio=None,
        total_return_pct=_dec(total_return),
        spy_return_pct=_dec(spy_return),
        alpha=None,
        max_drawdown_pct=None,
        win_rate=None,
        as_of=datetime.utcnow(),
    )


async def calculate_metrics(
    pool: asyncpg.Pool,
    period: str = "ytd",
    account_id: Optional[str] = None,
) -> PortfolioMetrics:
    start, end = _period_bounds(period)
    rows = await _fetch_snapshots(pool, start, end, account_id)

    if len(rows) < 2:
        if config.IBKR_ENABLED and config.IBKR_ACCOUNT_ID and not account_id:
            return _ibkr_period_metrics(period)
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
