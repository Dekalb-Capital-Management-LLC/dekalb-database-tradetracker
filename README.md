# DeKalb Database — Monorepo

Backend infrastructure for DeKalb. Houses the database layer, event ingestion pipeline, and trade tracker API.

---

## Repo Structure

```
dekalb-database/
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
│   │   │   ├── portfolio.py    # /portfolio/*
│   │   │   ├── trades.py       # /trades/*
│   │   │   ├── imports.py      # /import/fidelity
│   │   │   ├── market.py       # /market/*
│   │   │   └── ibkr.py         # /ibkr/*
│   │   ├── services/
│   │   │   ├── ibkr_client.py       # IBKR OAuth cloud API client
│   │   │   ├── market_data.py       # yfinance (IBKR when gateway is on)
│   │   │   ├── portfolio_metrics.py # beta, std dev, sharpe, alpha
│   │   │   └── fidelity_parser.py   # Fidelity CSV parser
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── frontend/               # React + Vite dashboard
│
├── ibkr-gateway/               # IBKR Client Portal Gateway config
│   └── conf.yaml.example       # Copy to conf.yaml
│
├── schemas/
│   ├── postgresql_schema.sql       # orders, positions, accounts (quant team)
│   ├── questdb_schema.sql          # time-series tables (apply via QuestDB console)
│   └── trade_tracker_schema.sql    # trades, snapshots, imports (equities team)
│
├── tests/
│   └── fake_zmq_sender.py      # Send test events to ingestion service
│
├── .env.example                # Copy to .env and fill in your values
└── docker-compose.yml
```

---

## Quick Start

```bash
# 1. Copy env file and fill in your values
cp .env.example .env
# at minimum set IBKR_ACCOUNT_ID if you have it

# 2. Start all services
docker compose up --build

# 3. Check the API
curl http://localhost:8000/health

# 4. Swagger docs
open http://localhost:8000/docs

# 5. Database GUI (Adminer)
open http://localhost:8080
# System: PostgreSQL | Server: postgres | User: postgres | Password: postgres
# Equities DB: trade_tracker  |  Quant DB: trading
```

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

