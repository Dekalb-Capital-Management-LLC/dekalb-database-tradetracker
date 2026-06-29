# DeKalb Database — Monorepo

Backend infrastructure for DeKalb Capital. Handles live trading-event ingestion
(quant team) and the equities team's trade-tracker dashboard. Two
mostly-independent halves share one Postgres instance.

> **⚠️ Status:** The ingestion service is solid. The **Trade Tracker is not
> production-ready** — auth isn't enforced, IBKR connects but can't pull
> positions/pricing, the Fidelity *CSV* import is currently dead code (only a
> custom XLSX import works), and the production deploy has never been completed.
> **[`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) is the source of truth for what
> works vs. what's broken** — read it before relying on any Trade Tracker
> feature. [`docs/FEATURES.md`](docs/FEATURES.md) has the per-feature status
> table.

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
│   │   │   ├── auth.py             # /auth/* — Google SSO (NOT wired into main.py yet)
│   │   │   ├── ibkr.py             # /ibkr/* — connect, account, positions, sync
│   │   │   ├── portfolio.py        # /portfolio/* — summary, positions, metrics
│   │   │   ├── trades.py           # /trades/* — trade log, labels
│   │   │   ├── imports.py          # /import/* — XLSX portfolio upload
│   │   │   └── market.py           # /market/* — quotes, history, SPY
│   │   ├── services/
│   │   │   ├── auth.py             # Google ID-token verification (unused at runtime)
│   │   │   ├── ibkr_client.py      # IBKR cloud Web API client (RSA OAuth 2.0)
│   │   │   ├── universal_parser.py # parse_portfolio_xlsx — the live import path
│   │   │   ├── fidelity_parser.py  # Fidelity CSV parser (DEAD CODE — not called)
│   │   │   ├── ibkr_parser.py      # IBKR Activity CSV parser (DEAD CODE — not called)
│   │   │   ├── market_data.py      # yfinance (+ IBKR when it works) with cache
│   │   │   └── portfolio_metrics.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── railway.toml
│   └── frontend/                   # React + Vite + Tailwind
│       ├── src/
│       │   ├── pages/              # Dashboard, Trades, Import, Login
│       │   ├── auth/               # AuthContext, Login
│       │   └── components/         # Layout, charts, tables
│       ├── vercel.json
│       └── package.json
│
├── schemas/
│   ├── postgresql_schema.sql        # Quant team DB (auto-applied on first boot)
│   ├── questdb_schema.sql           # Quant team time-series (run manually in console)
│   └── trade_tracker_schema.sql     # Equities team DB (auto-applied; see note below)
│
├── tests/
│   └── fake_zmq_sender.py           # Sends fake events to test the ingestion pipeline
│
├── .env.example                     # Copy to .env and fill in
└── docker-compose.yml
```

---

## Database Design

### Why two databases?

| | PostgreSQL | QuestDB |
|---|---|---|
| Best for | State that changes | Append-only time-series |
| Storage | Row-based | Columnar |
| Transactions | ACID | WAL |
| Quant team use | Orders, positions, accounts | Executions, logs, signals, ticks |

### PostgreSQL — `trading` database (quant team)

Applied automatically from `schemas/postgresql_schema.sql` on first boot.

| Table | What it holds |
|---|---|
| `orders` | Every order from submission to fill — status, fill price, commission |
| `positions` | Current holdings by account and symbol — UPSERT on each execution |
| `accounts` | Account-level cash, buying power, equity |
| `strategies` | Strategy registry with JSONB parameters |
| `ib_api_calls` | Audit log of every IB API call (compliance) |

### QuestDB — time-series (quant team)

Tables must be created manually. Open `http://localhost:9000`, paste
`schemas/questdb_schema.sql`, run it. One time only. (Auto-applying this is a
low-priority item in `docs/REPO_AUDIT.md`.)

| Table | What it holds |
|---|---|
| `executions` | Every trade fill — append-only, partitioned by day |
| `engine_logs` | High-volume application logs |
| `strategy_signals` | Buy/sell signals from strategies |
| `tick_data` | Market prices (optional) |

### PostgreSQL — `trade_tracker` database (equities team)

`schemas/trade_tracker_schema.sql` is applied on first boot **and** `db.py`'s
`_apply_migrations` adds more tables at startup. **The schema file and the real
schema have drifted** — the file defines only the first four tables below; the
last three exist *only* as runtime migrations in `db.py`. (Reconciling this is
an issue in `docs/REPO_AUDIT.md`.)

| Table | What it holds | Defined in |
|---|---|---|
| `trades` | Unified trade ledger | schema file |
| `portfolio_snapshots` | Daily NAV history for the performance chart | schema file |
| `fidelity_imports` | Audit log of all uploads | schema file |
| `cash_flows` | Deposits/withdrawals (intended to be excluded from perf — currently unused) | schema file |
| `imported_positions` | Current holdings (the portfolio/positions path depends on this) | `db.py` migration |
| `ibkr_tokens` | OAuth tokens for IBKR Web API | `db.py` migration |
| `instrument_conids` | Cached symbol → IBKR conid lookups | `db.py` migration |

Note the partial unique indexes on `portfolio_snapshots` for `account_id IS NULL`
(combined portfolio) vs per-account snapshots.

---

## Running Locally

### Option A — Docker (recommended, runs everything)

```bash
cp .env.example .env          # fill in IBKR creds only if you want to test IBKR
docker compose up --build
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

IBKR credentials are optional — the dashboard works with yfinance for prices and
the XLSX import for holdings.

### Option B — Without Docker

```bash
# Terminal 1 — API (point at any running Postgres)
cd trade-tracker/api
pip install -r requirements.txt
export DB_HOST=localhost POSTGRES_DB=trade_tracker
uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
cd trade-tracker/frontend
npm install
npm run dev
```

Frontend at `http://localhost:5173`, API at `http://localhost:8000`.

---

## Ingestion Service (Quant Team)

Receives batched events from Machine 1 over ZMQ PULL (port 5555) and routes them.
This service is functionally complete.

| Event type | PostgreSQL | QuestDB |
|---|---|---|
| `execution` | UPDATE orders + UPSERT positions | INSERT executions |
| `order_update` | UPDATE orders | — |
| `log` | — | INSERT engine_logs |
| `signal` | — | INSERT strategy_signals |

ZMQ message format:

```json
{
  "type": "batch",
  "batch_time": "2024-01-15T10:30:00Z",
  "count": 1,
  "events": [
    {
      "type": "execution",
      "timestamp": "2024-01-15T10:30:00Z",
      "server_env": "paper",
      "data": {
        "order_id": "ORD001", "symbol": "AAPL", "side": "BUY",
        "quantity": 100, "price": 185.40, "commission": 1.00,
        "strategy": "momentum_v1"
      }
    }
  ]
}
```

Test the pipeline (with the service running):

```bash
python tests/fake_zmq_sender.py   # sends 5 batches of 3 events; check Adminer
```

---

## Trade Tracker (Equities Team)

A web dashboard for tracking positions, P&L, and portfolio metrics vs SPY.

### How data gets in (current reality)

- **Portfolio XLSX upload** (the only working import): on the **Import** page,
  upload an `.xlsx`/`.xlsm` file whose sheets have columns
  `Ticker | Date Acquired | Amount | Price Acquired`. Everything is recorded
  under a single `PORTFOLIO` account, and each upload replaces the previous
  positions. This is **not** a Fidelity/IBKR export format — see the caveats
  below.
- **Prices**: yfinance, refreshed automatically in the background.
- **IBKR**: a cloud Web API client exists (see setup below) and the session
  connects, but it **cannot yet pull positions or pricing** — so IBKR data is
  not usable today.

> **Known gaps (tracked in `docs/REPO_AUDIT.md`):** the Fidelity/IBKR **CSV**
> parsers are written but not wired to any endpoint; the import is single-account
> only; Google SSO is built in the frontend but not enforced by the backend; and
> parts of the Dashboard UI are visually broken. Don't assume a feature works
> just because it appears in the UI.

---

## Deploying to Production (Railway + Cloudflare Pages)

The backend deploys to **Railway**, the frontend to **Cloudflare Pages**
(chosen over Vercel — free tier, no new vendor to evaluate), linked via env
vars (no shared secrets file). Google SSO is enforced server-side via
`AuthMiddleware` in `main.py` — `AUTH_ENABLED=true` actually does something
now, it's not a no-op.

Full step-by-step runbooks, in order:

1. [`docs/DEPLOY_RAILWAY.md`](docs/DEPLOY_RAILWAY.md) — backend + Postgres
2. [`docs/DEPLOY_GOOGLE_OAUTH.md`](docs/DEPLOY_GOOGLE_OAUTH.md) — domain-restricted Google sign-in
3. [`docs/DEPLOY_CLOUDFLARE_PAGES.md`](docs/DEPLOY_CLOUDFLARE_PAGES.md) — frontend

> **Railway gotcha:** don't use `${VAR:-default}` in `railway.toml` — Railway's
> templating uses `${{...}}` and the two conflict. Put shell-expansion logic in
> the Dockerfile `CMD` (`sh -c "..."`).

### Verifying the deploy

Quick commands for checking the live Railway backend without needing the
frontend deployed yet.

```bash
# 1. Basic health + DB connectivity check
curl https://<your-railway-domain>/health
# expect: {"status":"ok","database":"connected",...}

# 2. Confirm auth is actually enforced (should reject with no token)
curl https://<your-railway-domain>/trades
# expect: {"detail":"Not authenticated"} with a 401, if AUTH_ENABLED=true

# 3. Run the dashboard locally against the live Railway backend, instead of
#    a local Docker stack — useful before the frontend itself is deployed
cd trade-tracker/frontend
VITE_API_BASE_URL=https://<your-railway-domain> npm run dev
```

For fully local development against a throwaway local database instead
(doesn't touch Railway at all), use `docker compose up --build` per the
"Running Locally" section above.

---

## IBKR Web API Setup

IBKR's Web API uses **RSA key-based OAuth 2.0** (JWT bearer, server-to-server) —
no browser login, no redirect URL, no desktop gateway, no port 5001. (Any older
docs mentioning a "Client Portal Gateway", port 5001, or "Pangolin" are stale.)

**How it works:** your RSA private key signs a JWT → IBKR returns a bearer token
→ that token + your IBKR username + your server's outbound IP creates a session
→ the session is kept alive with a tickle every 60s. It reconnects on its own.

> **Status:** this gets as far as a connected session, but **pulling positions
> and pricing does not work yet** — see `docs/REPO_AUDIT.md` (IBKR project). Leave
> `IBKR_ENABLED=false` until that's fixed.

**Credentials** live in Ryan's zip (`privatekey.pem`) and ticket #619394. Set in
`.env`:

```
IBKR_ENABLED=true

# Paper account:
IBKR_CLIENT_ID=DekalbCapital-Paper
IBKR_CLIENT_KEY_ID=main
IBKR_CREDENTIAL=dekalbcapitalpaper
IBKR_ACCOUNT_ID=DFP321877

# Live account: swap to DekalbCapital-Prod / dekalbcapital3 / F16173704

# RSA private key — full privatekey.pem contents (base64 blob, or PEM with \n escapes):
IBKR_PRIVATE_KEY=...

# Outbound IP of the server IBKR will see:
#   Local dev: google "what is my ip"
#   Railway:   Settings → Networking → Outbound Static IP (Pro plan)
IBKR_SERVER_IP=YOUR.SERVER.IP.HERE
```

IBKR ties sessions to an IP. If the IP changes, `POST /ibkr/connect` re-establishes
the session. There is **no** `IBKR_CLIENT_SECRET` or `IBKR_REDIRECT_URI` — those
belong to a different OAuth flow that this project does not use.

---

## Trade Tracker API Reference

Full interactive docs at `/docs` (Swagger UI). Endpoints have no path prefix.

| Endpoint | What it does |
|---|---|
| `GET /health` | Health check (DB, IBKR flag, trade count, latest snapshot) |
| **Auth** | (router exists but is not registered in `main.py` yet) |
| `GET /auth/config` | Auth config for the frontend |
| `POST /auth/verify` | Verify a Google ID token |
| **IBKR** | (connects, but data calls don't return yet) |
| `GET /ibkr/status` | Is IBKR connected? |
| `POST /ibkr/connect` | Trigger reconnect |
| `GET /ibkr/account` | Account NAV/balances |
| `GET /ibkr/positions` | Open positions |
| `POST /ibkr/sync/positions` | Pull positions → `imported_positions` |
| `POST /ibkr/sync/trades` | Pull recent fills → `trades` |
| **Portfolio** | |
| `GET /portfolio/summary` | Combined + per-account P&L snapshot |
| `GET /portfolio/positions` | Open positions with live pricing |
| `GET /portfolio/performance?period=ytd` | NAV time series + SPY overlay |
| `GET /portfolio/metrics?period=ytd` | Beta, std dev, Sharpe, alpha, drawdown, win rate |
| `POST /portfolio/update-all` | Refresh prices + write a snapshot now |
| `POST /portfolio/snapshots/generate` | Generate today's NAV snapshot |
| `POST /portfolio/snapshots/backfill` | Backfill missing historical snapshots |
| **Trades** | |
| `GET /trades` | Trade log — filter by symbol, side, label, date |
| `PATCH /trades/{id}/label` | Set label, hedge flag, notes |
| `DELETE /trades/reset` | Wipe all trades + snapshots (irreversible) |
| **Imports** | |
| `POST /import/trades` | Upload portfolio `.xlsx` (aliases: `/import/fidelity`, `/import/ibkr`) |
| `GET /import/history` | List past imports |
| **Market** | |
| `GET /market/quote/{symbol}` | Current price (IBKR when working, else yfinance) |
| `GET /market/quotes?symbols=AAPL,MSFT` | Batch quotes |
| `GET /market/history/{symbol}` | Historical bars |
| `GET /market/spy` | SPY benchmark data |

Period options: `1m`, `3m`, `6m`, `ytd`, `1y`

---

## Adminer — DB Browser

`http://localhost:8080`

| Team | Database |
|---|---|
| Quant | System: PostgreSQL / Server: postgres / DB: **trading** |
| Equities | System: PostgreSQL / Server: postgres / DB: **trade_tracker** |

User: `postgres` — Password: `postgres`
