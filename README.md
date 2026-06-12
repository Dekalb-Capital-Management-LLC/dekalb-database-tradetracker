# DeKalb Database — Monorepo

Backend infrastructure for DeKalb Capital Management. Houses the database layer, event ingestion pipeline, and trade tracker (API + dashboard).

> **New here?** Also read [`CLAUDE.md`](CLAUDE.md) for architecture/conventions and [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) for what's done, what's broken, and what's next. Project planning lives in Linear — see [`docs/linear/`](docs/linear/).

---

## Repo Structure

```
dekalb-database-tradetracker/
├── ingestion-service/          # ZMQ -> PostgreSQL/QuestDB event pipeline (quant team)
│   ├── main.py                 # ZMQ listener entry point
│   ├── router.py               # Event routing logic
│   ├── config.py
│   ├── db_writers/
│   │   ├── postgres_writer.py
│   │   └── questdb_writer.py
│   ├── requirements.txt
│   └── Dockerfile
│
├── trade-tracker/              # Trade tracker (equities team)
│   ├── api/                    # FastAPI backend
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py               # asyncpg connection pool
│   │   ├── models/schemas.py   # Pydantic models
│   │   ├── routers/
│   │   │   ├── auth.py         # /auth/* (Google Workspace SSO)
│   │   │   ├── portfolio.py    # /portfolio/*
│   │   │   ├── trades.py       # /trades/*
│   │   │   ├── imports.py      # /import/fidelity
│   │   │   ├── market.py       # /market/*
│   │   │   └── ibkr.py         # /ibkr/*
│   │   ├── services/
│   │   │   ├── auth.py              # Google ID token verification (JWKS)
│   │   │   ├── ibkr_client.py       # IBKR Client Portal Gateway client
│   │   │   ├── market_data.py       # yfinance (IBKR when gateway is on)
│   │   │   ├── portfolio_metrics.py # beta, std dev, sharpe, alpha
│   │   │   └── fidelity_parser.py   # Fidelity CSV parser
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── railway.toml        # Railway deploy config (backend)
│   │
│   └── frontend/               # React + Vite dashboard
│       ├── src/
│       │   ├── auth/           # AuthContext (Google SSO)
│       │   ├── pages/          # Dashboard, Trades, Import, Login
│       │   ├── components/
│       │   ├── api/client.ts   # fetch wrapper, auth headers, base URL
│       │   └── types/
│       ├── nginx.conf          # Docker: proxies /api/* -> trade-tracker:8000
│       └── vercel.json         # Vercel deploy config (frontend)
│
├── ibkr-gateway/               # IBKR Client Portal Gateway config
│   └── conf.yaml.example       # Reference only - gateway download includes its own conf.yaml
│
├── schemas/
│   ├── postgresql_schema.sql       # orders, positions, accounts (quant team)
│   ├── questdb_schema.sql          # time-series tables (apply via QuestDB console)
│   └── trade_tracker_schema.sql    # trades, snapshots, imports (equities team)
│
├── tests/
│   ├── fake_zmq_sender.py      # Send test events to ingestion service
│   └── comprehensive_test.py   # Sends all event types across multiple symbols
│
├── docs/
│   ├── REPO_AUDIT.md           # Audit + Linear project/issue backlog
│   ├── FEATURES.md             # Feature catalog: what's live/planned/deprecated
│   └── linear/                 # Linear templates + GitHub workflow + label setup
│
├── .env.example                # Copy to .env and fill in your values
└── docker-compose.yml
```

---

## Quick Start (local dev)

```bash
# 1. Copy env file and fill in your values
cp .env.example .env
# at minimum set IBKR_ACCOUNT_ID if you have it - everything else has working defaults

# 2. Start all services
docker compose up --build

# 3. Check the API
curl http://localhost:8000/health

# 4. Swagger docs
open http://localhost:8000/docs

# 5. Frontend dashboard
open http://localhost:3000

# 6. Database GUI (Adminer)
open http://localhost:8080
# System: PostgreSQL | Server: postgres | User: postgres | Password: postgres
# Equities DB: trade_tracker  |  Quant DB: trading
```

