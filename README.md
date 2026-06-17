# DeKalb Database — Monorepo

---

## ⚡ Quick-Start Command List (On Every Startup)

Run these in order after `docker compose up --build`:

```
1. POST /ibkr/connect           ← reconnect to IBKR (auto-runs on boot, but use this if needed)
2. POST /ibkr/sync/trades       ← pull latest fills from IBKR (last 7 days)
3. POST /portfolio/snapshots/generate  ← generate today's NAV snapshot for performance chart
4. GET  /portfolio/summary      ← verify everything loaded correctly
```

**One-time setup after first deploy or data reset:**
```
DELETE /trades/reset            ← wipe all old paper/test data (CAREFUL — irreversible)
POST   /ibkr/sync/trades        ← pull live fills in
POST   /import/fidelity         ← upload Fidelity CSV (positions snapshot or activity)
POST   /portfolio/snapshots/generate  ← generate first NAV snapshot
```

**The backend cron syncs trades and writes NAV snapshots every hour. The dashboard refreshes summary data every 5 minutes.**

---



Backend infrastructure for DeKalb Capital. Runs on **Machine 2** (database server). Handles live trading event ingestion, portfolio storage, and the equities team's trade tracker dashboard.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  MACHINE 1 — Paper Trading Server                           │
│                                                             │
│  Trading Engine: Strategy → Risk Check → IB API            │
│         │                                                   │
│         ▼                                                   │
│  Log Aggregator  (Orders, Executions, Logs, Signals)        │
│         │                                                   │
│         ▼                                                   │
│  Bucket — batches events, sends every 1000 events or 5s     │
│         │                                                   │
│         │   ZMQ PUSH  →  tcp://machine2:5555               │
└─────────┼───────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│  MACHINE 2 — Database Server  (this repo)                   │
│                                                             │
│  Ingestion Service (ZMQ PULL port 5555)                     │
│       │                                                     │
│       ▼                                                     │
│     Router                                                  │
│    /       \                                                │
│   ▼         ▼                                               │
│  PostgreSQL   QuestDB                                       │
│  (state)      (time-series)                                 │
│                                                             │
│  Trade Tracker API → Equities team web dashboard            │
└─────────────────────────────────────────────────────────────┘
```

---

## Repo Structure

```
dekalb-database/
│
├── ingestion-service/              # ZMQ → DB pipeline (quant team)
│   ├── main.py                     # Entry point — ZMQ listener loop
│   ├── router.py                   # Routes events to correct DB writer
│   ├── config.py                   # Hosts, ports, ZMQ address
│   ├── db_writers/
│   │   ├── postgres_writer.py      # Writes orders + positions
│   │   └── questdb_writer.py       # Writes executions, logs, signals via ILP
│   ├── requirements.txt
│   └── Dockerfile
│
├── trade-tracker/                  # Equities team web app
│   ├── api/                        # FastAPI backend
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py                   # Connection pool + auto-migrations
│   │   ├── models/schemas.py
│   │   ├── routers/
│   │   │   ├── ibkr.py             # /ibkr/* — OAuth connect, sync trades
│   │   │   ├── portfolio.py        # /portfolio/* — summary, positions, metrics
│   │   │   ├── trades.py           # /trades/* — trade log, labels
│   │   │   ├── imports.py          # /import/fidelity and /import/ibkr
│   │   │   └── market.py           # /market/* — live prices, SPY history
│   │   ├── services/
│   │   │   ├── ibkr_client.py      # IBKR Web API client (OAuth 2.0)
│   │   │   ├── ibkr_parser.py      # IBKR Activity Statement CSV parser
│   │   │   ├── fidelity_parser.py  # Fidelity CSV parser
│   │   │   ├── market_data.py      # yfinance + IBKR price fetching with cache
│   │   │   └── portfolio_metrics.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── railway.toml
│   └── frontend/                   # React + Vite + Tailwind
│       ├── src/
│       │   ├── pages/              # Dashboard, Trades, Import
│       │   └── components/
│       ├── vercel.json
│       └── package.json
│
├── schemas/
│   ├── postgresql_schema.sql        # Quant team DB (auto-applied on first boot)
│   ├── questdb_schema.sql           # Quant team time-series (run manually in console)
│   └── trade_tracker_schema.sql     # Equities team DB (auto-applied on first boot)
│
├── tests/
│   └── fake_zmq_sender.py           # Sends fake events to test the ingestion pipeline
│
├── .env.example                     # Copy to .env and fill in
└── docker-compose.yml
```

---

## Database Design

### Why two databases on Machine 2?

| | PostgreSQL | QuestDB |
|---|---|---|
| Best for | State that changes | Append-only time-series |
| Storage | Row-based | Columnar |
| Transactions | ACID | WAL |
| Indexes | B-tree | Time-based partitions |
| Updates | Fast UPDATEs | Append only |
| Quant team use | Orders, positions, accounts | Executions, logs, signals, ticks |

---

### PostgreSQL — `trading` database (quant team)

Applied automatically from `schemas/postgresql_schema.sql` on first boot.

| Table | What it holds |
|---|---|
| `orders` | Every order from submission to fill — status, fill price, commission |
| `positions` | Current holdings by account and symbol — UPSERT on each execution |
| `accounts` | Account-level cash, buying power, equity |
| `strategies` | Strategy registry with JSONB parameters |
| `ib_api_calls` | Audit log of every IB API call (compliance) |

---

### QuestDB — time-series (quant team)

Tables must be created manually. Open `http://localhost:9000`, paste `schemas/questdb_schema.sql`, run it. One time only.

