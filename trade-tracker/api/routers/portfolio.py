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
    Derive current positions.
    When IBKR is connected and account_id matches the IBKR account, uses live
    IBKR position data (accurate prices, cost basis from IB).
    Otherwise falls back to computing from trades × IBKR snapshot prices.
    """
    # --- IBKR live path ---
    if (
        config.IBKR_ENABLED
        and account_id
        and account_id == config.IBKR_ACCOUNT_ID
    ):
        from services.ibkr_client import ibkr_client
        if ibkr_client.is_connected():
            try:
                raw = ibkr_client.get_positions(account_id)
                positions: list[PositionSummary] = []
                for p in raw:
                    qty = p.get("position", 0)
                    if not qty or abs(qty) < 0.00001:
                        continue
                    symbol = (p.get("ticker") or p.get("contractDesc") or "").upper().strip()
                    if not symbol:
                        continue
                    avg_cost = p.get("avgCost")
                    mkt_price = p.get("mktPrice")
                    mkt_value = p.get("mktValue")
                    unreal = p.get("unrealizedPnl")

                    qty_d = Decimal(str(qty))
                    avg_d = Decimal(str(avg_cost)).quantize(Decimal("0.0001")) if avg_cost else None
                    price_d = Decimal(str(mkt_price)).quantize(Decimal("0.0001")) if mkt_price else None
                    mktval_d = Decimal(str(mkt_value)).quantize(Decimal("0.01")) if mkt_value else None
                    unreal_d = Decimal(str(unreal)).quantize(Decimal("0.01")) if unreal is not None else None

                    cost_basis = (qty_d * avg_d).quantize(Decimal("0.01")) if avg_d else None
                    unreal_pct = None
                    if unreal_d is not None and cost_basis and cost_basis != 0:
                        unreal_pct = (unreal_d / abs(cost_basis) * 100).quantize(Decimal("0.0001"))

                    # pull label from trades table
                    label_row = await pool.fetchrow(
                        "SELECT MAX(label) AS label FROM trades WHERE account_id=$1 AND symbol=$2",
                        account_id, symbol,
                    )
                    positions.append(PositionSummary(
                        symbol=symbol,
                        account_id=account_id,
                        quantity=qty_d,
                        avg_cost=avg_d,
                        current_price=price_d,
                        market_value=mktval_d,
                        unrealized_pnl=unreal_d,
                        unrealized_pnl_pct=unreal_pct,
                        label=label_row["label"] if label_row else None,
                    ))
                return positions
            except Exception as exc:
                logger.warning("IBKR live positions failed, falling back to trades: %s", exc)

    # --- Imported position snapshot path (Fidelity positions file) ---
    # When a Fidelity positions CSV was imported, use the numbers straight from
    # the file — last_price, current_value, total_gain_loss — instead of
    # recomputing from trades × live prices (which needs IBKR market data).
    snap_params = [account_id] if account_id else []
    snap_rows = await pool.fetch(
        f"""
        SELECT ip.symbol, ip.account_id, ip.quantity, ip.last_price,
               ip.current_value, ip.total_gain_loss, ip.total_gl_pct,
               ip.cost_basis_total, ip.avg_cost,
               t.label
        FROM imported_positions ip
        LEFT JOIN LATERAL (
            SELECT MAX(label) AS label
            FROM trades
            WHERE account_id = ip.account_id AND symbol = ip.symbol
        ) t ON TRUE
        {"WHERE ip.account_id = $1" if account_id else ""}
        ORDER BY ip.symbol
        """,
        *snap_params,
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

    # --- Trades + IBKR price fallback ---
    params = [account_id] if account_id else []
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

    all_symbols = [row["symbol"] for row in rows]
    await market_data.warm_quote_cache(pool, all_symbols)

    positions: list[PositionSummary] = []
    for row in rows:
        qty = Decimal(str(row["net_qty"]))
        avg_cost = Decimal(str(row["avg_cost"])) if row["avg_cost"] else None

        quote = await market_data.get_quote(pool, symbol)
        current_price = quote.price if quote else None

        market_value = (qty * current_price).quantize(Decimal("0.01")) if current_price else None
        cost_basis = (qty * avg_cost).quantize(Decimal("0.01")) if avg_cost else None

        unrealized_pnl = None
        unrealized_pnl_pct = None
        if market_value is not None and cost_basis is not None and cost_basis != 0:
            unrealized_pnl = (market_value - cost_basis).quantize(Decimal("0.01"))
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100).quantize(Decimal("0.0001"))

        positions.append(PositionSummary(
            symbol=symbol,
            account_id=row["account_id"],
            quantity=qty,
            avg_cost=avg_cost,
            current_price=current_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            label=row["label"],
        ))
    return positions


async def _account_summary(pool, account_id: str) -> AccountSummary:
    # --- IBKR live account data path ---
    if (
        config.IBKR_ENABLED
        and account_id == config.IBKR_ACCOUNT_ID
    ):
        from services.ibkr_client import ibkr_client
        if ibkr_client.is_connected():
            try:
                # Use /ledger for accurate cash, NAV, unrealized P&L
                ledger = ibkr_client.get_ledger(account_id)
                base = (ledger or {}).get("BASE") or (ledger or {}).get("USD") or {}

                def _f(key: str) -> Optional[Decimal]:
                    v = base.get(key)
                    return Decimal(str(round(float(v), 2))) if v is not None else None

                positions = await _compute_positions(pool, account_id)
                cash_balance  = _f("cashbalance")
                total_nav     = _f("netliquidationvalue")
                equity_value  = _f("stockmarketvalue") or sum(
                    (p.market_value or Decimal(0)) for p in positions
                )
                unrealized    = _f("unrealizedpnl") or sum(
                    (p.unrealized_pnl or Decimal(0)) for p in positions
                )

                # Realized P&L from trade history (IB's realizedpnl resets each day)
                realized_row = await pool.fetchrow(
                    """
                    WITH by_symbol AS (
                        SELECT symbol,
                            SUM(CASE WHEN side='SELL' THEN net_amount  ELSE 0 END) AS sell_proceeds,
                            SUM(CASE WHEN side='SELL' THEN quantity     ELSE 0 END) AS sold_qty,
                            NULLIF(SUM(CASE WHEN side='BUY' THEN quantity ELSE 0 END),0) AS total_buy_qty,
                            SUM(CASE WHEN side='BUY' THEN gross_amount+commission ELSE 0 END) AS total_buy_cost
                        FROM trades WHERE account_id=$1 GROUP BY symbol
                        HAVING SUM(CASE WHEN side='SELL' THEN quantity ELSE 0 END) > 0
                    )
                    SELECT COALESCE(SUM(sell_proceeds - sold_qty*(total_buy_cost/total_buy_qty)),0) AS realized_pnl
                    FROM by_symbol WHERE total_buy_qty IS NOT NULL
                    """,
                    account_id,
                )
                realized = Decimal(str(realized_row["realized_pnl"])).quantize(Decimal("0.01"))

                snap_row = await pool.fetchrow(
                    "SELECT daily_pnl, daily_pnl_pct FROM portfolio_snapshots "
                    "WHERE account_id=$1 ORDER BY snapshot_date DESC LIMIT 1",
                    account_id,
                )
                source_row = await pool.fetchrow(
                    "SELECT source FROM trades WHERE account_id=$1 LIMIT 1", account_id
                )
                return AccountSummary(
                    account_id=account_id,
                    source=source_row["source"] if source_row else "ibkr",
                    total_nav=total_nav,
                    cash_balance=cash_balance,
                    equity_value=equity_value,
                    day_pnl=Decimal(str(snap_row["daily_pnl"])) if snap_row and snap_row["daily_pnl"] else None,
                    day_pnl_pct=Decimal(str(snap_row["daily_pnl_pct"])) if snap_row and snap_row["daily_pnl_pct"] else None,
                    total_realized_pnl=realized,
                    total_unrealized_pnl=unrealized,
                )
            except Exception as exc:
                logger.warning("IBKR live account summary failed, falling back: %s", exc)

    # --- Imported positions snapshot path (Fidelity) ---
    # If a Fidelity positions file was imported, sum from imported_positions
    # for perfectly accurate equity_value and unrealized PnL.
    snap_total = await pool.fetchrow(
        """
        SELECT SUM(current_value) AS equity_value,
               SUM(total_gain_loss) AS unrealized_pnl
        FROM imported_positions
        WHERE account_id = $1
        """,
        account_id,
    )
    realized = Decimal(str(realized_row["realized_pnl"])).quantize(Decimal("0.01"))

    source_row = await pool.fetchrow(
        "SELECT source FROM trades WHERE account_id=$1 LIMIT 1", account_id
    )

    if snap_total and snap_total["equity_value"] is not None:
        equity_value = Decimal(str(snap_total["equity_value"])).quantize(Decimal("0.01"))
        unrealized_pnl = Decimal(str(snap_total["unrealized_pnl"] or 0)).quantize(Decimal("0.01"))
    else:
        # --- Trades + IBKR price fallback ---
        positions = await _compute_positions(pool, account_id)
        equity_value = sum((p.market_value or Decimal(0)) for p in positions)
        unrealized_pnl = sum((p.unrealized_pnl or Decimal(0)) for p in positions)

    # Realized P&L from trade history
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

    # Latest snapshot for today's P&L
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

        # Pre-warm price cache for ALL symbols across all accounts in one batch call.
        # Without this, _compute_positions fetches each symbol individually (~1s each).
        symbol_rows = await pool.fetch("SELECT DISTINCT symbol FROM trades")
        await market_data.warm_quote_cache(pool, [r["symbol"] for r in symbol_rows])

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
    """
    Current open positions with live P&L.
    Quantities are netted from trade history (BUY - SELL).
    Prices fetched from IBKR.
    """
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


@router.post("/snapshots/backfill", tags=["portfolio"])
async def backfill_snapshots_endpoint(
    background_tasks,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    pool=Depends(get_pool),
):
    """
    Rebuild historical NAV snapshots from trade history + IBKR historical bars.
    Returns immediately — runs in background (~30s for 2 years of history).
    Safe to re-run: ON CONFLICT DO UPDATE.
    """
    background_tasks.add_task(portfolio_metrics.backfill_snapshots, pool, start_date, end_date)
    return {"status": "started", "message": "Backfill running in background. Performance graph updates in ~30s."}


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

            # If IBKR connected, pull live NAV instead of equity-only
            if config.IBKR_ENABLED and acct_id == config.IBKR_ACCOUNT_ID:
                from services.ibkr_client import ibkr_client as _ibkr
                if _ibkr.is_connected():
                    try:
                        acct_data = _ibkr.get_account_summary(acct_id)
                        if acct_data:
                            live_nav = acct_data.get("netliquidation", {}).get("amount")
                            if live_nav:
                                nav = Decimal(str(round(live_nav, 2)))
                                equity = nav
                    except Exception as _e:
                        logger.warning("Could not fetch live NAV from IBKR: %s", _e)

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
