"""
Trade Tracker API - Entry point.

Start locally:
    uvicorn main:app --reload --port 8000

In Docker:
    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
import asyncio
import logging
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import config, db
from routers import auth as auth_router
from routers import ibkr, imports, market, portfolio, trades
from routers.ibkr import sync_ibkr_trades
from services.ibkr_client import ibkr_client

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="DeKalb Trade Tracker API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

_allowed_origins = [
    "http://localhost:3000",
    "http://localhost:80",
    "http://localhost",
]
if config.FRONTEND_URL:
    for origin in config.FRONTEND_URL.split(","):
        origin = origin.strip()
        if origin and origin not in _allowed_origins:
            _allowed_origins.append(origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(imports.router)
app.include_router(market.router)
app.include_router(ibkr.router)


async def _auto_refresh_loop():
    """Refresh yfinance prices + write snapshot every 5 minutes for imported positions."""
    import yfinance as yf
    from decimal import Decimal
    from services.portfolio_metrics import upsert_snapshot

    INTERVAL = 300
    await asyncio.sleep(30)

    while True:
        try:
            pool = db.get_pool()
            rows = await pool.fetch(
                "SELECT account_id, symbol, quantity, avg_cost, cost_basis_total FROM imported_positions"
            )
            if rows:
                symbols = list({r["symbol"] for r in rows})
                CASH_SYMS = {"XXCASH", "CASH", "SPAXX", "FDRXX", "FCASH"}
                prices: dict[str, float] = {}
                cash_syms = {s for s in symbols if s.upper() in CASH_SYMS or s.upper().startswith("XX")}
                market_syms = [s for s in symbols if s not in cash_syms]
                for s in cash_syms:
                    prices[s] = 1.0
                if market_syms:
                    try:
                        df = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: yf.download(
                                " ".join(market_syms),
                                period="5d",
                                auto_adjust=True,
                                progress=False,
                                threads=True,
                            ),
                        )
                        if not df.empty:
                            close = df["Close"] if "Close" in df.columns else df.xs("Close", axis=1, level=0)
                            for sym in market_syms:
                                try:
                                    val = (
                                        float(close.dropna().iloc[-1])
                                        if len(market_syms) == 1
                                        else float(close[sym].dropna().iloc[-1])
                                    )
                                    if val > 0:
                                        prices[sym] = val
                                except Exception:
                                    pass
                    except Exception as exc:
                        logger.warning("Auto-refresh batch price fetch failed: %s", exc)

                updated = 0
                for r in rows:
                    price = prices.get(r["symbol"])
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
                        price,
                        current_value,
                        total_gl,
                        total_gl_pct,
                        r["account_id"],
                        r["symbol"],
                    )
                    updated += 1

                if updated > 0:
                    today = date.today()
                    acct_rows = await pool.fetch(
                        "SELECT DISTINCT account_id FROM imported_positions"
                    )
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
                            acct_id,
                            today,
                        )
                        await upsert_snapshot(
                            pool,
                            today,
                            nav,
                            acct_id,
                            nav,
                            Decimal(str(prev["total_nav"])) if prev else None,
                        )
                        combined_nav += nav
                    prev_comb = await pool.fetchrow(
                        """
                        SELECT total_nav FROM portfolio_snapshots
                        WHERE account_id IS NULL AND snapshot_date < $1
                        ORDER BY snapshot_date DESC LIMIT 1
                        """,
                        today,
                    )
                    await upsert_snapshot(
                        pool,
                        today,
                        combined_nav,
                        None,
                        combined_nav,
                        Decimal(str(prev_comb["total_nav"])) if prev_comb else None,
                    )
                    logger.info("Auto-refresh: updated %d positions, snapshot written", updated)
        except Exception as exc:
            logger.warning("Auto-refresh error: %s", exc)

        await asyncio.sleep(INTERVAL)


@app.on_event("startup")
async def startup():
    await db.init_pool()
    asyncio.create_task(_auto_refresh_loop())
    logger.info("Trade Tracker API started. Docs at /docs")
    config.validate_ibkr_oauth_config(logger)
    if config.IBKR_ENABLED:
        if config.IBKR_USE_OAUTH:
            ok = await ibkr_client.connect_oauth()
            mode = "oauth" if ok else "oauth (failed — check logs)"
            logger.info(
                "IBKR ENABLED — %s at %s (account: %s)",
                mode,
                config.IBKR_API_BASE_URL,
                config.IBKR_ACCOUNT_ID,
            )
            if ok:
                asyncio.create_task(_startup_ibkr_trade_sync())
        else:
            logger.info(
                "IBKR ENABLED — gateway at %s (account: %s)",
                config.IBKR_GATEWAY_URL,
                config.IBKR_ACCOUNT_ID,
            )
    else:
        logger.warning("IBKR disabled — yfinance fallback for market data.")


async def _startup_ibkr_trade_sync() -> None:
    """One-shot PA trade import after OAuth connects (non-blocking)."""
    try:
        pool = db.get_pool()
        result = await sync_ibkr_trades(pool)
        logger.info(
            "IBKR startup trade sync: inserted=%s parsed=%s symbols=%s",
            result.get("transactions_inserted"),
            result.get("transactions_parsed"),
            result.get("symbols_synced"),
        )
    except Exception as exc:
        logger.warning("IBKR startup trade sync failed: %s", exc)


@app.on_event("shutdown")
async def shutdown():
    await db.close_pool()


@app.get("/health", tags=["health"])
async def health():
    pool = db.get_pool()
    try:
        await pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    snap_date = None
    trade_count = 0
    if db_ok:
        try:
            snap_date = await pool.fetchval(
                "SELECT MAX(snapshot_date) FROM portfolio_snapshots WHERE account_id IS NULL"
            )
            trade_count = await pool.fetchval("SELECT COUNT(*) FROM trades") or 0
        except Exception:
            pass

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "ibkr": "enabled" if config.IBKR_ENABLED else "disabled (yfinance fallback)",
        "trades": trade_count,
        "latest_snapshot": str(snap_date) if snap_date else "none",
        "version": "0.1.0",
    }
