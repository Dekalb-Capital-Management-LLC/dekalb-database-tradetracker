# Project: Trade Tracker — Deploy & Hardening

Ready-to-paste project + issues for Linear. Create the project using [`PROJECT_TEMPLATE.md`](PROJECT_TEMPLATE.md), then create one Linear issue per `###` heading below (title = heading text, body = the rest of that section).

---

## Project description (paste into the project)

```markdown
## Overview
Finish the Railway/Vercel deployment for the Trade Tracker (currently in progress and broken),
then close the gaps found during the repo audit so the dashboard is reliable for daily use
as the team grows. See docs/PROJECT_STATUS.md for the full audit this project is based on.

## GitHub
- Repo: yyardi/dekalb-database-tradetracker
- Key paths touched: trade-tracker/api/, trade-tracker/frontend/, schemas/, ingestion-service/

## Goals / Success Criteria
- [ ] Production deploy (Railway + Vercel) is live, healthy, and CORS/auth work end to end
- [ ] Portfolio performance metrics (NAV, Sharpe, beta, alpha) are correct in the presence of deposits/withdrawals
- [ ] Trade Tracker API has automated tests running in CI
- [ ] Users aren't logged out mid-session due to ID token expiry

## Out of scope
- The AI News Sidebar feature (separate project, see docs/features/PORTFOLIO_AI_NEWS_SIDEBAR.md)
- Any IBKR Gateway "always-on" production solution (flagged as an open question, not solved here)

## Related docs
- docs/PROJECT_STATUS.md
```

## Milestones

1. **Deployed & verified** — Railway + Vercel live, CORS/auth working end to end.
2. **Metrics correctness** — cash flows, risk-free rate, win rate addressed.
3. **Test coverage & CI** — pytest + GitHub Actions for `trade-tracker/api`.
4. **Auth hardening** — token refresh so sessions don't randomly expire.

---

# Issues

## Milestone 1: Deployed & verified

### Configure Railway service for trade-tracker/api

**Labels:** `deploy`, `platform`
**Priority:** Urgent

In the Railway dashboard, create/configure the service for `trade-tracker/api`:

- Set **Settings → Source → Root Directory** to `trade-tracker/api` so Railway picks up `trade-tracker/api/railway.toml` and its Dockerfile.
- Add a **PostgreSQL** plugin to the project (Railway will inject `DATABASE_URL`; `config.py` already handles this and turns on `DB_SSL=require`).

**Acceptance criteria:** Railway build succeeds and the service starts (it may not pass `/health` yet until env vars from the next issue are set).

---

### Set Railway environment variables for trade-tracker/api

**Labels:** `deploy`, `platform`, `auth`
**Priority:** Urgent
**Depends on:** "Configure Railway service for trade-tracker/api"

Set the following service env vars in Railway:

