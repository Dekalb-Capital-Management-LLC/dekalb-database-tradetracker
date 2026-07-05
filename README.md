# DeKalb Database — Monorepo

Backend infrastructure for DeKalb Capital. Handles live trading-event ingestion
(quant team) and the equities team's trade-tracker dashboard. Two
mostly-independent halves share one Postgres instance.

> **⚠️ Status (2026-07-09):** The ingestion service is solid. The **Trade
> Tracker's equities half is largely working now** — Google SSO auth is fully
> wired and enforced (gated by `AUTH_ENABLED`), IBKR pulls real
> positions/pricing/trade history, Fidelity CSV import is live via a
> preview/commit wizard, and the production deploy is in progress (Railway
> backend is live; Google OAuth + Cloudflare frontend are being finished now).
> **[`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) is the source of truth for
> what's still outstanding** (schema drift, no automated tests, a couple of
> approximate metrics) — read it before assuming something is finished, but
> don't assume it's broken either. [`docs/FEATURES.md`](docs/FEATURES.md) has
> the per-feature status table.

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
│   │   │   ├── auth.py             # /auth/* — Google SSO, registered + enforced in main.py
│   │   │   ├── ibkr.py             # /ibkr/* — connect, account, positions, sync (real data)
│   │   │   ├── portfolio.py        # /portfolio/* — summary, positions, metrics
│   │   │   ├── trades.py           # /trades/* — trade log, labels
│   │   │   ├── imports.py          # /import/* — Fidelity CSV wizard + legacy XLSX upload
│   │   │   ├── dashboard.py        # /dashboard/* — capability manifest
│   │   │   └── market.py           # /market/* — quotes, history, SPY
│   │   ├── services/
│   │   │   ├── auth.py             # Google ID-token verification, called on every request
│   │   │   ├── ibkr_client.py      # IBKR cloud Web API client (RSA OAuth 2.0) — live data
│   │   │   ├── universal_parser.py # parse_portfolio_xlsx — legacy single-account XLSX import
│   │   │   ├── fidelity_parser.py  # Fidelity CSV parser — live, wired to /import/preview+commit
│   │   │   ├── ibkr_parser.py      # IBKR Activity CSV parser (unreferenced — superseded by ibkr_client.py)
│   │   │   ├── first_rate_data.py  # FirstRateData ZIP/directory bundle reader
│   │   │   ├── market_data.py      # FirstRateData + IBKR/yfinance market-data cache
│   │   │   ├── dashboard_capabilities.py
│   │   │   └── portfolio_metrics.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── railway.toml
│   └── frontend/                   # React + Vite + Tailwind
│       ├── src/
│       │   ├── pages/              # Dashboard, Login
│       │   ├── auth/               # AuthContext, Login
│       │   └── components/         # FidelityUpdateWizard, PositionsTable, PerformanceChart, etc.
│       ├── wrangler.jsonc          # Cloudflare Workers static-assets deploy config
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

IBKR credentials are optional. For local market-data proxy testing, download the
FirstRateData sample ZIP and set `FIRST_RATE_DATA_PATH` to that file; the XLSX
import still supplies holdings.

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

### How data gets in

- **Fidelity CSV import** (the primary path): on the **Import** tab, upload a
  Fidelity Activity/Orders CSV (trade history) or Portfolio Positions CSV
  (holdings, including multi-account via the Account Name/Number columns).
  Goes through a preview/diff wizard (`FidelityUpdateWizard.tsx`) before
  committing. Money-market/cash-sweep funds get $1-NAV synthetic positions;
  options are skipped.
- **IBKR** (live): the cloud Web API client (RSA OAuth, see setup below) pulls
  real positions, live pricing, and trade history — this used to be the
  biggest blocker and is now fixed.
- **Portfolio XLSX upload** (legacy, still works): a custom multi-sheet XLSX
  with columns `Ticker | Date Acquired | Amount | Price Acquired`, recorded
  under a single `PORTFOLIO` account. Not a Fidelity/IBKR export format —
  kept as a secondary path.
- **Prices**: IBKR-first when `IBKR_ENABLED=true`, yfinance fallback
  otherwise, refreshed automatically in the background.


> **Known gaps (tracked in `docs/REPO_AUDIT.md`):** schema drift (three tables
> only exist as runtime migrations, not in the schema file), no automated
> tests, `RISK_FREE_RATE_ANNUAL` hardcoded to `0.0`, no token-refresh flow for
> Google sign-in (expires ~1h, hard-redirects to `/login` on expiry). Don't
> assume something's broken just because an older doc said so — check
> `docs/REPO_AUDIT.md` for the current list.

---

## Deploying to Production (Railway + Cloudflare)

### The shape of it, in plain terms

The Trade Tracker has two halves that deploy to two different places:

- **Backend** (the API/database logic) → **Railway**.
- **Frontend** (the dashboard people open in a browser) → **Cloudflare**.

This is entirely separate infrastructure from DeKalb's main company website
at `dekalbcapitalmanagement.com`, which runs on **Vercel** and is untouched
by any of this — different repo, different hosting account, no shared DNS
unless you deliberately connect them (see custom domain, below).

The two Trade Tracker halves are linked purely by env vars, no shared
secrets file: `FRONTEND_URL` (on Railway, drives CORS) and
`VITE_API_BASE_URL` (on Cloudflare, baked into the frontend at build time so
it knows which API to call). Google SSO is enforced server-side via
`AuthMiddleware` in `main.py` — `AUTH_ENABLED=true` actually does something,
it's not a no-op.

### About the Cloudflare URL

Cloudflare's dashboard now deploys git-connected static sites as a **Worker
with static assets**, not the older "Pages" product (even though the nav
item is still labeled "Workers & Pages" and it's easy to click through what
looks like a Pages flow — see `docs/DEPLOY_CLOUDFLARE_PAGES.md` step 1 for
the tell-tale signs and the `wrangler.jsonc` config this requires).

Practically, that just changes the free default URL you get: instead of
`*.pages.dev` it's `*.<subdomain>.workers.dev` — and unlike Pages, it isn't
enabled by default; flip it on under the project's **Domains & Routes** tab.
That URL is a **real, permanent, fully-working production address** the
moment it's live, not a placeholder or local link — you can run on it
indefinitely with no custom domain at all.

A prettier URL (e.g. `tradetracker.dekalbcapitalmanagement.com`) is an
optional later step — one DNS record added wherever `dekalbcapitalmanagement.com`'s
DNS is actually managed, which does **not** need to be Cloudflare and does
**not** touch the existing Vercel site, as long as you scope it to a
subdomain rather than routing the root domain through Cloudflare.

### Runbooks, in order

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

> **Status:** working — positions, live pricing, and trade history all pull
> real data now (retry logic for IBKR's first-call-empty quirk, a `portfolio2`
> fallback, US-listed conid disambiguation, 429-backoff). Set
> `IBKR_ENABLED=true` to use it; see `docs/REPO_AUDIT.md` for remaining edge
> cases.

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
| **Auth** | Registered in `main.py`; `AuthMiddleware` enforces it on every request except `/health`, `/docs`, `/auth/*` when `AUTH_ENABLED=true` |
| `GET /auth/config` | Auth config for the frontend |
| `POST /auth/verify` | Verify a Google ID token |
| `GET /auth/me` | Current authenticated user |
| **IBKR** | Connects and returns real data |
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
| `GET/POST/PATCH/DELETE /portfolio/cash-flows` | Deposits/withdrawals — excluded from return/Sharpe/drawdown calcs |
| **Trades** | |
| `GET /trades` | Trade log — filter by symbol, side, label, date |
| `PATCH /trades/{id}/label` | Set label, hedge flag, notes |
| `DELETE /trades/reset` | Wipe all trades + snapshots (irreversible) |
| **Dashboard** | |
| `GET /dashboard/capabilities` | Stable dashboard module/capability manifest for current and future quant panels |
| **Imports** | |
| `POST /import/preview` | Upload Fidelity CSV/XLSX → diff preview (used by `FidelityUpdateWizard.tsx`) |
| `POST /import/commit` | Commit a previewed import |
| `POST /import/trades` | Legacy portfolio `.xlsx` upload (hardcoded to `account_id='PORTFOLIO'`) |
| `GET /import/history` | List past imports |
| **Market** | |
| `GET /market/provider/status` | Active market-data provider order/config |
| `GET /market/quote/{symbol}` | Current price (FirstRateData when configured, else IBKR/yfinance) |
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
