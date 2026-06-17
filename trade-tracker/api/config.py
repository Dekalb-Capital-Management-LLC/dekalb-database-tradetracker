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

# ---------------------------------------------------------------------------
# IBKR Web API — RSA key-based OAuth 2.0 (server-to-server, no browser login)
#
# How it works:
#   Your RSA private key signs a JWT → IBKR returns a bearer token → you
#   create an SSO session with your IBKR username → make API calls.
#   Fully automated. No user action needed. Reconnects on its own.
#
# Paper account:
#   IBKR_CLIENT_ID      = DekalbCapital-Paper
#   IBKR_CLIENT_KEY_ID  = main
#   IBKR_CREDENTIAL     = dekalbcapitalpaper   (the IBKR paper username)
#   IBKR_ACCOUNT_ID     = DFP321877
#
# Live / Production account (F account):
#   IBKR_CLIENT_ID      = DekalbCapital-Prod
#   IBKR_CLIENT_KEY_ID  = main
#   IBKR_CREDENTIAL     = dekalbcapital3
#   IBKR_ACCOUNT_ID     = F16173704
#   (private key from Ryan's zip — same key pair registered for this account)
#
# IBKR_PRIVATE_KEY: paste the full contents of your privatekey.pem file.
#   In .env, escape newlines as \n  OR  use a literal multiline value.
#   e.g.  IBKR_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
#
# IBKR_SERVER_IP: the outbound IP of this server as seen by IBKR.
#   Local dev: your public IP (google "what is my ip")
#   Railway:   check Settings → Networking for your static outbound IP
# ---------------------------------------------------------------------------
IBKR_ENABLED = os.getenv("IBKR_ENABLED", "false").lower() == "true"

IBKR_CLIENT_ID     = os.getenv("IBKR_CLIENT_ID", "")       # e.g. DekalbCapital-Paper
IBKR_CLIENT_KEY_ID = os.getenv("IBKR_CLIENT_KEY_ID", "")   # e.g. main
IBKR_CREDENTIAL    = os.getenv("IBKR_CREDENTIAL", "")       # IBKR username
IBKR_ACCOUNT_ID    = os.getenv("IBKR_ACCOUNT_ID", "")       # e.g. DFP321877
IBKR_SERVER_IP     = os.getenv("IBKR_SERVER_IP", "")        # outbound IP of this server

# RSA private key — accepts three formats:
#   1. Base64-encoded PEM (ends with ==)  → just paste the whole thing as-is, no quotes
#   2. Raw PEM with \n escapes            → -----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END...
#   3. Raw PEM with actual newlines       → only works with quoted multiline in .env
import base64 as _b64
_raw_key = os.getenv("IBKR_PRIVATE_KEY", "").strip()
if _raw_key and not _raw_key.startswith("-----"):
    # Looks like base64-encoded — decode it to get the PEM string
    try:
        _decoded = _b64.b64decode(_raw_key).decode("utf-8").strip()
        # If the decoded result is missing PEM headers, add them
        if not _decoded.startswith("-----"):
            _decoded = f"-----BEGIN RSA PRIVATE KEY-----\n{_decoded}\n-----END RSA PRIVATE KEY-----"
        IBKR_PRIVATE_KEY = _decoded
    except Exception:
        IBKR_PRIVATE_KEY = _raw_key.replace("\\n", "\n")
else:
    IBKR_PRIVATE_KEY = _raw_key.replace("\\n", "\n")

# IBKR API base URLs — don't change these unless IBKR updates them
IBKR_TOKEN_URL       = "https://api.ibkr.com/oauth2/api/v1/token"
IBKR_SSO_URL         = "https://api.ibkr.com/gw/api/v1/sso-sessions"
IBKR_BASE_URL        = "https://api.ibkr.com/v1/api"

# IBKR snapshot cache TTL
PRICE_CACHE_TTL_SECONDS = int(os.getenv("PRICE_CACHE_TTL_SECONDS", "300"))

# SPY symbol for benchmark overlay
BENCHMARK_SYMBOL = os.getenv("BENCHMARK_SYMBOL", "SPY")

# Annual risk-free rate as a decimal, used in Sharpe ratio.
# Example: 0.05 = 5%.
RISK_FREE_RATE_ANNUAL = float(os.getenv("RISK_FREE_RATE_ANNUAL", "0.0"))

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
ALLOWED_EMAIL_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "dekalbcapitalmanagement.com")

# Deployed frontend origin (e.g. https://dekalb-trade-tracker.vercel.app), used for CORS.
# Comma-separated if there's more than one (e.g. production + preview deploys).
FRONTEND_URL = os.getenv("FRONTEND_URL", "")
