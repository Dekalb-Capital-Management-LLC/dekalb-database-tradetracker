# CLAUDE.md

Guidance for Claude Code when working in this repo. Read this first, then [`README.md`](README.md) for setup/usage and [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) for current known issues and roadmap.

## What this repo is

A monorepo for DeKalb Capital Management with two mostly-independent halves that share a Postgres instance:

- **`ingestion-service/`** — quant team. ZMQ listener that routes live trading-engine events into Postgres (`trading` DB) and QuestDB.
- **`trade-tracker/`** — equities team. FastAPI backend (`trade-tracker/api`) + React/Vite dashboard (`trade-tracker/frontend`) for tracking trades, positions, and portfolio performance, with optional IBKR integration. Uses its own Postgres database (`trade_tracker`).

Other top-level dirs: `schemas/` (SQL for both DBs), `tests/` (manual test scripts), `docs/` (project status + Linear/feature docs).

When working on one half, you generally don't need to touch the other — they're separate Dockerfiles, separate requirements, separate Postgres databases (`trading` vs `trade_tracker`), separate teams.

> **Important — the equities half is not production-ready.** Large parts of the Trade Tracker are broken or unverified: auth isn't enforced, IBKR connects but can't pull positions/pricing, the Fidelity CSV import is dead code, and parts of the dashboard are visually broken. **`docs/REPO_AUDIT.md` is the source of truth for what actually works vs. what's broken — read it before assuming a feature works.** Don't trust feature names at face value (e.g. "Fidelity import" doesn't ingest Fidelity CSVs).

## Hard rules

- **Never read, cat, or grep the contents of `.env`.** It holds live credentials (IBKR keys, Polygon API key, etc.). If you need to know what variables exist, read `.env.example` instead — it's kept in sync and documents every variable with comments. If `.env.example` is missing something, ask the user what the variable is for rather than reading `.env`.
- Don't fix everything at once. This codebase has a lot of unfinished/rough edges (see `docs/REPO_AUDIT.md`). Small, well-scoped fixes are good; large speculative refactors should become Linear issues (documented in `docs/REPO_AUDIT.md`) instead, so the team can prioritize.

## Trade Tracker API (`trade-tracker/api/`)

FastAPI + asyncpg, Python 3.11.

- `config.py` — all config via `os.getenv()`. `DATABASE_URL` (Railway-style) takes precedence; falls back to discrete `DB_HOST`/`POSTGRES_*` vars (Docker/local). `DB_SSL` controls whether asyncpg gets an SSL context (`require` in prod, unset/`disable` locally).
- `db.py` — connection pool + auto-migrations. See the Database section below.
- `main.py` — app setup, CORS, startup hooks, `/health`. **Known gap:** it imports the auth router and `AuthMiddleware` pieces but **never registers them** — so auth is not actually enforced and `/auth/*` returns 404. Don't assume auth works; see the Auth section below and `docs/REPO_AUDIT.md`. (Note: `FRONTEND_URL` is appended to CORS origins as a single string and is *not* split on commas, despite the docs claiming comma-separated support.)
- `routers/` — one file per resource (`auth`, `portfolio`, `trades`, `imports`, `market`, `ibkr`). Keep this 1:1 mapping when adding endpoints. (`auth` exists but isn't wired into `main.py` yet.)
- `services/` — business logic, kept separate from routers:
  - `auth.py` — Google ID token verification against Google's JWKS (cached). Currently unused at runtime (no middleware calls it).
  - `ibkr_client.py` — client for the **IBKR cloud Web API** (`https://api.ibkr.com`), using **RSA key-based OAuth 2.0 / JWT bearer flow** (server-to-server, no browser login, no desktop gateway, no port 5001). Flow: RSA-signed JWT → bearer token → SSO session (with IBKR username + outbound IP) → `iserver` init → tickle every 60s. **Current real state: the session connects but positions/pricing calls return nothing usable — IBKR is effectively non-functional.** Any references to a "Client Portal Gateway", port 5001, "Pangolin", or port 5000 are stale from older architectures — fix them when you find them.
  - `market_data.py` — yfinance by default; routes through IBKR if `IBKR_ENABLED=true` and the session can return data. Since IBKR pricing doesn't work, this is effectively yfinance-only today.
  - `portfolio_metrics.py` — beta/std dev/Sharpe/alpha/drawdown/win-rate from `portfolio_snapshots`. `RISK_FREE_RATE_ANNUAL` is hardcoded to `0.0`; cash flows are not excluded, so metrics are approximate/wrong around deposits/withdrawals.
  - `universal_parser.py` — `parse_portfolio_xlsx`: the **only live import path**. Parses a custom multi-sheet XLSX (`Ticker | Date Acquired | Amount | Price Acquired`) into `trades` + `imported_positions`, hardcoded to `account_id='PORTFOLIO'`.
  - `fidelity_parser.py` / `ibkr_parser.py` — CSV parsers (Fidelity Activity/Positions, IBKR Activity Statement). **Currently dead code** — no router calls them; `/import/trades` only accepts XLSX. Don't assume CSV import works.
- `models/schemas.py` — Pydantic request/response models.

### Auth

Designed as: gated by `AUTH_ENABLED` (default `false`); when `true`, Google Workspace SSO via Google Identity Services, ID token verified server-side, restricted to `@<ALLOWED_EMAIL_DOMAIN>`; frontend sends `Authorization: Bearer <id_token>`, `AuthContext.tsx` handles sign-in, `client.ts` (`handle401`) redirects to `/login` on expiry.

**Reality: none of this is enforced.** `main.py` never registers the auth router or any `AuthMiddleware`, so `/auth/config`/`/auth/verify`/`/auth/me` 404, the frontend silently falls back to `auth_enabled: false`, and `AUTH_ENABLED=true` protects nothing. Wiring this up is a top item in `docs/REPO_AUDIT.md`. There's also no token-refresh flow.

### Database

- Two separate Postgres databases in the same instance: `trading` (quant, schema = `schemas/postgresql_schema.sql`) and `trade_tracker` (equities, schema = `schemas/trade_tracker_schema.sql`). The trade-tracker API only ever touches `trade_tracker`.
- `db.py` — connection pool (`asyncpg.create_pool`). On startup, auto-creates the database and applies `schemas/trade_tracker_schema.sql` if empty (`_ensure_db_exists` / `_apply_schema_if_empty`), then runs `_apply_migrations` (idempotent `CREATE TABLE IF NOT EXISTS`). **Schema drift warning:** the schema *file* only defines `trades`, `portfolio_snapshots`, `fidelity_imports`, `cash_flows`. Three more tables — `ibkr_tokens`, `instrument_conids`, `imported_positions` — exist *only* as runtime migrations in `db.py`, not in the schema file or older docs. The whole portfolio/positions path depends on `imported_positions`. Note the partial unique indexes on `portfolio_snapshots` for `account_id IS NULL` (combined) vs per-account snapshots.

## Frontend (`trade-tracker/frontend/`)

React 18 + Vite + TypeScript + Tailwind + Recharts + react-router-dom.

- `src/api/client.ts` — all API calls go through here (`get`/`post`/`patch`/`postForm`). `BASE = import.meta.env.VITE_API_BASE_URL || '/api'`:
  - Local dev: Vite proxy (`vite.config.ts`) forwards `/api/*` → `http://localhost:8000/*`, prefix stripped.
  - Docker: `nginx.conf` does the same proxying to `trade-tracker:8000`.
  - Vercel (prod): set `VITE_API_BASE_URL` to the Railway API URL — the frontend then calls the API directly cross-origin (CORS via `FRONTEND_URL` on the backend).
- `src/vite-env.d.ts` — typing for `import.meta.env.VITE_API_BASE_URL`. Add new `VITE_*` vars here when introduced.
- `src/auth/` — `AuthContext.tsx` (Google SSO state), `Login.tsx`.
- `src/pages/` — `Dashboard`, `Trades`, `Import`, etc. `src/components/Layout.tsx` has the sidebar nav — add new pages there.

## Ingestion Service (`ingestion-service/`)

ZMQ PULL socket on port 5555 → routes events to Postgres (`trading` DB) and/or QuestDB based on `event['type']` (`execution`, `order_update`, `log`, `signal`). See `router.py` for the routing table. This service is functionally complete; changes here should be rare and quant-team-driven.

## Deployment

- **Frontend → Vercel**, root directory `trade-tracker/frontend`, env var `VITE_API_BASE_URL` = Railway API URL.
- **Backend → Railway**, root directory `trade-tracker/api`, uses `railway.toml` (Dockerfile build, `/health` healthcheck) + a Postgres plugin (`DATABASE_URL` auto-injected).
- The two are linked via `FRONTEND_URL` (Railway env var, comma-separated list of allowed origins for CORS) and `VITE_API_BASE_URL` (Vercel env var). Full step-by-step is in the README's "Deploying to Production" section.
- **Railway gotcha**: don't put `${VAR:-default}` bash-style syntax in `railway.toml` — Railway's own templating uses `${{...}}` and the two conflict. Put shell-expansion logic in the Dockerfile's `CMD` (shell form, `sh -c "..."`) instead.
- **Vercel gotcha**: `vercel.json` rewrites can't reference env vars, so don't hardcode a Railway URL there — use `VITE_API_BASE_URL` (build-time env var) in `client.ts` instead.
- **IBKR in production**: the IBKR client is the cloud Web API (RSA OAuth, server-to-server), so it *can* technically run headless on Railway — it just needs a stable outbound IP (`IBKR_SERVER_IP`, Railway Pro static IP) that matches what IBKR sees. But since positions/pricing don't work yet, production must run `IBKR_ENABLED=false` (yfinance fallback) until that's fixed. (This replaces the old "desktop gateway can't run on Railway" guidance — that gateway architecture is gone.)
- **The deploy has never been completed or smoke-tested.** The README's deploy section also contradicts itself and references env vars that don't exist (`IBKR_CLIENT_SECRET`, `IBKR_REDIRECT_URI`) — see `docs/REPO_AUDIT.md` (Docs cleanup).

## Conventions

- Config: always `os.getenv('VAR', default)` in `config.py`, never scattered `os.environ` calls in business logic.
- Error handling in writers/services: wrap external calls (DB, ZMQ, HTTP, IBKR) in try/except, log with `logging.error()`/`logging.info()`, return `True`/`False` or `None` rather than raising — these are long-running services that shouldn't crash on a single bad event/request.
- New env vars: add to `.env.example` with a comment, and to `config.py` with a sensible default. Document in README if it affects setup/deployment.
- Frontend env vars must be prefixed `VITE_` (Vite only exposes those to client code) and typed in `vite-env.d.ts`.

## Project management

Planning happens in Linear, not GitHub Issues. See [`docs/linear/`](docs/linear/) for the project/issue templates and GitHub↔Linear workflow conventions, and [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) for the current backlog. New bugs/features discovered while working in this repo should generally become new entries in `docs/REPO_AUDIT.md` rather than being silently fixed or left as TODO comments — flag them to the user. User-facing changes should also update [`docs/FEATURES.md`](docs/FEATURES.md).
