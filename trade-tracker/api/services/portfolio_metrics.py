"""
Portfolio metrics calculations.

Provides:
- NAV-based return series from portfolio_snapshots
- Beta (portfolio vs configured benchmark) for supported periods
- Annualized standard deviation
- Sharpe ratio (risk-free rate = 0 for simplicity; can be updated)
- Alpha, max drawdown, win rate
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
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


def _beta(portfolio_returns: list[float], benchmark_returns: list[float]) -> Optional[float]:
    """
    Beta is the regression slope of portfolio returns on benchmark returns.

    This is equivalent to Excel SLOPE(portfolio_returns, benchmark_returns)
    and to covariance(portfolio, benchmark) / variance(benchmark), but using
    the standard-library regression helper makes the intended calculation
    explicit.
    """
    if len(portfolio_returns) != len(benchmark_returns) or len(portfolio_returns) < 2:
        return None
    if not all(math.isfinite(value) for value in portfolio_returns + benchmark_returns):
        return None
    try:
        regression = statistics.linear_regression(benchmark_returns, portfolio_returns)
    except statistics.StatisticsError:
        return None
    return regression.slope if math.isfinite(regression.slope) else None


def _paired_beta_returns(
    points: list[PerformancePoint],
) -> tuple[list[float], list[float]]:
    """
    Return date-aligned portfolio/benchmark daily returns for beta.

    Missing benchmark values are common around market holidays or data outages.
    Pairing values from the same performance point prevents returns from
    different dates from being regressed against one another.
    """
    portfolio_returns: list[float] = []
    benchmark_returns: list[float] = []
    for point in points:
        if point.portfolio_pct_change is None or point.spy_pct_change is None:
            continue
        portfolio_return = float(point.portfolio_pct_change) / 100
        benchmark_return = float(point.spy_pct_change) / 100
        if not math.isfinite(portfolio_return) or not math.isfinite(benchmark_return):
            continue
        portfolio_returns.append(portfolio_return)
        benchmark_returns.append(benchmark_return)
    return portfolio_returns, benchmark_returns


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
    Also fetches the benchmark close and stores it in the legacy spy_* columns.
    """
    benchmark_symbol = config.BENCHMARK_SYMBOL
    spy_bars = get_historical_bars(benchmark_symbol, snapshot_date, snapshot_date)
    spy_close = Decimal(str(spy_bars[0].close)) if spy_bars else None

    daily_pnl: Optional[Decimal] = None
    daily_pnl_pct: Optional[Decimal] = None
    if prev_nav and prev_nav > 0:
        # Exclude deposits/withdrawals that landed on this exact day — otherwise
        # adding $5k to the account reads as a 50% gain on a $10k portfolio.
        net_flow = await _net_external_flow(pool, account_id, snapshot_date)
        daily_pnl = total_nav - prev_nav - net_flow
        daily_pnl_pct = (daily_pnl / prev_nav * 100).quantize(Decimal("0.000001"))

    # Fetch the previous benchmark close to calculate its daily return.
    spy_daily_pct: Optional[Decimal] = None
    if spy_close:
        prev_spy_bars = get_historical_bars(
            benchmark_symbol,
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

async def _has_trades(pool: asyncpg.Pool, account_id: str) -> bool:
    row = await pool.fetchrow(
        "SELECT 1 FROM trades WHERE account_id = $1 LIMIT 1", account_id
    )
    return row is not None


# Always pull YTD or 1Y from PA, then window with prior-close baseline.
# Native 1M/6M/3M codes use IBKR's rolling windows and don't match calendar
# period pickers (and 6M/3M often 400).
_IBKR_PA_SOURCE = {
    "ytd": "YTD",
    "1y": "1Y",
    "1m": "1Y",
    "6m": "1Y",
    "3m": "1Y",
}


def _is_ibkr_account(account_id: Optional[str]) -> bool:
    ibkr = (config.IBKR_ACCOUNT_ID or "").strip()
    return bool(account_id and ibkr and account_id == ibkr)


async def get_performance_series(
    pool: asyncpg.Pool,
    start: date,
    end: date,
    account_id: Optional[str] = None,
    period: Optional[str] = None,
) -> list[PerformancePoint]:
    # IBKR only: Portfolio Analyst TWR (real deposits/withdrawals). Never run
    # PA for Fidelity/other accounts — they use trade-replay TWR below with the
    # same prior-close windowing.
    if (
        _is_ibkr_account(account_id)
        and config.IBKR_ENABLED
        and period
    ):
        loop = asyncio.get_event_loop()
        pa_pts = await loop.run_in_executor(
            None, _ibkr_pa_performance_series, account_id, period.lower(), start, end
        )
        if pa_pts:
            return pa_pts

    # Fidelity (and IBKR fallback): trade replay TWR + cash_flows / implicit
    # deposits, prior-close baseline matching PA period windows.
    if account_id and await _has_trades(pool, account_id):
        trade_pts = await _trades_performance_series(pool, account_id, start, end)
        if trade_pts:
            return trade_pts

    rows = await _fetch_snapshots(pool, start, end, account_id)
    # Only trust snapshots when they cover the requested window (main's
    # coverage check). Otherwise prefer IBKR/trade replay below.
    if len(rows) >= 2 and (rows[0]["snapshot_date"] - start) <= timedelta(days=3):
        return _performance_from_snapshots(rows)

    if (
        config.IBKR_ENABLED
        and config.IBKR_ACCOUNT_ID
        and (not account_id or account_id == config.IBKR_ACCOUNT_ID)
        and not (account_id and await _has_trades(pool, account_id))
    ):
        loop = asyncio.get_event_loop()
        ibkr_pts = await loop.run_in_executor(None, _ibkr_performance_series, start, end)
        if ibkr_pts:
            return ibkr_pts

    # Nothing richer available — fall back to whatever real snapshot rows
    # exist, even if they don't fully cover the requested window.
    if rows:
        return _performance_from_snapshots(rows)

    return []


def _apply_trade_cash(
    cash: float,
    side: str,
    qty: float,
    price: float,
    commission: float,
) -> tuple[float, float]:
    """
    Apply one trade to cash. Returns (new_cash, implicit_external_flow).

    IBKR/Fidelity trade sync often has buys/sells but no deposit rows in
    cash_flows. Without funding, cash goes deeply negative and TWR explodes
    (e.g. +294% on a ~$20k account). Treat any cash shortfall on a BUY as an
    implicit deposit (excluded from return). Treat cash that would go more
    negative on a SELL covering a short the same way only for the buy side —
    sells just add proceeds.
    """
    if side == "BUY":
        cost = qty * price + commission
        if cash >= cost:
            return cash - cost, 0.0
        # Fund the shortfall as new capital entering the account.
        shortfall = cost - max(cash, 0.0)
        return 0.0, shortfall
    # SELL
    return cash + qty * price - commission, 0.0


def _mark_equity(
    holdings: dict[str, float],
    closes: dict[str, dict[date, float]],
    last_close: dict[str, float],
    d: date,
) -> float:
    """Mark holdings to market; forward-fill last known close; include shorts."""
    equity = 0.0
    for sym, qty in holdings.items():
        if abs(qty) < 0.00001:
            continue
        px = closes.get(sym, {}).get(d)
        if px is not None:
            last_close[sym] = px
        else:
            px = last_close.get(sym)
        if px is None:
            continue
        equity += qty * px
    return equity


async def _trades_performance_series(
    pool: asyncpg.Pool,
    account_id: str,
    start: date,
    end: date,
) -> list[PerformancePoint]:
    """
    Replay synced trades → daily holdings, mark-to-market, time-weighted return.

    Used for Fidelity (primary) and as IBKR fallback when PA is unavailable.
    External cash (recorded cash_flows + implicit funding when a BUY would
    otherwise drive cash negative) is excluded from daily return so deposits
    don't look like investment gains. Period windows use a prior-close
    baseline — same convention as IBKR PA period pickers.
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

    # Lot-only sheets (all BUYs, no SELLs, no cash ledger): value equity only
    # and treat each buy cost as an implicit deposit.
    has_sells = any(r["side"] == "SELL" for r in rows)
    lot_only = not has_sells

    symbols = sorted({r["symbol"].upper() for r in rows})
    loop = asyncio.get_event_loop()
    spy_bars = await loop.run_in_executor(None, get_spy_history, start, end)
    if not spy_bars:
        logger.warning("No SPY history for performance series")
        return []

    # Need history from first trade (may be before `start`) so holdings are
    # correct at the period open — but only emit points inside [start, end].
    first_trade = rows[0]["trade_date"]
    first_trade_d = first_trade.date() if hasattr(first_trade, "date") else first_trade
    hist_start = min(start, first_trade_d)

    batch = await loop.run_in_executor(
        None, get_historical_bars_batch, symbols, hist_start, end
    )
    closes: dict[str, dict[date, float]] = {
        sym: {b.date: float(b.close) for b in batch.get(sym, [])}
        for sym in symbols
    }
    flows_by_date = await _cash_flows_by_date(pool, account_id, hist_start, end)

    # Replay calendar = SPY bars from hist_start (trading days only)
    all_spy = await loop.run_in_executor(None, get_spy_history, hist_start, end)
    if not all_spy:
        all_spy = spy_bars

    holdings: dict[str, float] = defaultdict(float)
    cash = 0.0
    trade_idx = 0
    points: list[PerformancePoint] = []
    last_close: dict[str, float] = {}
    prev_port: Optional[float] = None
    prev_spy: Optional[float] = None
    cum_factor = 1.0
    spy_base: Optional[float] = None

    for bar in all_spy:
        d = bar.date
        flow_today = 0.0

        while trade_idx < len(rows):
            t = rows[trade_idx]
            td = t["trade_date"].date() if hasattr(t["trade_date"], "date") else t["trade_date"]
            if td > d:
                break
            sym = t["symbol"].upper()
            qty = float(t["quantity"])
            price = float(t["price"])
            commission = float(t["commission"] or 0)
            if lot_only:
                if t["side"] == "BUY":
                    flow_today += qty * price + commission
                    holdings[sym] += qty
                else:
                    holdings[sym] -= qty
            else:
                if t["side"] == "BUY":
                    cash, implicit = _apply_trade_cash(cash, "BUY", qty, price, commission)
                    flow_today += implicit
                    holdings[sym] += qty
                else:
                    cash, _ = _apply_trade_cash(cash, "SELL", qty, price, commission)
                    holdings[sym] -= qty
            trade_idx += 1

        recorded = flows_by_date.get(d, 0.0)
        if not lot_only:
            cash += recorded
        flow_today += recorded

        equity = _mark_equity(holdings, closes, last_close, d)
        port_val = equity if lot_only else cash + equity
        if port_val <= 0:
            continue

        # Warm holdings/cash/prev before the window; do not publish yet.
        if d < start:
            prev_port = port_val
            prev_spy = float(bar.close)
            continue

        if spy_base is None:
            # Prior-close baseline (same as IBKR PA period windows): first
            # session in [start, end] earns return vs the last NAV before
            # `start`, not a forced 0% open that drops that day's move.
            spy_base = float(bar.close)
            if prev_port and prev_port > 0:
                daily_pct = (port_val - prev_port - flow_today) / prev_port * 100
                daily_pct = max(-50.0, min(50.0, daily_pct))
                cum_factor = 1 + daily_pct / 100
                port_cum = (cum_factor - 1) * 100
                spy_daily_pct = (
                    (float(bar.close) - prev_spy) / prev_spy * 100 if prev_spy else None
                )
            else:
                cum_factor = 1.0
                daily_pct = None
                port_cum = 0.0
                spy_daily_pct = None
            spy_cum = 0.0
        else:
            # TWR: r_t = (V_t - V_{t-1} - flow_t) / V_{t-1}
            # When flow funds a buy, V also rises by ~flow, so net return ≈ 0
            # that day aside from price moves — which is what we want.
            if prev_port and prev_port > 0:
                daily_pct = (port_val - prev_port - flow_today) / prev_port * 100
            else:
                daily_pct = None
            spy_daily_pct = (
                (float(bar.close) - prev_spy) / prev_spy * 100 if prev_spy else None
            )
            if daily_pct is not None:
                # Clamp absurd single-day moves from bad marks / missing prices
                # rather than letting one day dominate the chain.
                daily_pct = max(-50.0, min(50.0, daily_pct))
                cum_factor *= 1 + daily_pct / 100
            port_cum = (cum_factor - 1) * 100
            spy_cum = (float(bar.close) - spy_base) / spy_base * 100

        prev_port = port_val
        prev_spy = float(bar.close)

        points.append(
            PerformancePoint(
                date=d,
                portfolio_nav=Decimal(str(round(port_val, 2))),
                portfolio_pct_change=(
                    Decimal(str(round(daily_pct, 6))) if daily_pct is not None else None
                ),
                spy_pct_change=(
                    Decimal(str(round(spy_daily_pct, 6))) if spy_daily_pct is not None else None
                ),
                spy_cumulative_pct=Decimal(str(round(spy_cum, 4))),
                portfolio_cumulative_pct=Decimal(str(round(port_cum, 4))),
            )
        )

    logger.info(
        "Built %d TWR performance points from trade replay for %s (lot_only=%s)",
        len(points), account_id, lot_only,
    )
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


def _pa_window_indices(
    dates: list[date],
    start_label: date,
    end: date,
) -> Optional[tuple[int, int]]:
    """
    (first_idx, end_idx) for a period window.

    TWR uses prior-close baseline: return from close of dates[first-1]
    (or 0 if first==0) through close of dates[end_idx]. That matches
    broker period pickers (6/10–7/10 baselines off 6/09 close).
    """
    if not dates:
        return None
    end_idxs = [i for i, d in enumerate(dates) if d <= end]
    if not end_idxs:
        return None
    end_idx = end_idxs[-1]
    first = next((i for i, d in enumerate(dates) if d >= start_label and i <= end_idx), None)
    if first is None:
        return None
    return first, end_idx


def _ibkr_pa_fetch(account_id: str, period: str) -> Optional[dict]:
    """Fetch a PA series long enough to window the requested period."""
    from services.ibkr_client import ibkr_client

    if not ibkr_client.is_connected:
        return None
    code = _IBKR_PA_SOURCE.get(period, "1Y")
    return ibkr_client.get_pa_performance(account_id, code)


def _ibkr_pa_performance_series(
    account_id: str,
    period: str,
    start: date,
    end: date,
) -> list[PerformancePoint]:
    """
    Build chart/metrics points from IBKR Portfolio Analyst TWR + NAV.

    Windows the PA series to [start, end] with a prior-close baseline so
    calendar 1M/3M/6M/YTD match the broker period-picker numbers.
    """
    raw = _ibkr_pa_fetch(account_id, period)
    if not raw:
        return []

    dates: list[date] = raw["dates"]
    cum: list[float] = raw["cumulative_returns"]
    navs: Optional[list[float]] = raw.get("navs")
    if not dates or len(dates) != len(cum):
        return []

    as_of = min(end, dates[-1])
    win = _pa_window_indices(dates, start, as_of)
    if win is None:
        return []
    first, end_idx = win
    base_c = 0.0 if first == 0 else cum[first - 1]
    if base_c <= -1:
        return []

    def _rebased(i: int) -> float:
        return (1 + cum[i]) / (1 + base_c) - 1

    spy_bars = get_spy_history(dates[first], dates[end_idx])
    spy_by_d = {b.date: float(b.close) for b in spy_bars}
    spy_ff: dict[date, float] = {}
    last_px: Optional[float] = None
    for d in dates[first : end_idx + 1]:
        if d in spy_by_d:
            last_px = spy_by_d[d]
        if last_px is not None:
            spy_ff[d] = last_px

    spy_base = next((spy_ff[d] for d in dates[first : end_idx + 1] if d in spy_ff), None)
    prev_spy: Optional[float] = None
    points: list[PerformancePoint] = []

    for i in range(first, end_idx + 1):
        d = dates[i]
        port_cum = _rebased(i) * 100
        if i == first:
            daily_pct = None if first == 0 else port_cum
        else:
            prev_r, cur_r = _rebased(i - 1), _rebased(i)
            daily_pct = ((1 + cur_r) / (1 + prev_r) - 1) * 100 if prev_r > -1 else None

        spy_px = spy_ff.get(d)
        spy_daily = None
        spy_cum_pct = None
        if spy_px is not None and spy_base:
            if prev_spy:
                spy_daily = (spy_px - prev_spy) / prev_spy * 100
            spy_cum_pct = (spy_px - spy_base) / spy_base * 100
            prev_spy = spy_px

        nav = navs[i] if navs else None
        points.append(
            PerformancePoint(
                date=d,
                portfolio_nav=Decimal(str(round(nav, 2))) if nav is not None else Decimal("0"),
                portfolio_pct_change=(
                    Decimal(str(round(daily_pct, 6))) if daily_pct is not None else None
                ),
                spy_pct_change=(
                    Decimal(str(round(spy_daily, 6))) if spy_daily is not None else None
                ),
                spy_cumulative_pct=(
                    Decimal(str(round(spy_cum_pct, 4))) if spy_cum_pct is not None else None
                ),
                portfolio_cumulative_pct=Decimal(str(round(port_cum, 4))),
            )
        )

    logger.info(
        "Built %d points from IBKR PA (%s) for %s — last TWR=%.2f%% (%s→%s)",
        len(points), period, account_id,
        float(points[-1].portfolio_cumulative_pct or 0),
        points[0].date, points[-1].date,
    )
    return points


# ---------------------------------------------------------------------------
# Metrics calculation
# ---------------------------------------------------------------------------

def _period_bounds(period: str) -> tuple[date, date]:
    today = date.today()
    p = (period or "ytd").lower()
    if p == "ytd":
        start = date(today.year, 1, 1)
    elif p == "1y":
        start = today - timedelta(days=365)
    elif p == "6m":
        start = today - timedelta(days=182)
    elif p == "3m":
        start = today - timedelta(days=91)
    elif p == "1m":
        start = today - timedelta(days=30)
    else:
        start = date(today.year, 1, 1)  # default ytd
    return start, today


def _blank_metrics(period: str) -> PortfolioMetrics:
    return PortfolioMetrics(
        period=period, benchmark_symbol=config.BENCHMARK_SYMBOL,
        beta=None, beta_observations=0,
        std_dev_annualized=None, sharpe_ratio=None,
        total_return_pct=None, spy_return_pct=None, alpha=None,
        max_drawdown_pct=None, win_rate=None, as_of=datetime.utcnow(),
    )


async def _metrics_from_points(
    pool: asyncpg.Pool,
    account_id: Optional[str],
    period: str,
    points: list[PerformancePoint],
) -> PortfolioMetrics:
    """
    Compute the full metric set (beta, std dev, sharpe, alpha, max drawdown,
    win rate, and total/benchmark return from a PerformancePoint series —
    the same series the performance graph already builds via IBKR-live or
    trade replay. Shared by both fallbacks below so "the graph has the data,
    why are the other numbers blank" isn't true anymore once there's enough
    replayed history, even without 2+ stored snapshot rows.
    """
    if not points:
        return _blank_metrics(period)

    start, end = _period_bounds(period)
    port_daily = [
        float(point.portfolio_pct_change) / 100
        for point in points
        if point.portfolio_pct_change is not None
    ]
    nav_series = [float(p.portfolio_nav) for p in points]

    paired_portfolio_returns, paired_benchmark_returns = _paired_beta_returns(points)
    beta_val = _beta(paired_portfolio_returns, paired_benchmark_returns)


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
        benchmark_symbol=config.BENCHMARK_SYMBOL,
        beta=_dec(beta_val),
        beta_observations=len(paired_portfolio_returns),
        std_dev_annualized=_dec(std_dev_annual),
        sharpe_ratio=_dec(sharpe),
        total_return_pct=_dec(total_return),
        spy_return_pct=_dec(spy_return),
        alpha=_dec(alpha),
        max_drawdown_pct=_dec(max_dd),
        win_rate=_dec(win_rate_val),
        as_of=datetime.utcnow(),
    )



async def calculate_metrics(
    pool: asyncpg.Pool,
    period: str = "ytd",
    account_id: Optional[str] = None,
) -> PortfolioMetrics:
    """Metrics from the same PerformancePoint series the chart uses."""
    start, end = _period_bounds(period)
    points = await get_performance_series(pool, start, end, account_id, period=period)
    if points:
        return await _metrics_from_points(pool, account_id, period, points)
    return _blank_metrics(period)



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
