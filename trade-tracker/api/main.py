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

import config
import db
from routers import ibkr, imports, market, portfolio, trades

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
if config.FRONTEND_URL and config.FRONTEND_URL not in _allowed_origins:
    _allowed_origins.append(config.FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(imports.router)
app.include_router(market.router)
app.include_router(ibkr.router)


async def _auto_backfill():
    """
    On startup: if we have trades but missing/stale snapshots, run backfill automatically.
    Waits 15s for DB + IBKR to settle before starting.
    """
    await asyncio.sleep(15)
    try:
        pool = db.get_pool()
        trade_count = await pool.fetchval("SELECT COUNT(*) FROM trades")
        if not trade_count:
            logger.info("Auto-backfill: no trades yet, skipping")
            return

        latest_snap = await pool.fetchval(
            "SELECT MAX(snapshot_date) FROM portfolio_snapshots WHERE account_id IS NULL"
        )
        today = date.today()
        needs_backfill = (latest_snap is None) or ((today - latest_snap).days > 1)

        if needs_backfill:
            logger.info("Auto-backfill: snapshots missing or stale (latest=%s), running now...", latest_snap)
            from services.portfolio_metrics import backfill_snapshots
            result = await backfill_snapshots(pool)
            logger.info("Auto-backfill complete: %s", result)
        else:
            logger.info("Auto-backfill: snapshots are current (latest=%s), skipping", latest_snap)
    except Exception as exc:
        logger.error("Auto-backfill failed: %s", exc)


async def _hourly_snapshot_loop():
    """
    Every hour: generate today's snapshot so the dashboard stays current.
    Uses live IBKR prices when connected, yfinance otherwise.
    """
    await asyncio.sleep(60)  # Let startup settle first
    while True:
        try:
            pool = db.get_pool()
            trade_count = await pool.fetchval("SELECT COUNT(*) FROM trades")
            if trade_count:
                from services.portfolio_metrics import backfill_snapshots
                # Only backfill the last 2 days to keep it fast
                from datetime import timedelta
                start = date.today() - timedelta(days=2)
                await backfill_snapshots(pool, start_date=start)
                logger.info("Hourly snapshot refresh complete")
        except Exception as exc:
            logger.error("Hourly snapshot loop error: %s", exc)
        await asyncio.sleep(3600)  # 1 hour


@app.on_event("startup")
async def startup():
    await db.init_pool()
    logger.info("Trade Tracker API started")

    if config.IBKR_ENABLED:
        import threading
        from services.ibkr_client import ibkr_client
        t = threading.Thread(target=ibkr_client.connect, daemon=True)
        t.start()
        logger.info("IBKR connection starting (client_id=%s)", config.IBKR_CLIENT_ID)
    else:
        logger.info("IBKR disabled — using yfinance. Set IBKR_ENABLED=true to activate.")

    # Auto-backfill and hourly refresh run in background
    asyncio.create_task(_auto_backfill())
    asyncio.create_task(_hourly_snapshot_loop())


@app.on_event("shutdown")
async def shutdown():
    if config.IBKR_ENABLED:
        from services.ibkr_client import ibkr_client
        ibkr_client.disconnect()
    await db.close_pool()
    logger.info("Trade Tracker API stopped")


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
