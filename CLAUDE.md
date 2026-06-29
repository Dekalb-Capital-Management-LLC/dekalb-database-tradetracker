# CLAUDE.md

Guidance for Claude Code when working in this repo. Read this first, then [`README.md`](README.md) for setup/usage and [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) for current known issues and roadmap.

_Last verified against the code: 2026-06-28._

## What this repo is

A monorepo for DeKalb Capital Management with two mostly-independent halves that share a Postgres instance:

- **`ingestion-service/`** — quant team. ZMQ listener that routes live trading-engine events into Postgres (`trading` DB) and QuestDB.
- **`trade-tracker/`** — equities team. FastAPI backend (`trade-tracker/api`) + React/Vite dashboard (`trade-tracker/frontend`) for tracking trades, positions, and portfolio performance, with IBKR integration. Uses its own Postgres database (`trade_tracker`).

Other top-level dirs: `schemas/` (SQL for both DBs), `tests/` (manual test scripts), `docs/` (project status + Linear/feature docs).

When working on one half, you generally don't need to touch the other — they're separate Dockerfiles, separate requirements, separate Postgres databases (`trading` vs `trade_tracker`), separate teams.

> **Status — the equities half is largely working now.** As of late June 2026: Google SSO auth is fully wired and enforced (gated by `AUTH_ENABLED`), IBKR pulls real positions/pricing/trade history (not just a connection), Fidelity CSV import is live via a preview/commit wizard, the dashboard theme/nav issues are fixed, and the Railway + Cloudflare Pages + Google OAuth production deploy is in progress. **`docs/REPO_AUDIT.md` is still the source of truth for what's genuinely outstanding** (schema drift, no automated tests, a few approximate metrics) — read it before assuming something is finished, but don't assume it's broken either; verify against the code.

## Hard rules

- **Never read, cat, or grep the contents of `.env`.** It holds live credentials (IBKR keys, Polygon API key, etc.). If you need to know what variables exist, read `.env.example` instead — it's kept in sync and documents every variable with comments. If `.env.example` is missing something, ask the user what the variable is for rather than reading `.env`.
- Don't fix everything at once. This codebase has a lot of unfinished/rough edges (see `docs/REPO_AUDIT.md`). Small, well-scoped fixes are good; large speculative refactors should become Linear issues (documented in `docs/REPO_AUDIT.md`) instead, so the team can prioritize.

## Trade Tracker API (`trade-tracker/api/`)

FastAPI + asyncpg, Python 3.11.

- `config.py` — all config via `os.getenv()`. `DATABASE_URL` (Railway-style) takes precedence; falls back to discrete `DB_HOST`/`POSTGRES_*` vars (Docker/local). `DB_SSL` controls whether asyncpg gets an SSL context (`require` in prod, unset/`disable` locally).
- `db.py` — connection pool + auto-migrations. See the Database section below.
- `main.py` — app setup, CORS, startup hooks, `/health`. `AuthMiddleware` is registered (`app.add_middleware(AuthMiddleware)`) and `routers/auth.py` is included — auth is genuinely enforced when `AUTH_ENABLED=true`. `FRONTEND_URL` is split on commas into the CORS origin list (so multiple origins, e.g. a custom domain alongside a `.pages.dev` URL, both work).
- `routers/` — one file per resource (`auth`, `portfolio`, `trades`, `imports`, `market`, `ibkr`). Keep this 1:1 mapping when adding endpoints.
- `services/` — business logic, kept separate from routers:
  - `auth.py` — Google ID token verification against Google's JWKS (cached, RS256, audience + issuer + `hd`/domain checks). Called by `AuthMiddleware` on every request when `AUTH_ENABLED=true`.
  - `ibkr_client.py` — client for the **IBKR cloud Web API** (`https://api.ibkr.com`), using **RSA key-based OAuth 2.0 / JWT bearer flow** (server-to-server, no browser login, no desktop gateway, no port 5001). Flow: RSA-signed JWT → bearer token → SSO session (with IBKR username + outbound IP) → `iserver` init → tickle every 60s. Positions (`get_positions`, with `portfolio2` fallback + retries), live pricing (`get_market_snapshot_batch`, polls until field `31` populates), conid resolution (`get_conid`, prefers US-listed contracts), and trade history (`get_pa_transactions` + `/iserver/account/trades`, synced via `/ibkr/sync/trades`) all return real data now — this was the biggest blocker historically and is fixed. Any references to a "Client Portal Gateway", port 5001, "Pangolin", or port 5000 are stale from an older architecture.
  - `market_data.py` — IBKR-first when `IBKR_ENABLED=true` (quotes via snapshot batch + position-price fallback, history via `/iserver/marketdata/history`), falls back to yfinance per-symbol when IBKR has no data or is disabled.
  - `portfolio_metrics.py` — beta/std dev/Sharpe/alpha/drawdown/win-rate from `portfolio_snapshots`. Cash flows (deposits/withdrawals, via the `cash_flows` table + `/portfolio/cash-flows` CRUD) are now excluded from the return calc. Win-rate uses real FIFO-matched per-sell P&L (not just "did the sale produce positive cash", which used to read ~100% unconditionally). **Remaining gap:** `RISK_FREE_RATE_ANNUAL` is still hardcoded to `0.0` rather than an env var.
  - `universal_parser.py` — `parse_portfolio_xlsx`: parses a custom multi-sheet XLSX (`Ticker | Date Acquired | Amount | Price Acquired`) into `trades` + `imported_positions`. Used by the legacy `/import/trades` endpoint (hardcoded to `account_id='PORTFOLIO'`) and by `/import/preview` when the uploaded file is `.xlsx`/`.xlsm`.
  - `fidelity_parser.py` — **now live**, not dead code. Auto-detects and parses real Fidelity exports: Activity/Orders CSV (trade history) and Portfolio Positions CSV (holdings snapshot, including per-row multi-account support via the Account Name/Number columns). Money-market/cash-sweep funds (SPAXX/FDRXX/FCASH) get $1-NAV synthetic positions instead of being silently dropped; options (dash-prefixed or `YYMMDD[PC]strike` symbols) are still skipped. Wired in via `routers/imports.py`'s `/import/preview` + `/import/commit` (diff-and-confirm wizard flow), matching the frontend's `FidelityUpdateWizard.tsx`.
  - `ibkr_parser.py` — CSV parser for IBKR Activity Statements. Still unreferenced by any router — superseded by the live IBKR API integration above, which gets the same data without a manual export. Leave as-is unless asked to remove it.