| Table | What it holds |
|---|---|
| `executions` | Every trade fill — append-only, partitioned by day |
| `engine_logs` | High-volume application logs |
| `strategy_signals` | Buy/sell signals from strategies |
| `tick_data` | Market prices (optional) |

QuestDB uses `SYMBOL` columns for low-cardinality strings (env, side, strategy) — stored as integers internally for fast filtering. All tables use `PARTITION BY DAY WAL`.

> **Production note:** The gateway is a desktop app tied to one person's 2FA session — it cannot run unattended on Railway. In production, leave `IBKR_ENABLED=false` (yfinance fallback) unless/until there's a plan for headless re-auth. See [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md).

---

### PostgreSQL — `trade_tracker` database (equities team)

Applied automatically from `schemas/trade_tracker_schema.sql` on first boot. Auto-migrated on API startup — no manual steps ever needed.

| Table | What it holds |
|---|---|
| `trades` | Unified trade ledger — IBKR + Fidelity in one table |
| `portfolio_snapshots` | Daily NAV history for performance chart |
| `fidelity_imports` | Audit log of portfolio, Fidelity, and IBKR uploads |
| `cash_flows` | Deposits/withdrawals (excluded from performance calculations) |
| `ibkr_tokens` | OAuth 2.0 tokens for IBKR Web API — auto-managed |
| `instrument_conids` | Symbol-to-IBKR contract ID cache for market data |
| `imported_positions` | Latest broker/imported position snapshot for dashboard P&L |

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
   | `RISK_FREE_RATE_ANNUAL` | `0.0` by default; set `0.05` for 5% |
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

Receives batched events from Machine 1 over ZMQ PULL and routes them.

### Event routing

| Event type | PostgreSQL | QuestDB |
|---|---|---|
| `execution` | UPDATE orders + UPSERT positions | INSERT executions |
| `order_update` | UPDATE orders | — |
| `log` | — | INSERT engine_logs |
| `signal` | — | INSERT strategy_signals |

### ZMQ message format

```json
{
  "type": "batch",
  "batch_time": "2024-01-15T10:30:00Z",
  "count": 3,
  "events": [
    {
      "type": "execution",
      "timestamp": "2024-01-15T10:30:00Z",
      "server_env": "paper",
      "data": {
        "order_id": "ORD001",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 100,
        "price": 185.40,
        "commission": 1.00,
        "strategy": "momentum_v1"
      }
    }
  ]
}
```

### Testing the pipeline

```bash
# With ingestion-service running:
python tests/fake_zmq_sender.py
# Sends 5 batches of 3 events each. Check Adminer at http://localhost:8080.
```

---

## Trade Tracker — Equities Team

A web dashboard for tracking IBKR + Fidelity positions, P&L, and portfolio metrics vs SPY. Team members just open a URL.

### How data gets in

**IBKR (fully automated):**
- Set credentials in `.env` (see IBKR Web API Setup section below)
- API connects automatically on startup — no user action, no login page
- New fills sync automatically every hour — nothing to do

**IBKR full history (one-time):**
- The API only returns recent trades
- For everything before that: export from IBKR → Client Portal → Performance & Reports → Activity Statements → set date range → Format: CSV → Download
- Upload on the **Import** page — duplicates are skipped automatically
- After this upload, the hourly sync handles everything going forward

**Fidelity (manual CSV upload):**
- Export from Fidelity → Accounts & Trade → Portfolio → Activity & Orders → Download
- Upload on the **Import** page
- Upload again whenever you want to pull in new Fidelity trades

---

## Running Locally

### Option A — Docker (recommended, runs everything)

```bash
# 1. Configure
cp .env.example .env
# Edit .env — fill in IBKR credentials if you want live data (optional)

# 2. Start
docker compose up --build

# 3. Check
curl http://localhost:8000/health
```

Services that start:

