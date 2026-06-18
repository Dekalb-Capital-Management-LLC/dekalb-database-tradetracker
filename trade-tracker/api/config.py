import os

# PostgreSQL - equities team's isolated database (trade_tracker)
# The quant team uses the "trading" database; this service connects to "trade_tracker".
DB_HOST = os.getenv("DB_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "trade_tracker")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

# Connection pool sizing
DB_MIN_CONNECTIONS = int(os.getenv("DB_MIN_CONNECTIONS", "2"))
DB_MAX_CONNECTIONS = int(os.getenv("DB_MAX_CONNECTIONS", "10"))

# IBKR Client Portal Gateway (runs locally on your machine)
# Gateway download: https://www.interactivebrokers.com/en/trading/ib-api.php
# IBKR_GATEWAY_URL: use https://host.docker.internal:5001 when running in Docker
#                   use https://localhost:5001 when running the API locally
IBKR_GATEWAY_URL = os.getenv("IBKR_GATEWAY_URL", "https://localhost:5001")
IBKR_API_BASE_URL = os.getenv("IBKR_API_BASE_URL", "https://api.ibkr.com")
IBKR_ENABLED = os.getenv("IBKR_ENABLED", "false").lower() == "true"
IBKR_ACCOUNT_ID = os.getenv("IBKR_ACCOUNT_ID", "")
# OAuth 2.0 (RSA) — if IBKR_CLIENT_ID is set, API uses api.ibkr.com instead of the gateway
IBKR_CLIENT_ID = os.getenv("IBKR_CLIENT_ID", "")
IBKR_CLIENT_KEY_ID = os.getenv("IBKR_CLIENT_KEY_ID", "main")
IBKR_CREDENTIAL = os.getenv("IBKR_CREDENTIAL", "")
IBKR_PRIVATE_KEY = os.getenv("IBKR_PRIVATE_KEY", "")
IBKR_SERVER_IP = os.getenv("IBKR_SERVER_IP", "")
IBKR_USE_OAUTH = bool(IBKR_CLIENT_ID and IBKR_PRIVATE_KEY and IBKR_CREDENTIAL)

# yfinance / market data cache
# How long to cache price lookups (seconds) before hitting yfinance again
PRICE_CACHE_TTL_SECONDS = int(os.getenv("PRICE_CACHE_TTL_SECONDS", "60"))
HISTORICAL_CACHE_TTL_SECONDS = int(os.getenv("HISTORICAL_CACHE_TTL_SECONDS", "3600"))
# Min seconds between outbound yfinance / IBKR API calls (avoids rate limits)
YFINANCE_REQUEST_DELAY_SECONDS = float(os.getenv("YFINANCE_REQUEST_DELAY_SECONDS", "1.5"))
IBKR_REQUEST_DELAY_SECONDS = float(os.getenv("IBKR_REQUEST_DELAY_SECONDS", "0.35"))
IBKR_TX_DAYS = int(os.getenv("IBKR_TX_DAYS", "730"))
IBKR_POSITIONS_RETRY_COUNT = int(os.getenv("IBKR_POSITIONS_RETRY_COUNT", "3"))
IBKR_POSITIONS_RETRY_DELAY = float(os.getenv("IBKR_POSITIONS_RETRY_DELAY", "1.5"))
IBKR_SNAPSHOT_MAX_ATTEMPTS = int(os.getenv("IBKR_SNAPSHOT_MAX_ATTEMPTS", "8"))
IBKR_SNAPSHOT_POLL_DELAY = float(os.getenv("IBKR_SNAPSHOT_POLL_DELAY", "0.5"))

# SPY symbol for benchmark overlay
BENCHMARK_SYMBOL = os.getenv("BENCHMARK_SYMBOL", "SPY")

# FastAPI
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


def validate_ibkr_oauth_config(log) -> None:
    """Startup checks for OAuth — log only, never crash the API."""
    if not IBKR_ENABLED or not IBKR_USE_OAUTH:
        return
    if not IBKR_SERVER_IP:
        log.error(
            "IBKR_SERVER_IP is not set. IBKR ties OAuth sessions to your outbound IP. "
            "Register your public IP in the IBKR OAuth app, set IBKR_SERVER_IP in .env, "
            "and restart the API."
        )
        return
    try:
        import requests as _req

        resp = _req.get("https://api.ipify.org", timeout=5)
        if resp.ok:
            detected = resp.text.strip()
            if detected and detected != IBKR_SERVER_IP:
                log.warning(
                    "IBKR_SERVER_IP=%s but detected outbound IP=%s — update IBKR portal "
                    "and .env if sessions fail",
                    IBKR_SERVER_IP,
                    detected,
                )
            else:
                log.info("IBKR_SERVER_IP matches detected outbound IP (%s)", IBKR_SERVER_IP)
    except Exception as exc:
        log.debug("Could not detect outbound IP for IBKR check: %s", exc)
