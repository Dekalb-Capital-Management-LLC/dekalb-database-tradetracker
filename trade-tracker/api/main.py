import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import config, db
from routers import ibkr, imports, market, portfolio, trades
from routers import auth as auth_router
from services.auth import AuthError, verify_google_id_token

logging.basicConfig(level=logging.DEBUG if config.DEBUG else logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="DeKalb Trade Tracker API", version="0.1.0", docs_url="/docs", redoc_url="/redoc")

_cors_origins = ["http://localhost:3000", "http://localhost:80", "http://localhost"]
_cors_origins += [origin.strip() for origin in config.FRONTEND_URL.split(",") if origin.strip()]

app.add_middleware(CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

_BYPASS = ("/health", "/docs", "/redoc", "/openapi.json", "/auth/")

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not config.AUTH_ENABLED or request.method == "OPTIONS":
            return await call_next(request)
        if any(request.url.path.startswith(p) for p in _BYPASS):
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing Authorization header"})
        try:
            claims = verify_google_id_token(auth_header.removeprefix("Bearer ").strip())
        except AuthError as exc:
            return JSONResponse(status_code=401, content={"detail": str(exc)})
        request.state.user = {"email": claims.get("email"), "name": claims.get("name"),
                              "picture": claims.get("picture"), "sub": claims.get("sub")}
        return await call_next(request)

app.add_middleware(AuthMiddleware)
app.include_router(auth_router.router)
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(imports.router)
app.include_router(market.router)
app.include_router(ibkr.router)

@app.on_event("startup")
async def startup():
    await db.init_pool()
    logger.info("Trade Tracker API started. Docs at /docs")

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
    return {"status": "ok" if db_ok else "degraded", "database": "connected" if db_ok else "unreachable",
            "ibkr": "enabled" if config.IBKR_ENABLED else "disabled (yfinance fallback)", "version": "0.1.0"}