| Service | URL | What it is |
|---|---|---|
| Trade Tracker | http://localhost:3000 | React dashboard |
| API | http://localhost:8000/docs | Swagger UI |
| Adminer | http://localhost:8080 | DB browser |
| QuestDB | http://localhost:9000 | Time-series console |
| PostgreSQL | localhost:5432 | Direct DB access |
| Ingestion Service | port 5555 | ZMQ PULL for quant events |

IBKR credentials are optional for local dev — everything works with yfinance for prices and Fidelity CSV imports.

### Option B — Without Docker

Terminal 1 — start PostgreSQL locally (or point to any running Postgres), then run the API:
```bash
cd trade-tracker/api
pip install -r requirements.txt
export DB_HOST=localhost POSTGRES_DB=trade_tracker
uvicorn main:app --reload --port 8000
```

Terminal 2 — frontend:
```bash
cd trade-tracker/frontend
npm install
npm run dev
```

Frontend at `http://localhost:5173`, API at `http://localhost:8000`.

---

## Deploying for the Team (Vercel + Railway)

Everyone on the team opens one URL — no one installs anything locally.

### Step 1 — Deploy API on Railway

1. Create a project at [railway.app](https://railway.app)
2. Add a PostgreSQL service (Railway provides the connection string)
3. Connect this GitHub repo, set root directory to `trade-tracker/api`
4. Railway picks up `railway.toml` automatically
5. Set environment variables in the Railway dashboard:

```
DB_HOST               = (from Railway PostgreSQL, e.g. postgres.railway.internal)
POSTGRES_PASSWORD     = (from Railway PostgreSQL)
POSTGRES_DB           = trade_tracker
POSTGRES_USER         = postgres
IBKR_ENABLED          = true
IBKR_CLIENT_ID        = (from IBKR — see setup below)
IBKR_CLIENT_SECRET    = (from IBKR — see setup below)
IBKR_ACCOUNT_ID       = U1234567
IBKR_REDIRECT_URI     = https://YOUR-APP.railway.app/ibkr/auth/callback
FRONTEND_URL          = https://YOUR-APP.vercel.app
RISK_FREE_RATE_ANNUAL = 0.0
```

Note the Railway API URL — you'll need it in the next step.

### Step 2 — Deploy frontend on Vercel

1. Import this repo at [vercel.com](https://vercel.com), root directory = `trade-tracker/frontend`
2. Edit `trade-tracker/frontend/vercel.json` — replace the placeholder with your actual Railway URL:

```json
{
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://YOUR-ACTUAL-RAILWAY-URL.railway.app/:path*"
    },
    {
      "source": "/((?!api/).*)",
      "destination": "/index.html"
    }
  ]
}
```

3. Deploy — this is the URL you share with the team.

### Step 3 — Configure IBKR credentials in Railway

In Railway → your service → Variables, add:
```
IBKR_ENABLED=true
IBKR_CLIENT_ID=DekalbCapital-Paper   (or live value from ticket #619394)
IBKR_CLIENT_KEY_ID=main              (or live value)
IBKR_CREDENTIAL=dekalbcapitalpaper   (or live username)
IBKR_ACCOUNT_ID=DFP321877            (or live account)
IBKR_PRIVATE_KEY=<full privatekey.pem content with \n for newlines>
IBKR_SERVER_IP=<Railway static outbound IP>
```

The API auto-connects on startup. No user action needed, no login page.

---

## IBKR Web API Setup

IBKR's Web API uses **RSA key-based OAuth 2.0** — this is server-to-server authentication, not a browser login flow. There is no redirect URL, no login page, and no user action after initial setup.

**How it works:**
1. Your RSA private key signs a JWT
2. That JWT is sent to IBKR → they return a bearer token
3. The bearer token + your IBKR username + your server's IP → creates an IBKR session
4. The session auto-renews in the background every 60 seconds

**DeKalb already has approved credentials.** Ryan has the zip with the private key and ticket #619394 has the live account `clientId`, `clientKeyId`, and `credential`.

**Setting it up:**

1. Open Ryan's zip (password: `dcm1234`) — it contains `privatekey.pem` (and possibly the live credentials)
2. Open your `.env` file and fill in:

```
IBKR_ENABLED=true

# Paper account (ready to use now):
IBKR_CLIENT_ID=DekalbCapital-Paper
IBKR_CLIENT_KEY_ID=main
IBKR_CREDENTIAL=dekalbcapitalpaper
IBKR_ACCOUNT_ID=DFP321877

# RSA private key — paste the FULL contents of privatekey.pem, with \n for each newline:
IBKR_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----

# The outbound IP of the machine running the trade-tracker API:
# Local dev: google "what is my ip"
# Railway: Settings → Networking → Outbound Static IP
IBKR_SERVER_IP=YOUR.SERVER.IP.HERE
```

3. Restart the API — it connects automatically on startup. No further action needed.

**Live account** (`clientId`, `clientKeyId`, `credential` from ticket #619394): same process, just swap those three values in `.env`. The RSA key file is the same for both paper and live.

**Note on IP:** IBKR ties sessions to an IP address. If you're running locally, use your home IP. For Railway, you need their static outbound IP (Pro plan). If the IP changes, `POST /ibkr/connect` re-establishes the session with the new IP.

---

## Trade Tracker API Reference

Full interactive docs at `/docs` (Swagger UI).

| Endpoint | What it does |
|---|---|
| `GET /health` | Health check |
| **IBKR** | |
| `GET /ibkr/status` | Is IBKR connected? (auto-connects on startup) |
| `POST /ibkr/connect` | Manually trigger reconnect (useful after credential change) |
| `GET /ibkr/account` | Live NAV, cash, equity from IBKR |
| `GET /ibkr/positions` | Live open positions from IBKR |
| `POST /ibkr/sync/trades` | Pull recent fills now (also runs automatically every hour) |
| **Portfolio** | |
| `GET /portfolio/summary` | Combined + per-account P&L snapshot |
| `GET /portfolio/positions` | Open positions with live pricing |
| `GET /portfolio/performance?period=ytd` | NAV time series + SPY overlay |
| `GET /portfolio/metrics?period=ytd` | Cash-flow-adjusted beta, std dev, Sharpe, alpha, drawdown, approximate win rate |
| `GET /portfolio/cash-flows` | List recorded cash flows |
| `POST /portfolio/cash-flows` | Record a deposit/withdrawal cash flow for performance adjustment |
| `DELETE /portfolio/cash-flows/{id}` | Delete a recorded cash flow |
| `POST /portfolio/snapshots/generate` | Generate today's NAV snapshot (also runs automatically every hour) |
| **Trades** | |
| `GET /trades` | Full trade log — filter by symbol, side, label, date |
| `PATCH /trades/{id}/label` | Set label, hedge flag, notes |
| **Imports** | |
| `POST /import/ibkr` | Upload IBKR Activity Statement CSV (historical data) |
| `POST /import/fidelity` | Upload Fidelity CSV |
| `GET /import/fidelity` | List all past imports |
| **Market** | |
| `GET /market/quote/{symbol}` | Current price (IBKR or yfinance fallback) |
| `GET /market/quotes?symbols=AAPL,MSFT` | Batch quotes |
| `GET /market/spy` | SPY benchmark data |

Period options: `1m`, `3m`, `6m`, `ytd`, `1y`

---

## 🔮 Next Steps — Member Browser Login (To Implement Later)

Currently the dashboard is a single shared view with no user auth. To let each team member log in with their own browser and see their own account:

### Option A — Simple Password Protection (1-2 days work)
Add HTTP Basic Auth via nginx in front of the app. One shared password for the whole team. Simplest option, zero code changes.

### Option B — Per-Member Login with IBKR OAuth (1-2 weeks work)
Let each member authenticate with their own IBKR account via the browser. This is a real OAuth 2.0 flow — each user gets redirected to IBKR to log in, then comes back with their own session.

**What needs to be built:**
1. **User table in PostgreSQL** — map IBKR credentials to team member profiles
2. **OAuth callback endpoint** — `GET /auth/ibkr/callback` — receives the auth code after IBKR login
3. **Session management** — store session tokens in `ibkr_tokens` table (already exists), issue JWTs to the browser
4. **Auth middleware on FastAPI** — protect all endpoints, extract user from JWT
5. **Login page on frontend** — a simple page with a "Login with IBKR" button
6. **Per-user data filtering** — trades/positions filtered by the logged-in user's account IDs

**IBKR's OAuth 2.0 Browser Flow (different from what we have):**
- What we have now: RSA key-based server-to-server auth (one fixed service account)
- What members need: browser-based OAuth where users log in to IBKR in a popup, approve access, and get redirected back
- IBKR calls this "Third-party OAuth 2.0" — it requires a redirect URI registered with IBKR
- Contact IBKR API team to register a redirect URI for your Railway/Vercel URL

**IBKR docs:** https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/

**Key config needed from IBKR:**
- A registered redirect URI: `https://YOUR-APP.vercel.app/auth/callback`
- The OAuth 2.0 authorization endpoint URL from IBKR
- This is a separate app registration from the current service account

---

## Adminer — DB Browser

`http://localhost:8080`

| Team | Database |
|---|---|
| Quant | System: PostgreSQL / Server: postgres / DB: **trading** |
| Equities | System: PostgreSQL / Server: postgres / DB: **trade_tracker** |

User: `postgres` — Password: `postgres`
