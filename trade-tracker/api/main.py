"""
Trade Tracker API - Entry point.

Start locally:
    uvicorn main:app --reload --port 8000

In Docker:
    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import config, db
from routers import ibkr, imports, market, portfolio, trades
from routers import auth as auth_router

logging.basicConfig(level=logging.DEBUG if config.DEBUG else logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
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


@app.on_event("startup")
async def startup():
    await db.init_pool()
    logger.info("Trade Tracker API started. Docs at /docs")
    if config.IBKR_ENABLED:
        import threading
        from services.ibkr_client import ibkr_client
        t = threading.Thread(target=ibkr_client.connect, daemon=True)
        t.start()
        logger.info("IBKR connection starting (client_id=%s)", config.IBKR_CLIENT_ID)
    else:
        logger.warning("IBKR disabled — no market data available. Set IBKR_ENABLED=true to activate.")




@app.on_event("shutdown")
async def shutdown():
    if config.IBKR_ENABLED:
        from services.ibkr_client import ibkr_client
        ibkr_client.disconnect()
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
        "ibkr": "enabled" if config.IBKR_ENABLED else "disabled (no market data)",
        "trades": trade_count,
        "latest_snapshot": str(snap_date) if snap_date else "none",
        "version": "0.1.0",
    }