> **IBKR:** Primary integration is OAuth cloud API (`api.ibkr.com`) via `.env` — no desktop gateway required. See [IBKR OAuth Setup](#ibkr-oauth-cloud-api-setup) below. Legacy Client Portal Gateway (port 5001) is optional for local dev only.

---

## Trade Tracker API Endpoints

| Endpoint | What it does |
|---|---|
| `GET /health` | Service health check |
| `GET /portfolio/summary` | Combined + per-account P&L |
| `GET /portfolio/positions` | Open positions with live P&L |
| `GET /portfolio/performance?period=ytd` | NAV time series + SPY overlay |
| `GET /portfolio/metrics?period=ytd` | Beta, std dev, Sharpe, alpha, max drawdown |
| `POST /portfolio/snapshots/generate` | Store today's NAV snapshot |
| `GET /trades` | Full trade log with filters |
| `PATCH /trades/{id}/label` | Label a trade |
| `POST /import/fidelity` | Upload Fidelity CSV |
| `GET /market/quote/{symbol}` | Current price (yfinance or IBKR) |
| `GET /ibkr/status` | OAuth connection + positions probe |
| `GET /ibkr/account` | Live NAV + balances from IBKR |
| `GET /ibkr/positions` | Live open positions from IBKR (no DB sync) |
| `POST /ibkr/sync/trades` | Import IBKR buy/sell history into `trades` table |

**Automation:** `snapshot-cron` calls `POST /portfolio/snapshots/generate` hourly. Trade sync runs once on API startup (OAuth) and via `POST /ibkr/sync/trades` or the Trades page "Sync IBKR" button.

---

## IBKR OAuth Cloud API Setup

The trade tracker uses **IBKR's cloud Web API** (`api.ibkr.com`) with RSA key-based OAuth 2.0 — server-to-server, no browser login, no desktop gateway. When disabled, the API falls back to **yfinance** for quotes and historical data.

### What works on OAuth

| Capability | Source |
|---|---|
| Live positions, NAV, unrealized P&L | `/portfolio/*` endpoints |
| Trade history import | `/pa/transactions` (Portfolio Analyst) |
| Quotes for held symbols | Portfolio `mktPrice` (snapshot fallback when iserver unavailable) |
| Historical charts / SPY benchmark | yfinance |

### 1. Register OAuth app with IBKR

Obtain from your IBKR account manager: `IBKR_CLIENT_ID`, `IBKR_CREDENTIAL`, RSA private key, and register your **public outbound IP** in the OAuth app settings.

### 2. Configure `.env`

```bash
cp .env.example .env
```

```env
IBKR_ENABLED=true
IBKR_ACCOUNT_ID=U1234567          # client account (U*), not FA master (F*)
IBKR_CLIENT_ID=your-client-id
IBKR_CLIENT_KEY_ID=main
IBKR_CREDENTIAL=your-credential
IBKR_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
IBKR_SERVER_IP=203.0.113.10       # your machine's public IP (see below)
IBKR_API_BASE_URL=https://api.ibkr.com
```

### 3. IP pinning (`IBKR_SERVER_IP`)

IBKR ties OAuth sessions to your outbound IP. Set `IBKR_SERVER_IP` to the IP registered in the IBKR portal.

| Environment | What to use |
|---|---|
| Local dev | Your home public IP (`curl https://api.ipify.org`) |
| Railway / cloud | Static outbound IP (Railway Pro plan) |

On IP change: update IBKR OAuth app settings, update `.env`, restart `trade-tracker`.

The API logs a warning at startup if detected IP ≠ configured IP.

### 4. Start and verify

```bash
docker compose up --build postgres trade-tracker trade-tracker-frontend
```

```bash
curl http://localhost:8000/ibkr/status
# oauth_connected: true, positions_count: 16, ...

curl http://localhost:8000/ibkr/positions
curl http://localhost:8000/ibkr/account
curl -X POST http://localhost:8000/ibkr/sync/trades
```

### Production (Railway)

- Set all OAuth env vars in Railway dashboard
- Use static outbound IP; set `IBKR_SERVER_IP` to match
- `IBKR_ENABLED=true` is safe when `/ibkr/positions` returns data
- Quotes for symbols you don't hold still use yfinance until iserver market data is fully available

### Legacy: Client Portal Gateway (optional)

For local development without OAuth credentials, you can still use the desktop Client Portal Gateway on port 5001. Leave `IBKR_CLIENT_ID` unset and set `IBKR_GATEWAY_URL`. This path is deprecated for production.

---

## Importing Trades

### From Fidelity (CSV)

1. In Fidelity: Accounts & Trade > Portfolio > select account > Activity & Orders > Download CSV
2. `POST /import/fidelity` with the file and an `account_id` string (e.g. `FIDELITY_MAIN`)
3. Trades land unlabeled — use `PATCH /trades/{id}/label` to categorize them
4. Labels: `event-driven`, `hedge`, `long-term`, `short-term`

### From IBKR (live sync)

Trades import automatically on API startup when OAuth is enabled. To refresh manually:

```bash
curl -X POST http://localhost:8000/ibkr/sync/trades
```

Or use **Sync IBKR** on the Trades page. History comes from IBKR Portfolio Analyst (`/pa/transactions`, up to ~2 years per symbol). Positions are live-read via `GET /ibkr/positions` — not stored in the database.

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
| `trading` | `postgresql_schema.sql` | orders, positions, accounts, strategies |
| `trade_tracker` | `trade_tracker_schema.sql` | trades, portfolio_snapshots, fidelity_imports, cash_flows |

QuestDB tables are created manually — open `http://localhost:9000` and run `schemas/questdb_schema.sql`.

---

## Portfolio Metrics

Calculated from daily NAV snapshots in `portfolio_snapshots`. The `snapshot-cron` container runs `POST /portfolio/snapshots/generate` every hour automatically.

| Metric | Formula |
|---|---|
| Beta | Cov(portfolio, SPY) / Var(SPY) |
| Std Dev | Daily std dev x sqrt(252) |
| Sharpe | Annualized return / Annualized std dev |
| Alpha | Portfolio return - Beta x SPY return |
| Max Drawdown | Max peak-to-trough NAV decline |
