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

import asyncio
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


async def _net_external_flow(
    pool: asyncpg.Pool,
    account_id: Optional[str],
    flow_date: date,
) -> Decimal:
    """
    Net deposits/withdrawals for one account (or all accounts, combined) on
    one day. Deliberately excludes 'dividend'/'interest' rows — those are
    real investment return, not external funding, and should stay IN the
    performance number rather than be subtracted out.
    """
    if account_id:
        row = await pool.fetchrow(
            """
            SELECT COALESCE(SUM(amount), 0) AS net FROM cash_flows
            WHERE account_id = $1 AND flow_date::date = $2
              AND flow_type IN ('deposit', 'withdrawal')
            """,
            account_id, flow_date,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT COALESCE(SUM(amount), 0) AS net FROM cash_flows
            WHERE flow_date::date = $1
              AND flow_type IN ('deposit', 'withdrawal')
            """,
            flow_date,
        )
    return Decimal(str(row["net"])) if row else Decimal(0)


async def _cash_flows_by_date(
    pool: asyncpg.Pool,
    account_id: str,
    start: date,
    end: date,
) -> dict[date, float]:
    """Bulk version of _net_external_flow for replaying a whole date range at once."""
    rows = await pool.fetch(
        """
        SELECT flow_date::date AS d, SUM(amount) AS net FROM cash_flows
        WHERE account_id = $1 AND flow_date::date BETWEEN $2 AND $3
          AND flow_type IN ('deposit', 'withdrawal')
        GROUP BY d
        """,
        account_id, start, end,
    )
    return {r["d"]: float(r["net"] or 0) for r in rows}


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
        # Exclude deposits/withdrawals that landed on this exact day — otherwise
        # adding $5k to the account reads as a 50% gain on a $10k portfolio.
        net_flow = await _net_external_flow(pool, account_id, snapshot_date)
        daily_pnl = total_nav - prev_nav - net_flow
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
# Snapshot backfill (called after imports / from update-all)
# ---------------------------------------------------------------------------

async def _account_nav_for_snapshot(pool: asyncpg.Pool, account_id: str) -> Decimal:
    """
    NAV to snapshot for one account: live IBKR (which includes cash, via the
    synthetic CASH row _compute_positions adds) when it's the connected IBKR
    account, else summed from imported_positions.current_value.
    """
    if config.IBKR_ENABLED and account_id == config.IBKR_ACCOUNT_ID:
        from services.ibkr_client import ibkr_client
        if ibkr_client.is_connected:
            try:
                from routers.portfolio import _compute_positions
                positions = await _compute_positions(pool, account_id)
                return sum((p.market_value or Decimal(0)) for p in positions) or Decimal(0)
            except Exception as exc:
                logger.warning("Live IBKR NAV failed, falling back to snapshot sum: %s", exc)

    snap_total = await pool.fetchrow(
        "SELECT SUM(current_value) AS nav FROM imported_positions WHERE account_id=$1",
        account_id,
    )
    return Decimal(str(snap_total["nav"] or 0))


async def backfill_snapshots(pool: asyncpg.Pool) -> dict:
    """
    Ensure today's NAV snapshot exists for every known account, plus the
    combined total. This is NOT a full historical rebuild (that would need
    trade-replay x historical pricing for every past day) — it just makes
    sure "today" is always recorded, which is what update-all and the
    post-import hook actually need.
    """
    today = date.today()
    account_rows = await pool.fetch(
        "SELECT DISTINCT account_id FROM imported_positions "
        "UNION SELECT DISTINCT account_id FROM trades"
    )
    combined_nav = Decimal(0)
    written = 0
    for r in account_rows:
        acct_id = r["account_id"]
        if not acct_id:
            continue
        nav = await _account_nav_for_snapshot(pool, acct_id)
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
        written += 1

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
    logger.info("backfill_snapshots: wrote %d account snapshot(s), combined_nav=%s", written, combined_nav)
    return {"accounts_written": written, "combined_nav": float(combined_nav)}


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
    # A single day of snapshot history (e.g. right after a reset) makes for a
    # dead one-point graph even though there's plenty of real trade/price
    # history to replay — only trust the snapshot table once it has enough
    # rows to be more informative than the live/trade-replay fallbacks below.
    if len(rows) >= 2:
        return _performance_from_snapshots(rows)

    acct = account_id or config.IBKR_ACCOUNT_ID

    if config.IBKR_ENABLED and config.IBKR_ACCOUNT_ID:
        if not account_id or account_id == config.IBKR_ACCOUNT_ID:
            # Synchronous (blocking HTTP/yfinance calls) — run off the event
            # loop so a slow historical-bars fetch doesn't freeze every other
            # concurrent request.
            loop = asyncio.get_event_loop()
            ibkr_pts = await loop.run_in_executor(None, _ibkr_performance_series, start, end)
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

    # Custom cost-basis-lot sheets (universal_parser.py) record every lot as a
    # synthetic BUY with no corresponding cash/deposit ever recorded — it's a
    # "here's what I currently hold" snapshot, not a real cash-tracked ledger.
    # Modeling "cash" for these drains to a huge negative number with nothing
    # to offset it, which blew up every ratio that divides by portfolio value
    # (the +450% return / -97% drawdown / beta-138 readings). If this account
    # has never recorded a real SELL, skip cash entirely and value the
    # portfolio as just the stock — there's no real cash balance to model.
    has_sells = any(r["side"] == "SELL" for r in rows)

    symbols = sorted({r["symbol"].upper() for r in rows})
    loop = asyncio.get_event_loop()
    spy_bars = await loop.run_in_executor(None, get_spy_history, start, end)
    if not spy_bars:
        logger.warning("No SPY history for performance series")
        return []

    batch = await loop.run_in_executor(None, get_historical_bars_batch, symbols, start, end)
    closes: dict[str, dict[date, float]] = {
        sym: {b.date: float(b.close) for b in batch.get(sym, [])}
        for sym in symbols
    }
    flows_by_date = await _cash_flows_by_date(pool, account_id, start, end)

    holdings: dict[str, float] = defaultdict(float)
    cash = 0.0
    trade_idx = 0
    points: list[PerformancePoint] = []
    spy_base = float(spy_bars[0].close)
    prev_port: Optional[float] = None
    prev_spy: Optional[float] = None
    cum_factor = 1.0

    for bar in spy_bars:
        d = bar.date
        implicit_deposit_today = 0.0
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
                cost = qty * price + commission
                if has_sells:
                    cash -= cost
                else:
                    # Lot-only sheet: this "purchase" is newly-tracked capital
                    # entering the portfolio (there's no real cash account it
                    # came out of), not investment growth — exclude its cost
                    # from today's return the same way a real cash deposit
                    # would be excluded. Without this, every new lot's full
                    # value appeared "for free" the day it's acquired, which
                    # is exactly what was producing the absurd +1985% return.
                    implicit_deposit_today += cost
                holdings[sym] += qty
            else:
                cash += qty * price - commission
                holdings[sym] -= qty
            trade_idx += 1

        # External deposits/withdrawals land in cash like any other day's
        # activity (so the dollar NAV stays real), but get excluded from the
        # *return* below — otherwise funding a purchase with new cash reads
        # as investment growth.
        flow_today = (flows_by_date.get(d, 0.0) if has_sells else 0.0) + implicit_deposit_today
        if has_sells:
            cash += flows_by_date.get(d, 0.0)

        equity = sum(
            holdings[sym] * closes[sym][d]
            for sym in holdings
            if holdings[sym] > 0 and d in closes.get(sym, {})
        )
        port_val = (cash + equity) if has_sells else equity
        if port_val <= 0:
            continue
        daily_pct = ((port_val - prev_port - flow_today) / prev_port * 100) if prev_port else None
        spy_daily_pct = ((float(bar.close) - prev_spy) / prev_spy * 100) if prev_spy else None
        if daily_pct is not None:
            cum_factor *= (1 + daily_pct / 100)
        port_cum = (cum_factor - 1) * 100
        spy_cum = (float(bar.close) - spy_base) / spy_base * 100
        prev_port = port_val
        prev_spy = float(bar.close)

        points.append(
            PerformancePoint(
                date=d,
                portfolio_nav=Decimal(str(round(port_val, 2))),
                portfolio_pct_change=Decimal(str(round(daily_pct, 6))) if daily_pct is not None else None,
                spy_pct_change=Decimal(str(round(spy_daily_pct, 6))) if spy_daily_pct is not None else None,
                spy_cumulative_pct=Decimal(str(round(spy_cum, 4))),
                portfolio_cumulative_pct=Decimal(str(round(port_cum, 4))),
            )
        )

    logger.info("Built %d performance points from trade replay for %s", len(points), account_id)
    return points


def _performance_from_snapshots(rows: list) -> list[PerformancePoint]:
    """
    Build cumulative returns from stored NAV snapshots.
    Cumulative % is the chained (compounded) product of each day's
    daily_pnl_pct rather than a raw (nav - day1_nav) / day1_nav comparison —
    daily_pnl_pct already has deposits/withdrawals excluded (see
    upsert_snapshot), but a deposit still moves total_nav itself, so chaining
    the already-adjusted daily returns is what keeps the *cumulative* number
    from re-absorbing that bias.
    """
    points: list[PerformancePoint] = []
    spy_base: Optional[float] = None
    cum_factor = 1.0

    for i, row in enumerate(rows):
        nav = float(row["total_nav"])
        daily_pct_raw = float(row["daily_pnl_pct"]) if row["daily_pnl_pct"] else None
        if i > 0 and daily_pct_raw is not None:
            cum_factor *= (1 + daily_pct_raw / 100)
        port_cum = (cum_factor - 1) * 100
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
    spy_base = float(spy_bars[0].close)
    prev_port: Optional[float] = None
    prev_spy: Optional[float] = None
    cum_factor = 1.0

    for bar in spy_bars:
        d = bar.date
        port_val = sum(
            closes[sym][d] * qty
            for sym, qty in qty_by_sym.items()
            if d in closes.get(sym, {})
        )
        if port_val <= 0:
            continue
        daily_pct = ((port_val - prev_port) / prev_port * 100) if prev_port else None
        spy_daily_pct = ((float(bar.close) - prev_spy) / prev_spy * 100) if prev_spy else None
        if daily_pct is not None:
            cum_factor *= (1 + daily_pct / 100)
        port_cum = (cum_factor - 1) * 100
        spy_cum = (float(bar.close) - spy_base) / spy_base * 100
        prev_port = port_val
        prev_spy = float(bar.close)

        points.append(
            PerformancePoint(
                date=d,
                portfolio_nav=Decimal(str(round(port_val, 2))),
                portfolio_pct_change=Decimal(str(round(daily_pct, 6))) if daily_pct is not None else None,
                spy_pct_change=Decimal(str(round(spy_daily_pct, 6))) if spy_daily_pct is not None else None,
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


def _blank_metrics(period: str) -> PortfolioMetrics:
    return PortfolioMetrics(
        period=period, beta=None, std_dev_annualized=None, sharpe_ratio=None,
        total_return_pct=None, spy_return_pct=None, alpha=None,
        max_drawdown_pct=None, win_rate=None, as_of=datetime.utcnow(),
    )


async def _metrics_from_points(
    pool: asyncpg.Pool,
    account_id: str,
    period: str,
    points: list[PerformancePoint],
) -> PortfolioMetrics:
    """
    Compute the full metric set (beta, std dev, sharpe, alpha, max drawdown,
    win rate, total/SPY return) from a day-by-day PerformancePoint series —
    the same series the performance graph already builds via IBKR-live or
    trade replay. Shared by both fallbacks below so "the graph has the data,
    why are the other numbers blank" isn't true anymore once there's enough
    replayed history, even without 2+ stored snapshot rows.
    """
    if not points:
        return _blank_metrics(period)

    start, end = _period_bounds(period)
    port_daily = [float(p.portfolio_pct_change) / 100 for p in points if p.portfolio_pct_change is not None]
    spy_daily = [float(p.spy_pct_change) / 100 for p in points if p.spy_pct_change is not None]
    nav_series = [float(p.portfolio_nav) for p in points]

    min_len = min(len(port_daily), len(spy_daily))
    beta_val = _beta(port_daily[:min_len], spy_daily[:min_len])

    std_dev_daily = _std_dev(port_daily)
    std_dev_annual = std_dev_daily * math.sqrt(252) * 100 if std_dev_daily else None

    last = points[-1]
    total_return = float(last.portfolio_cumulative_pct) if last.portfolio_cumulative_pct is not None else None
    spy_return = float(last.spy_cumulative_pct) if last.spy_cumulative_pct is not None else None

    alpha = None
    if total_return is not None and beta_val is not None and spy_return is not None:
        alpha = total_return - beta_val * spy_return

    sharpe = None
    if std_dev_annual and std_dev_annual != 0 and total_return is not None:
        trading_days = len(port_daily)
        annual_factor = 252 / trading_days if trading_days else 1
        annualized_return = total_return * annual_factor
        sharpe = (annualized_return - RISK_FREE_RATE_ANNUAL * 100) / std_dev_annual

    max_dd = _max_drawdown(nav_series)
    win_rate_val = await _calculate_win_rate(pool, start, end, account_id)

    def _dec(v: Optional[float], places: int = 4) -> Optional[Decimal]:
        return Decimal(str(round(v, places))) if v is not None else None

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


async def _ibkr_period_metrics(pool: asyncpg.Pool, period: str) -> PortfolioMetrics:
    """Estimate metrics from the same IBKR-live replay series the graph uses."""
    from services.ibkr_client import ibkr_client

    if not ibkr_client.is_connected:
        return _blank_metrics(period)

    start, end = _period_bounds(period)
    loop = asyncio.get_event_loop()
    points = await loop.run_in_executor(None, _ibkr_performance_series, start, end)
    return await _metrics_from_points(pool, config.IBKR_ACCOUNT_ID, period, points)


async def _replay_period_metrics(pool: asyncpg.Pool, account_id: str, period: str) -> PortfolioMetrics:
    """
    Non-IBKR fallback for when there's less than 2 days of snapshot history
    (e.g. right after a fresh Fidelity upload): replay trades x historical
    prices — the same data the performance graph uses — to compute the full
    metric set via _metrics_from_points, instead of returning an all-blank
    result.
    """
    start, end = _period_bounds(period)
    points = await _trades_performance_series(pool, account_id, start, end)
    return await _metrics_from_points(pool, account_id, period, points)


async def calculate_metrics(
    pool: asyncpg.Pool,
    period: str = "ytd",
    account_id: Optional[str] = None,
) -> PortfolioMetrics:
    start, end = _period_bounds(period)
    rows = await _fetch_snapshots(pool, start, end, account_id)

    if len(rows) < 2:
        # Allow the IBKR live-estimate fallback both for the combined view
        # (account_id=None) and when the IBKR account itself is requested —
        # excluding the latter meant the IBKR tab always got an all-blank
        # PortfolioMetrics whenever it asked for its own account_id explicitly.
        if config.IBKR_ENABLED and config.IBKR_ACCOUNT_ID and (
            not account_id or account_id == config.IBKR_ACCOUNT_ID
        ):
            ibkr_metrics = await _ibkr_period_metrics(pool, period)
            if ibkr_metrics.total_return_pct is not None:
                return ibkr_metrics
        if account_id:
            replay = await _replay_period_metrics(pool, account_id, period)
            if replay.total_return_pct is not None:
                return replay
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

    # Align lengths (in case some days missing spy data)
    min_len = min(len(port_daily_returns), len(spy_daily_returns))
    port_r = port_daily_returns[:min_len]
    spy_r = spy_daily_returns[:min_len]

    # Beta
    beta_val = _beta(port_r, spy_r)

    # Annualized std dev (assuming 252 trading days)
    std_dev_daily = _std_dev(port_daily_returns)
    std_dev_annual = std_dev_daily * math.sqrt(252) * 100 if std_dev_daily else None

    # Total return — chain the already deposit/withdrawal-excluded daily
    # returns rather than comparing raw end NAV to start NAV, otherwise a
    # mid-period deposit reads as portfolio growth.
    cum_factor = 1.0
    cum_series = [1.0]
    for r in port_daily_returns:
        cum_factor *= (1 + r)
        cum_series.append(cum_factor)
    total_return = (cum_factor - 1) * 100 if port_daily_returns else 0.0
    nav_series = [v * 100 for v in cum_series]  # index series for drawdown, not real dollars

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
    Win rate: % of SELL trades that were actually profitable, using real
    FIFO-matched P&L per sell (mirrors _compute_realized_pnl_fifo in
    routers/portfolio.py, but tracked per-sell instead of summed).

    The previous version checked "net_amount > 0" on the SELL row, which is
    just the cash proceeds of the sale — true for almost every sale
    regardless of whether it was profitable vs. cost basis. That's why this
    was reading ~100% almost unconditionally; it wasn't measuring wins, it
    was measuring "did selling shares generate positive cash" (always yes).
    """
    try:
        if account_id:
            rows = await pool.fetch(
                "SELECT symbol, side, quantity, price, commission, trade_date FROM trades "
                "WHERE account_id = $1 ORDER BY trade_date, id",
                account_id,
            )
        else:
            rows = await pool.fetch(
                "SELECT symbol, side, quantity, price, commission, trade_date FROM trades "
                "ORDER BY trade_date, id",
            )

        lots: dict[str, list[list[float]]] = defaultdict(list)
        wins = 0
        total_sells = 0

        for row in rows:
            sym = row["symbol"].upper()
            qty = float(row["quantity"])
            price = float(row["price"])
            commission = float(row["commission"] or 0)
            td = row["trade_date"].date() if hasattr(row["trade_date"], "date") else row["trade_date"]

            if row["side"] == "BUY":
                cost_per_share = (qty * price + commission) / qty if qty else 0
                lots[sym].append([qty, cost_per_share])
                continue

            remaining = qty
            sell_pnl = 0.0
            while remaining > 0.0001 and lots[sym]:
                lot_qty, lot_cost = lots[sym][0]
                take = min(remaining, lot_qty)
                proceeds = take * price - (commission * take / qty if qty else 0)
                cost = take * lot_cost
                sell_pnl += proceeds - cost
                lot_qty -= take
                remaining -= take
                if lot_qty <= 0.0001:
                    lots[sym].pop(0)
                else:
                    lots[sym][0][0] = lot_qty

            if start <= td <= end:
                total_sells += 1
                if sell_pnl > 0:
                    wins += 1

        if total_sells > 0:
            return wins / total_sells * 100
    except Exception as exc:
        logger.error("Win rate calculation failed: %s", exc)
    return None