| Variable | Value |
|---|---|
| `AUTH_ENABLED` | `true` |
| `GOOGLE_CLIENT_ID` | from Google Cloud Console OAuth client (create one if it doesn't exist — see README "Setting up a Google OAuth Client ID") |
| `ALLOWED_EMAIL_DOMAIN` | `dekalbcapitalmanagement.com` |
| `IBKR_ENABLED` | `false` (gateway can't run on Railway, see docs/PROJECT_STATUS.md #6) |
| `FRONTEND_URL` | placeholder for now — set for real once Vercel is deployed (next issues) |

**Acceptance criteria:** `curl https://<railway-url>/health` returns `{"status": "ok", ...}`.

---

### Deploy Trade Tracker frontend to Vercel

**Labels:** `deploy`, `frontend`
**Priority:** Urgent
**Depends on:** "Set Railway environment variables for trade-tracker/api"

- Create a Vercel project from this repo with **Root Directory** = `trade-tracker/frontend` (Vercel auto-detects Vite via `vercel.json`).
- Set env var `VITE_API_BASE_URL` to the Railway URL from the previous issue (no trailing slash).
- Deploy.

**Acceptance criteria:** Vercel build succeeds and the app loads (it may show CORS/auth errors until the next issue is done — that's expected).

---

### Wire FRONTEND_URL + Google OAuth origins for the deployed frontend

**Labels:** `deploy`, `auth`
**Priority:** Urgent
**Depends on:** "Deploy Trade Tracker frontend to Vercel"

- Set `FRONTEND_URL` on Railway to the Vercel URL (comma-separate if there's also a preview-deploy domain you want to allow). Redeploy the backend so CORS picks it up — `main.py` reads `FRONTEND_URL` into `_cors_origins` at startup.
- Add the Vercel URL(s) to the Google OAuth Client's **Authorized JavaScript origins** in Google Cloud Console.

**Acceptance criteria:** Open the Vercel URL, sign in with Google, and confirm the dashboard loads data from the Railway API with no CORS or 401 errors in the browser console.

---

### Production smoke test

**Labels:** `deploy`, `qa`
**Priority:** High
**Depends on:** "Wire FRONTEND_URL + Google OAuth origins for the deployed frontend"

Manually walk through, on the production URLs:

- [ ] Sign in with a `@dekalbcapitalmanagement.com` Google account
- [ ] Dashboard loads portfolio summary + performance chart
- [ ] Trades page loads and filters work
- [ ] Upload a small Fidelity CSV via Import page
- [ ] `/health` and `/docs` are reachable without auth

Document any failures as new issues in this project.

---

## Milestone 2: Metrics correctness

### Design and implement cash flow tracking (deposits/withdrawals)

**Labels:** `bug`, `data-quality`, `backend`
**Priority:** High

`schemas/trade_tracker_schema.sql` already has a `cash_flows` table (`account_id`, `flow_date`, `amount`, presumably a type/description column) with a comment that it should be "excluded from NAV performance calc" — but nothing in `trade-tracker/api` inserts into or reads from it. As-is, `services/portfolio_metrics.py` computes returns straight from `portfolio_snapshots` NAV deltas, so any deposit or withdrawal is currently indistinguishable from a trading gain/loss. This will produce visibly wrong Sharpe/beta/alpha/drawdown numbers the next time someone adds or removes capital.

**Scope:**
- Add an endpoint (or admin-only form) to record a cash flow (date, account, amount, direction).
- Update `portfolio_metrics.py`'s return calculation to subtract cash flows from period-over-period NAV changes (i.e. compute returns on a "as if no external cash moved" basis — a simple Modified Dietz or similar adjustment is enough for v1).
- Add a test (see Milestone 3) covering a snapshot sequence with a deposit in the middle.

**Acceptance criteria:** A deposit between two snapshots does not appear as portfolio return in `/portfolio/performance` or `/portfolio/metrics`.

---

### Make risk-free rate configurable

**Labels:** `enhancement`, `backend`
**Priority:** Medium

`RISK_FREE_RATE_ANNUAL = 0.0` is hardcoded in `trade-tracker/api/services/portfolio_metrics.py`. Sharpe ratio is therefore effectively return/volatility with no risk-free adjustment.

**Scope:**
- Move `RISK_FREE_RATE_ANNUAL` to `config.py` as an env var with a documented default (e.g. current ~3-month T-bill rate, updated periodically — doesn't need to be live/automated for v1).
- Document in README that this needs occasional manual updating, or note as a future enhancement to pull from a market data source.

**Acceptance criteria:** Changing `RISK_FREE_RATE_ANNUAL` via env var changes the Sharpe ratio returned by `/portfolio/metrics`.

---

### Document win-rate calculation as approximate (or improve it)

**Labels:** `enhancement`, `backend`, `docs`
**Priority:** Low

Win rate is currently "% of SELL trades with positive `net_amount`" — not a FIFO-matched realized-P&L-per-round-trip calculation. This is fine as a rough indicator but could mislead someone expecting a precise number.

**Scope (pick one):**
- Minimal: add a tooltip/label in the frontend ("approximate, not FIFO-matched") and a docstring note — already partially done in README.
- Full: implement FIFO lot matching for realized P&L per round trip and recompute win rate from that. Larger effort — split into its own issue if pursued.

---

## Milestone 3: Test coverage & CI

### Add pytest setup for trade-tracker/api

**Labels:** `testing`, `backend`
**Priority:** Medium

There are currently zero automated tests for `trade-tracker/api` (only manual ZMQ scripts for the ingestion service, in `tests/`).

**Scope:**
- Add `pytest`, `pytest-asyncio`, `httpx` (for `TestClient`/`AsyncClient`) to `trade-tracker/api/requirements.txt` (dev-only group if you split requirements files).
- Add a test database setup — simplest option: spin up a throwaway Postgres via `docker-compose` (or `testcontainers-python`) and apply `schemas/trade_tracker_schema.sql`.
- Add `trade-tracker/api/tests/` with at least:
  - `test_health.py` — `/health` returns 200 when DB is reachable.
  - `test_trades.py` — list/filter/label a trade against seeded data.
  - `test_portfolio_metrics.py` — feed known `portfolio_snapshots` rows and assert beta/Sharpe/etc. match hand-calculated values (this also covers the cash-flow fix above).

**Acceptance criteria:** `pytest` runs locally and passes against a fresh test DB.

---

### Add CI workflow for trade-tracker/api tests

**Labels:** `testing`, `ci`, `platform`
**Priority:** Medium
**Depends on:** "Add pytest setup for trade-tracker/api"

Add a GitHub Actions workflow (`.github/workflows/test-trade-tracker-api.yml`) that, on PRs touching `trade-tracker/api/**`:

- Spins up Postgres as a service container.
- Installs `trade-tracker/api/requirements.txt`.
- Applies `schemas/trade_tracker_schema.sql`.
- Runs `pytest`.

**Acceptance criteria:** A PR with a failing test fails CI; a PR with passing tests shows a green check.

---

## Milestone 4: Auth hardening

### Implement Google ID token refresh

**Labels:** `bug`, `auth`, `frontend`
**Priority:** Medium

Google ID tokens expire after ~1 hour. `trade-tracker/frontend/src/api/client.ts`'s `handle401()` currently just clears storage and redirects to `/login` on any 401 — so once `AUTH_ENABLED=true` in production, users get logged out mid-session with no warning.

**Scope:**
- Use Google Identity Services' silent re-auth (e.g. `google.accounts.id.prompt()` or One Tap) to get a fresh ID token before/when the old one expires, without a full page redirect.
- Fall back to the existing redirect-to-`/login` behavior only if silent re-auth fails (e.g. user revoked access).

**Acceptance criteria:** A user who keeps a tab open past the 1h token expiry doesn't get bounced to `/login` on their next action (assuming their Google session is still valid).

---

## Other / unassigned

### QuestDB schema isn't auto-applied

**Labels:** `enhancement`, `ingestion`, `platform`
**Priority:** Low

Unlike the two Postgres databases (auto-provisioned via `docker-compose.yml` init scripts and `db.py`'s `_ensure_db_exists`), `schemas/questdb_schema.sql` must be run manually via the QuestDB web console (`http://localhost:9000`). On a fresh environment this is easy to forget, and `ingestion-service` writes to QuestDB will silently fail (logged as errors, but easy to miss) until the tables exist.

**Scope:** Add a small init step — either a one-shot container in `docker-compose.yml` that POSTs the schema to QuestDB's `/exec` HTTP endpoint on startup, or a `make`/shell script documented in the README, run once per environment.

**Acceptance criteria:** Fresh `docker compose up --build` results in a working `executions`/`engine_logs`/`strategy_signals` table set in QuestDB with no manual console step.
