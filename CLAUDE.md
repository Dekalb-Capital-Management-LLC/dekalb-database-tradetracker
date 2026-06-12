# CLAUDE.md

Guidance for Claude Code when working in this repo. Read this first, then [`README.md`](README.md) for setup/usage and [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) for current known issues and roadmap.

## What this repo is

A monorepo for DeKalb Capital Management with two mostly-independent halves that share a Postgres instance:

- **`ingestion-service/`** — quant team. ZMQ listener that routes live trading-engine events into Postgres (`trading` DB) and QuestDB.
- **`trade-tracker/`** — equities team. FastAPI backend (`trade-tracker/api`) + React/Vite dashboard (`trade-tracker/frontend`) for tracking trades, positions, and portfolio performance, with optional IBKR integration. Uses its own Postgres database (`trade_tracker`).

Other top-level dirs: `schemas/` (SQL for both DBs), `ibkr-gateway/` (IBKR Client Portal Gateway config, gitignored binary), `tests/` (manual test scripts), `docs/` (project status + Linear/feature docs).

When working on one half, you generally don't need to touch the other — they're separate Dockerfiles, separate requirements, separate Postgres databases (`trading` vs `trade_tracker`), separate teams.

## Hard rules

- **Never read, cat, or grep the contents of `.env`.** It holds live credentials (IBKR keys, Polygon API key, etc.). If you need to know what variables exist, read `.env.example` instead — it's kept in sync and documents every variable with comments. If `.env.example` is missing something, ask the user what the variable is for rather than reading `.env`.
- Don't fix everything at once. This codebase has a lot of unfinished/rough edges (see `docs/REPO_AUDIT.md`). Small, well-scoped fixes are good; large speculative refactors should become Linear issues (documented in `docs/REPO_AUDIT.md`) instead, so the team can prioritize.

## Trade Tracker API (`trade-tracker/api/`)

FastAPI + asyncpg, Python 3.11.

- `config.py` — all config via `os.getenv()`. `DATABASE_URL` (Railway-style) takes precedence; falls back to discrete `DB_HOST`/`POSTGRES_*` vars (Docker/local). `DB_SSL` controls whether asyncpg gets an SSL context (`require` in prod, unset/`disable` locally).
- `db.py` — connection pool (`asyncpg.create_pool`). On startup, auto-creates the database and applies `schemas/trade_tracker_schema.sql` if it's empty (`_ensure_db_exists` / `_apply_schema_if_empty`) — no manual migration step for local dev.
- `main.py` — app setup, CORS, `AuthMiddleware`, `/health`. Auth bypass paths: `/health`, `/docs`, `/redoc`, `/openapi.json`, `/auth/*`.
- `routers/` — one file per resource (`auth`, `portfolio`, `trades`, `imports`, `market`, `ibkr`). Keep this 1:1 mapping when adding endpoints.
- `services/` — business logic, kept separate from routers:
  - `auth.py` — Google ID token verification against Google's JWKS (cached).
  - `ibkr_client.py` — thin wrapper over the IBKR Client Portal Gateway REST API (`https://localhost:5001` locally, `https://host.docker.internal:5001` in Docker). **Direct connection, no proxy/VPN** — if you see references to "Pangolin" or port 5000 anywhere, that's stale leftover docs from an earlier architecture; fix them when you find them.
  - `market_data.py` — yfinance by default; routes through IBKR if `IBKR_ENABLED=true` and the gateway is reachable/authenticated.
  - `portfolio_metrics.py` — beta/std dev/Sharpe/alpha/drawdown/win-rate from `portfolio_snapshots`. `RISK_FREE_RATE_ANNUAL` is currently hardcoded to `0.0`.
  - `fidelity_parser.py` — Fidelity CSV → `trades` rows.
- `models/schemas.py` — Pydantic request/response models.

### Auth

Gated by `AUTH_ENABLED` (default `false`). When `true`: Google Workspace SSO via Google Identity Services, ID token verified server-side, restricted to `@<ALLOWED_EMAIL_DOMAIN>`. Frontend sends `Authorization: Bearer <id_token>` on every request; `AuthContext.tsx` handles the sign-in flow and `client.ts` (`handle401`) redirects to `/login` on expiry. There's no token-refresh flow yet — tokens expire after ~1h.

### Database

- Two separate Postgres databases in the same instance: `trading` (quant, schema = `schemas/postgresql_schema.sql`) and `trade_tracker` (equities, schema = `schemas/trade_tracker_schema.sql`). The trade-tracker API only ever touches `trade_tracker`.
- `trade_tracker` tables: `trades`, `portfolio_snapshots`, `fidelity_imports`, `cash_flows`. Note the partial unique indexes on `portfolio_snapshots` to handle `account_id IS NULL` (combined portfolio) vs per-account snapshots.

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
- IBKR Gateway is a desktop app requiring interactive 2FA login — it cannot run on Railway. Production should run with `IBKR_ENABLED=false` (yfinance fallback) until/unless a headless re-auth strategy exists.

## Conventions

- Config: always `os.getenv('VAR', default)` in `config.py`, never scattered `os.environ` calls in business logic.
- Error handling in writers/services: wrap external calls (DB, ZMQ, HTTP, IBKR) in try/except, log with `logging.error()`/`logging.info()`, return `True`/`False` or `None` rather than raising — these are long-running services that shouldn't crash on a single bad event/request.
- New env vars: add to `.env.example` with a comment, and to `config.py` with a sensible default. Document in README if it affects setup/deployment.
- Frontend env vars must be prefixed `VITE_` (Vite only exposes those to client code) and typed in `vite-env.d.ts`.

## Project management

Planning happens in Linear, not GitHub Issues. See [`docs/linear/`](docs/linear/) for the project/issue templates and GitHub↔Linear workflow conventions, and [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) for the current backlog. New bugs/features discovered while working in this repo should generally become new entries in `docs/REPO_AUDIT.md` rather than being silently fixed or left as TODO comments — flag them to the user. User-facing changes should also update [`docs/FEATURES.md`](docs/FEATURES.md).
