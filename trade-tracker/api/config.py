import os
from urllib.parse import urlparse

_DATABASE_URL = os.getenv("DATABASE_URL", "")

if _DATABASE_URL:
    _url = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
    _parsed = urlparse(_url)
    DB_HOST = _parsed.hostname or "localhost"
    POSTGRES_PORT = _parsed.port or 5432
    POSTGRES_DB = (_parsed.path or "/trade_tracker").lstrip("/") or "trade_tracker"
    POSTGRES_USER = _parsed.username or "postgres"
    POSTGRES_PASSWORD = _parsed.password or "postgres"
else:
    DB_HOST = os.getenv("DB_HOST", "localhost")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "trade_tracker")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

DB_SSL = os.getenv("DB_SSL", "require" if _DATABASE_URL else "disable")
DB_MIN_CONNECTIONS = int(os.getenv("DB_MIN_CONNECTIONS", "2"))
DB_MAX_CONNECTIONS = int(os.getenv("DB_MAX_CONNECTIONS", "10"))

IBKR_GATEWAY_URL = os.getenv("IBKR_GATEWAY_URL", "https://localhost:5001")
IBKR_ENABLED = os.getenv("IBKR_ENABLED", "false").lower() == "true"
IBKR_ACCOUNT_ID = os.getenv("IBKR_ACCOUNT_ID", "")

PRICE_CACHE_TTL_SECONDS = int(os.getenv("PRICE_CACHE_TTL_SECONDS", "60"))
BENCHMARK_SYMBOL = os.getenv("BENCHMARK_SYMBOL", "SPY")

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
ALLOWED_EMAIL_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "dekalbcapitalmanagement.com")

# Deployed frontend origin (e.g. https://dekalb-trade-tracker.vercel.app), used for CORS.
# Comma-separated if there's more than one (e.g. production + preview deploys).
FRONTEND_URL = os.getenv("FRONTEND_URL", "")