- `models/schemas.py` — Pydantic request/response models.

### Auth

Gated by `AUTH_ENABLED` (default `false`); when `true`, Google Workspace SSO via Google Identity Services, ID token verified server-side against Google's JWKS, restricted to `@<ALLOWED_EMAIL_DOMAIN>`. `AuthMiddleware` in `main.py` runs on every request except `/health`, `/docs`, `/redoc`, `/openapi.json`, `/auth/*`; frontend sends `Authorization: Bearer <id_token>`, `AuthContext.tsx` handles sign-in/config fetch, `Login.tsx` renders the Google Identity Services button, `client.ts` (`handle401`) redirects to `/login` on a 401. **This is genuinely enforced** — `/auth/config`, `/auth/verify`, `/auth/me` are real endpoints, `request.state.user` is set on every authenticated request.

**Remaining gaps:** no token-refresh flow (ID tokens expire after ~1h; `handle401()` just hard-redirects to `/login`, no silent re-auth via `google.accounts.id.prompt()`). The Settings and Notifications buttons next to Sign-out in `Dashboard.tsx` still have no `onClick` (Sign-out itself is wired to `signOut()`).

**Frontend gotcha to watch for:** any code in `src/auth/` or a page that needs to hit the backend directly (bypassing `client.ts`'s `get`/`post` helpers) must still import the exported `BASE` constant from `client.ts`, never hardcode `/api/...`. Local dev's Vite proxy masks a hardcoded `/api` prefix; it silently breaks once the frontend and backend are on different domains (Cloudflare Pages + Railway). This bit us once already (`AuthContext.tsx` / `Login.tsx`), fixed 2026-06-28.

### Database

- Two separate Postgres databases in the same instance: `trading` (quant, schema = `schemas/postgresql_schema.sql`) and `trade_tracker` (equities, schema = `schemas/trade_tracker_schema.sql`). The trade-tracker API only ever touches `trade_tracker`.
- `db.py` — connection pool (`asyncpg.create_pool`). On startup, auto-creates the database and applies `schemas/trade_tracker_schema.sql` if empty (`_ensure_db_exists` / `_apply_schema_if_empty`), then runs `_apply_migrations` (idempotent `CREATE TABLE IF NOT EXISTS`). **Schema drift still open:** the schema *file* only defines `trades`, `portfolio_snapshots`, `fidelity_imports`, `cash_flows`. Three more tables — `ibkr_tokens`, `instrument_conids`, `imported_positions` — exist *only* as runtime migrations in `db.py`, not in the schema file. The whole portfolio/positions path depends on `imported_positions`. Note the partial unique indexes on `portfolio_snapshots` for `account_id IS NULL` (combined) vs per-account snapshots.

## Frontend (`trade-tracker/frontend/`)

React 18 + Vite + TypeScript + Tailwind + Recharts + react-router-dom (mounted via `BrowserRouter` in `main.tsx`, but there's currently only one route — `App.tsx` switches between `<Login>` and `<Dashboard>` based on auth state, it doesn't define `<Routes>`/`<Route>`).

- `src/api/client.ts` — all API calls go through here (`get`/`post`/`patch`/`postForm`), and it exports `BASE` for the rare case a component needs the backend URL directly. `BASE = import.meta.env.VITE_API_BASE_URL || '/api'`:
  - Local dev: Vite proxy (`vite.config.ts`) forwards `/api/*` → `http://localhost:8000/*`, prefix stripped.
  - Docker: `nginx.conf` does the same proxying to `trade-tracker:8000`.
  - Cloudflare Pages (prod): set `VITE_API_BASE_URL` to the Railway API URL at build time — the frontend then calls the API directly cross-origin (CORS via `FRONTEND_URL` on the backend).
- `src/vite-env.d.ts` — typing for `import.meta.env.VITE_API_BASE_URL`. Add new `VITE_*` vars here when introduced.
- `src/auth/AuthContext.tsx` — Google SSO state (auth config fetch, current user, sign-out). `src/pages/Login.tsx` — Google Identity Services button + credential verification.
- `src/pages/` — `Dashboard.tsx` (the whole authenticated app — tabbed IBKR/Fidelity/IronBeam/Trades view, header, period selector) and `Login.tsx`. There's no `Layout.tsx`/sidebar — nav lives inline in `Dashboard.tsx`'s header. `src/components/` has the supporting pieces: `FidelityUpdateWizard.tsx` (CSV/XLSX upload → preview/diff → commit), `CashFlowModal.tsx`, `PositionsTable.tsx`, `PerformanceChart.tsx`, `MetricCard.tsx`, `LabelBadge.tsx`, `Modal.tsx`.

## Ingestion Service (`ingestion-service/`)

ZMQ PULL socket on port 5555 → routes events to Postgres (`trading` DB) and/or QuestDB based on `event['type']` (`execution`, `order_update`, `log`, `signal`). See `router.py` for the routing table. This service is functionally complete; changes here should be rare and quant-team-driven.

## Deployment

Three-step production deploy, each with its own doc: [`docs/DEPLOY_RAILWAY.md`](docs/DEPLOY_RAILWAY.md) (backend) → [`docs/DEPLOY_GOOGLE_OAUTH.md`](docs/DEPLOY_GOOGLE_OAUTH.md) (auth) → [`docs/DEPLOY_CLOUDFLARE_PAGES.md`](docs/DEPLOY_CLOUDFLARE_PAGES.md) (frontend). Read those for the actual click-through steps; this is just the shape of it.

- **Frontend → Cloudflare Pages** (not Vercel — `vercel.json` is gone), root directory `trade-tracker/frontend`, env var `VITE_API_BASE_URL` = Railway API URL, set at build time.
- **Backend → Railway**, root directory `trade-tracker/api`, uses `railway.toml` (Dockerfile build, `/health` healthcheck) + a Postgres plugin (`DATABASE_URL` auto-injected, turns on `DB_SSL=require`).
- The two are linked via `FRONTEND_URL` (Railway env var, comma-separated list of allowed origins for CORS — `main.py` does split this on commas) and `VITE_API_BASE_URL` (Cloudflare Pages build-time env var).
- **Railway gotcha**: don't put `${VAR:-default}` bash-style syntax in `railway.toml` — Railway's own templating uses `${{...}}` and the two conflict. Put shell-expansion logic in the Dockerfile's `CMD` (shell form, `sh -c "..."`) instead.
- **Cloudflare Pages gotcha**: `VITE_API_BASE_URL` is baked into the JS bundle at build time, not read at runtime — set it before the first build, and any change requires a rebuild.
- **IBKR in production**: the IBKR client is the cloud Web API (RSA OAuth, server-to-server), so it runs headless on Railway fine — it just needs a stable outbound IP (`IBKR_SERVER_IP`, Railway Pro static IP) that matches what IBKR sees. Since positions/pricing now work, `railway.toml`'s env var comment recommends `IBKR_ENABLED=true` in production (yfinance is just the fallback when IBKR has no data, not the primary path anymore).
- **As of 2026-06-28, Railway is deployed and the Google OAuth + Cloudflare Pages steps are in progress** — this is an active, not hypothetical, deploy. Don't assume the old "never smoke-tested" framing still applies; check the actual Railway/Cloudflare dashboards (which Claude Code can't see) for current status rather than assuming from this doc alone.

## Conventions

- Config: always `os.getenv('VAR', default)` in `config.py`, never scattered `os.environ` calls in business logic.
- Error handling in writers/services: wrap external calls (DB, ZMQ, HTTP, IBKR) in try/except, log with `logging.error()`/`logging.info()`, return `True`/`False` or `None` rather than raising — these are long-running services that shouldn't crash on a single bad event/request.
- New env vars: add to `.env.example` with a comment, and to `config.py` with a sensible default. Document in README if it affects setup/deployment.
- Frontend env vars must be prefixed `VITE_` (Vite only exposes those to client code) and typed in `vite-env.d.ts`.

## Project management

Planning happens in Linear, not GitHub Issues. See [`docs/linear/`](docs/linear/) for the project/issue templates and GitHub↔Linear workflow conventions, and [`docs/REPO_AUDIT.md`](docs/REPO_AUDIT.md) for the current backlog. New bugs/features discovered while working in this repo should generally become new entries in `docs/REPO_AUDIT.md` rather than being silently fixed or left as TODO comments — flag them to the user. User-facing changes should also update [`docs/FEATURES.md`](docs/FEATURES.md).
