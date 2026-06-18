"""
Trade Tracker API - Entry point.
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import db
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
    description=(
        "Backend API for the DeKalb hedge fund trade tracker. "
        "Tracks trades from IBKR and Fidelity, calculates portfolio metrics "
        "(beta, std dev, NAV), and serves data to the frontend dashboard."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow the frontend (React dev server on :3000 and production on :80)
# NOTE: allow_credentials=True requires explicit origins (not "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:80", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(imports.router)
app.include_router(market.router)
app.include_router(ibkr.router)


@app.on_event("startup")
async def startup():
    await db.init_pool()
    logger.info("Trade Tracker API started. Docs at /docs")
    config.validate_ibkr_oauth_config(logger)
    if config.IBKR_ENABLED:
        if config.IBKR_USE_OAUTH:
            ok = await ibkr_client.connect_oauth()
            mode = "oauth" if ok else "oauth (failed — check logs)"
            logger.info("IBKR ENABLED — %s at %s (account: %s)", mode, config.IBKR_API_BASE_URL, config.IBKR_ACCOUNT_ID)
            if ok:
                asyncio.create_task(_startup_ibkr_trade_sync())
        else:
            logger.info("IBKR ENABLED — gateway at %s (account: %s)", config.IBKR_GATEWAY_URL, config.IBKR_ACCOUNT_ID)
    else:
        logger.info("IBKR DISABLED — using yfinance for market data. Set IBKR_ENABLED=true to activate.")


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
    logger.info("Trade Tracker API stopped")


@app.get("/health", tags=["health"])
async def health():
    """Health check endpoint."""
    pool = db.get_pool()
    try:
        await pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "ibkr": "enabled" if config.IBKR_ENABLED else "disabled (yfinance fallback)",
        "version": "0.1.0",
    }