By default `AUTH_ENABLED=false`, so the dashboard skips the Google sign-in screen entirely. See [Authentication](#authentication) to enable it.

---

## Services

| Service | Port | What it does |
|---|---|---|
| Trade Tracker API | 8000 | FastAPI backend — Swagger at `/docs` |
| Frontend | 3000 | React dashboard |
| PostgreSQL | 5432 | Main relational DB (two isolated databases) |
| QuestDB | 9000 | Time-series DB (quant team only) |
| Adminer | 8080 | DB GUI |
| Ingestion Service | 5555 | ZMQ listener (quant team) |
| IBKR Gateway | 5001 | Client Portal Gateway (runs on host, not in Docker) |

---

## Trade Tracker API Endpoints

| Endpoint | What it does |
|---|---|
| `GET /health` | Service health check |
| `GET /auth/config` | Returns whether SSO is enabled + Google client ID |
| `POST /auth/verify` | Verify a Google ID token, returns user profile |
| `GET /auth/me` | Current authenticated user (requires `Authorization: Bearer <id_token>`) |
| `GET /portfolio/summary` | Combined + per-account P&L |
| `GET /portfolio/positions` | Open positions with live P&L |
| `GET /portfolio/performance?period=ytd` | NAV time series + SPY overlay |
| `GET /portfolio/metrics?period=ytd` | Beta, std dev, Sharpe, alpha, max drawdown, win rate |
| `GET /portfolio/snapshots` | Raw daily NAV snapshots (debugging) |
| `POST /portfolio/snapshots/generate` | Store today's NAV snapshot |
| `GET /trades` | Full trade log with filters |
| `GET /trades/{id}` | Single trade detail |
| `PATCH /trades/{id}/label` | Label a trade |
| `POST /import/fidelity` | Upload Fidelity CSV |
| `GET /import/fidelity` | List past CSV imports |
| `GET /market/quote/{symbol}` | Current price (yfinance or IBKR) |
| `GET /market/quotes?symbols=...` | Batch price quotes |
| `GET /market/history/{symbol}` | Historical OHLCV bars |
| `GET /market/spy` | SPY benchmark history |
| `GET /ibkr/status` | Gateway connection status |
| `GET /ibkr/account` | Live NAV + balances from IBKR |
| `GET /ibkr/positions` | Live open positions from IBKR |
| `POST /ibkr/sync/trades` | Pull last 24h of IBKR fills into trades table |

---

## Authentication

The dashboard supports Google Workspace SSO, gated by `AUTH_ENABLED`.

- **`AUTH_ENABLED=false`** (default): no login screen, all endpoints open. Good for local dev.
- **`AUTH_ENABLED=true`**: every request except `/health`, `/docs`, `/redoc`, `/openapi.json`, and `/auth/*` requires `Authorization: Bearer <google id_token>`. The frontend shows a "Sign in with Google" button (Google Identity Services) restricted to `@<ALLOWED_EMAIL_DOMAIN>` accounts.

### How it works

1. Frontend loads Google's `gsi/client` script and renders the Sign-In button, scoped to `hd=<ALLOWED_EMAIL_DOMAIN>`.
2. On success, the browser gets a Google **ID token** (JWT) and POSTs it to `/auth/verify`.
3. The backend (`services/auth.py`) verifies the token's signature against Google's published JWKS, checks the issuer, audience (`GOOGLE_CLIENT_ID`), email verification, and `hd`/email domain.
4. The frontend stores the ID token in `localStorage` and sends it as `Authorization: Bearer <token>` on every API call. `AuthMiddleware` in `main.py` re-verifies it on each request.

ID tokens expire (~1h). There's currently no refresh flow — see [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md).

### Setting up a Google OAuth Client ID

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
2. Create an **OAuth 2.0 Client ID** of type "Web application".
3. Under **Authorized JavaScript origins**, add every origin the frontend is served from, e.g.:
   - `http://localhost:3000` (local dev)
   - `https://<your-app>.vercel.app` (production)
4. Copy the Client ID into `GOOGLE_CLIENT_ID`.
5. Set `ALLOWED_EMAIL_DOMAIN` (defaults to `dekalbcapitalmanagement.com`) and `AUTH_ENABLED=true`.

---

## IBKR Gateway Setup

The API works without IBKR — it falls back to **yfinance** for market prices. Enabling IBKR gives you live account data, position sync, and real-time prices.

### How it works

The **IBKR Client Portal Gateway** is a small Java app you run on your machine. You log into it once via browser (username + password + 2FA), and it keeps a session alive. The API talks to it directly at `https://localhost:5001` — there's no VPN or proxy involved.

### Step-by-step

**1. Download the gateway**

Go to: https://www.interactivebrokers.com/en/trading/ib-api.php

Find "Client Portal API" and download the `.zip`. Unzip it into `ibkr-gateway/`:

```
ibkr-gateway/
└── clientportal.gw/
    └── root/
        └── clientportal.gw.jar
```

**2. Start the gateway**

```bash
cd ibkr-gateway/clientportal.gw
bin/run.sh root/conf.yaml
```

You do **not** need to copy or edit `conf.yaml` — the download already includes a working one (listens on port 5001 by default). `ibkr-gateway/conf.yaml.example` in this repo is reference-only.

Leave this terminal open.

**3. Authenticate in your browser**

Open `https://localhost:5001`. Your browser will warn about the self-signed cert — click through it. Log in with your IBKR username/password and complete 2FA.

You'll see a confirmation page when it works. The session lasts ~24 hours. Repeat this step after it expires.

**4. Set env vars in your .env file**

```
IBKR_ENABLED=true
IBKR_ACCOUNT_ID=U1234567
```

Your account ID is on the IBKR homepage after login (top right), format: `U` followed by digits.

**5. Restart the API**

```bash
docker compose up --build trade-tracker
```

**6. Verify**

```bash
curl http://localhost:8000/ibkr/status
# {"enabled": true, "connected": true, "authenticated": true, ...}
```

> **Docker note:** The gateway runs on your host machine. Docker reaches it via `host.docker.internal:5001`, which is already configured in `docker-compose.yml`. No extra steps needed.

> **Production note:** The gateway is a desktop app tied to one person's 2FA session — it cannot run unattended on Railway. In production, leave `IBKR_ENABLED=false` (yfinance fallback) unless/until there's a plan for headless re-auth. See [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md).

---

## Importing Trades

### From Fidelity (CSV)

1. In Fidelity: Accounts & Trade > Portfolio > select account > Activity & Orders > Download CSV
2. `POST /import/fidelity` with the file and an `account_id` string (e.g. `FIDELITY_MAIN`)
3. Trades land unlabeled — use `PATCH /trades/{id}/label` to categorize them
4. Labels: `event-driven`, `hedge`, `long-term`, `short-term`

### From IBKR (live sync)

Once the gateway is running and authenticated: `POST /ibkr/sync/trades`

This pulls the last ~24h of fills. For older history, use IBKR Flex Queries and import the CSV.

---

## Deploying to Production (Railway + Vercel)

The backend deploys to **Railway**, the frontend to **Vercel**. They're separate projects/repos-as-monorepo deployments, connected via env vars (no shared secrets file).

### 1. Backend → Railway

1. Create a new Railway project from this GitHub repo.
2. In the service's **Settings → Source**, set **Root Directory** to `trade-tracker/api`. Railway will then pick up `trade-tracker/api/railway.toml` (Dockerfile build, `/health` healthcheck).
3. Add a **PostgreSQL** plugin to the project. Railway injects `DATABASE_URL` automatically — `config.py` parses it and turns on `DB_SSL=require`.
4. Set these service env vars:

   | Variable | Value |
   |---|---|
   | `AUTH_ENABLED` | `true` |
   | `GOOGLE_CLIENT_ID` | from Google Cloud Console (see [Authentication](#authentication)) |
   | `ALLOWED_EMAIL_DOMAIN` | `dekalbcapitalmanagement.com` |
   | `FRONTEND_URL` | the Vercel URL from step 2 below (set this *after* step 2) |
   | `IBKR_ENABLED` | `false` (gateway can't run on Railway — see IBKR section) |

5. Deploy. Railway gives you a public URL like `https://<service>.up.railway.app`.
6. Verify: `curl https://<service>.up.railway.app/health` → `{"status": "ok", ...}`.

### 2. Frontend → Vercel

1. Create a new Vercel project from this repo, with **Root Directory** set to `trade-tracker/frontend`. Vercel auto-detects Vite via `vercel.json`.
2. Set the env var `VITE_API_BASE_URL` to the Railway URL from step 1.6 above (no trailing slash, no `/api` suffix — the API has no path prefix).
3. Deploy. Vercel gives you a URL like `https://<project>.vercel.app`.

### 3. Close the loop

Go back to Railway and set `FRONTEND_URL` to the Vercel URL from step 2.3 (comma-separate if you also want to allow a preview-deploy domain). Redeploy the backend so CORS picks up the new origin, then add the Vercel URL(s) to the Google OAuth Client's **Authorized JavaScript origins**.

At that point: browser → Vercel (static frontend) → directly to Railway API (cross-origin, allowed by `FRONTEND_URL` CORS) → Railway Postgres.

---

## Ingestion Service (Quant Team)

Listens on ZMQ port 5555 for trading events from the live engine.

```bash
# Send test events
python tests/fake_zmq_sender.py
```

| Event type | Destination |
|---|---|
| `execution` | PostgreSQL (orders + positions) + QuestDB |
| `order_update` | PostgreSQL only |
| `log` | QuestDB only |
| `signal` | QuestDB only |

---

## Database Schema

PostgreSQL schemas are auto-applied on first boot.

| Database | Schema file | Tables |
|---|---|---|
| `trading` | `postgresql_schema.sql` | orders, positions, accounts, strategies, ib_api_calls |
| `trade_tracker` | `trade_tracker_schema.sql` | trades, portfolio_snapshots, fidelity_imports, cash_flows |

QuestDB tables are created manually — open `http://localhost:9000` and run `schemas/questdb_schema.sql`.

---

## Portfolio Metrics

Calculated from daily NAV snapshots in `portfolio_snapshots`. The `snapshot-cron` container runs `POST /portfolio/snapshots/generate` every hour automatically.

| Metric | Formula |
|---|---|
| Beta | Cov(portfolio, SPY) / Var(SPY) |
| Std Dev | Daily std dev x sqrt(252) |
| Sharpe | Annualized return / Annualized std dev (risk-free rate = 0) |
| Alpha | Portfolio return - Beta x SPY return |
| Max Drawdown | Max peak-to-trough NAV decline |
| Win Rate | % of SELL trades with positive `net_amount` (simplified, not FIFO-matched) |

---

## Project Status, Roadmap & Team Workflow

- [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) — what's working, what's broken, what's next, and the current project/issue backlog.
- [`docs/FEATURES.md`](docs/FEATURES.md) — catalog of features (live/planned/deprecated) and where their code lives.
- [`docs/linear/`](docs/linear/) — Linear project/issue templates and the GitHub↔Linear workflow.
