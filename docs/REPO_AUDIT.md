# Repo Audit & Roadmap

_Last updated: 2026-06-11_

Single source of truth for outstanding work across the repo. Replaces the old
`docs/PROJECT_STATUS.md`, `docs/linear/TRADE_TRACKER_BACKLOG.md`, and
`docs/features/PORTFOLIO_AI_NEWS_SIDEBAR.md` (all removed — this file absorbed
their content).

## Status snapshot

**Working:** ingestion service (ZMQ → Postgres `trading` + QuestDB); Trade
Tracker API (trades CRUD/filter/label, portfolio summary/positions/performance/
metrics, Fidelity CSV import, market data via yfinance, IBKR status/account/
positions/sync); frontend (Dashboard/Trades/Import); Google Workspace SSO
(gated by `AUTH_ENABLED`); IBKR Gateway integration (direct, port 5001, no
proxy/VPN). `docker compose up --build` auto-provisions both Postgres DBs.

**Recently fixed (2026-06-10):** stale Pangolin/port-5000 IBKR docs removed;
`FRONTEND_URL`/CORS wired up; `docker-compose.yml` healthcheck fixed (was
`curl`, doesn't exist in `python:3.11-slim`); `vercel.json` placeholder URL
replaced with `VITE_API_BASE_URL`; README/CLAUDE.md rewritten.

---

## Project: [Platform] Trade Tracker — Deploy & Hardening

**Summary:** Finish the Railway/Vercel deploy (in progress, currently broken),
then close the gaps found in this audit so the dashboard is reliable for daily
use as the team grows.

**Labels:** Platform

**Milestones:**
1. Deployed & verified
2. Metrics correctness
3. Test coverage & CI
4. Auth hardening

### Milestone 1: Deployed & verified

#### [Platform] Configure Railway service for trade-tracker/api
- **Labels:** Chore
- **Priority:** Urgent
- **What:** In the Railway dashboard, set Settings → Source → Root Directory
  to `trade-tracker/api` (picks up its `railway.toml`/Dockerfile), and add a
  Postgres plugin — `DATABASE_URL` is auto-injected and `config.py` already
  turns on `DB_SSL=require`.

#### [Platform] Set Railway environment variables for trade-tracker/api
- **Labels:** Chore
- **Priority:** Urgent
- **Notes:** Depends on "Configure Railway service for trade-tracker/api".
- **What:** Set `AUTH_ENABLED=true`, `GOOGLE_CLIENT_ID` (create an OAuth
  client in Google Cloud Console if one doesn't exist yet — see README),
  `ALLOWED_EMAIL_DOMAIN=dekalbcapitalmanagement.com`, `IBKR_ENABLED=false`,
  and a placeholder `FRONTEND_URL` (real value comes once Vercel is up).
  Acceptance: `curl https://<railway-url>/health` returns 200.

#### [Platform] Deploy Trade Tracker frontend to Vercel
- **Labels:** Chore
- **Priority:** Urgent
- **Notes:** Depends on "Set Railway environment variables for
  trade-tracker/api".
- **What:** Create a Vercel project with Root Directory =
  `trade-tracker/frontend` (Vite auto-detected via `vercel.json`), set
  `VITE_API_BASE_URL` to the Railway URL (no trailing slash), deploy.

#### [Platform] Wire FRONTEND_URL + Google OAuth origins for the deployed frontend
- **Labels:** Chore
- **Priority:** Urgent
- **Notes:** Depends on "Deploy Trade Tracker frontend to Vercel".
- **What:** Set `FRONTEND_URL` on Railway to the Vercel URL(s) and redeploy
  (`main.py` reads it into CORS `allow_origins` at startup). Add the Vercel
  URL(s) to the Google OAuth client's Authorized JavaScript origins.
  Acceptance: sign in on the Vercel URL with a `@dekalbcapitalmanagement.com`
  account with no CORS/401 errors.

#### [Platform] Production smoke test
- **Labels:** Chore
- **Priority:** High
- **Notes:** Depends on "Wire FRONTEND_URL + Google OAuth origins for the
  deployed frontend".
- **What:** On the production URLs — sign in, confirm Dashboard loads
  portfolio summary + performance chart, Trades page loads/filters work,
  upload a small Fidelity CSV via Import, and confirm `/health`/`/docs` are
  reachable without auth. File any failures as new issues.

### Milestone 2: Metrics correctness

#### [Platform] Design and implement cash flow tracking (deposits/withdrawals)
- **Labels:** Bug
- **Priority:** High
- **Status:** Resolved 2026-06-17. `POST /portfolio/cash-flows` records
  deposits/withdrawals and performance/metrics use cash-flow-adjusted returns.
- **Original finding:** `cash_flows` existed in `trade_tracker_schema.sql` (comment says
  "excluded from NAV performance calc") but nothing wrote or read it, so
  deposits/withdrawals showed up as portfolio gains/losses in
  `/portfolio/performance` and `/portfolio/metrics` (beta, Sharpe, alpha,
  drawdown all wrong around those dates).

#### [Platform] Make risk-free rate configurable
- **Labels:** Improvement
- **Priority:** Medium
- **Status:** Resolved 2026-06-17. `RISK_FREE_RATE_ANNUAL` is read from
  `config.py`/environment and documented in `.env.example`.
- **Original finding:** `RISK_FREE_RATE_ANNUAL` was hardcoded to `0.0` in
  `portfolio_metrics.py`; Sharpe needed to change when the env var changes.

#### [Platform] Document or improve win-rate calculation
- **Labels:** Chore
- **Priority:** Low
- **Status:** Resolved 2026-06-17 by labeling the dashboard metric as
  approximate; FIFO matching remains a separate larger enhancement.
- **Original finding:** Win rate is "% of SELL trades with positive `net_amount`", not
  FIFO-matched realized P&L. The shipped fix labels it approximate in the UI;
  FIFO lot matching remains a separate larger issue if pursued.

### Milestone 3: Test coverage & CI

#### [Platform] Add pytest setup for trade-tracker/api
- **Labels:** Chore
- **Priority:** Medium
- **What:** Zero automated tests exist for `trade-tracker/api`. Add
  `pytest`/`pytest-asyncio`/`httpx`, a throwaway test Postgres (docker-compose
  or testcontainers) seeded from `trade_tracker_schema.sql`, and tests for
  `/health`, trades CRUD, and `portfolio_metrics` (covering the cash-flow fix
  above).

#### [Platform] Add CI workflow for trade-tracker/api tests
- **Labels:** Chore
- **Priority:** Medium
- **Notes:** Depends on "Add pytest setup for trade-tracker/api".
- **What:** GitHub Actions workflow that spins up Postgres, applies
  `trade_tracker_schema.sql`, and runs `pytest` on PRs touching
  `trade-tracker/api/**`.

### Milestone 4: Auth hardening

#### [Platform] Implement Google ID token refresh
- **Labels:** Bug
- **Priority:** Medium
- **What:** ID tokens expire after ~1h; `client.ts`'s `handle401()` redirects
  to `/login` on any 401 with no warning. Use Google Identity Services' silent
  re-auth (`google.accounts.id.prompt()` / One Tap) to refresh before expiry,
  falling back to the redirect only if silent re-auth fails.

---

## Project: [AI] Portfolio News Sidebar

**Summary:** Dashboard sidebar surfacing AI-summarized Twitter/X content
relevant to the firm's positions — without ever telling the AI service what
the firm holds.

**Labels:** AI, Platform

**Design — privacy invariant (do not relax this):** The AI/analysis step must
never receive DeKalb's actual holdings, position sizes, P&L, or account
values. The set of tickers the AI analyzes is a broad, statically-curated
watchlist maintained by humans on a slow cadence (e.g. monthly) — never
derived from live positions. The AI's output (ticker/sentiment/summary/
category per post) is general-purpose. The join between "what's noteworthy"
and "what we hold" happens **only** inside `trade-tracker/api`'s new
`GET /news/relevant` endpoint (authenticated, internal) — the AI/vendor never
sees positions or position-derived queries.

**Milestones:**
1. Design finalized
2. Ingestion pipeline
3. API + frontend
4. Hardening

### Milestone 1: Design finalized

#### [AI] Resolve open design questions
- **Labels:** Chore
- **Priority:** High
- **Notes:** Blocks every other issue in this project.
- **What:** Decide (a) X API vs. RSS for source posts (X's filtered-stream
  tiers are paid, ~$100/mo+), (b) AI provider — Claude Haiku recommended,
  needs a new `ANTHROPIC_API_KEY` env var, (c) who maintains
  `social-feed-service/config/watchlist.yaml` and how often, (d) source of
  truth for "current positions" (`trading.positions` vs.
  `trade_tracker.trades`), (e) retention policy for `social_signals`
  (recommend ~90 days).

### Milestone 2: Ingestion pipeline

#### [AI] Scaffold social-feed-service
- **Labels:** Feature
- **Priority:** Medium
- **Notes:** Depends on "Resolve open design questions".
- **What:** New top-level service (own Dockerfile, like
  `ingestion-service/`) — config loading + polling loop skeleton, following
  the same conventions (try/except around external calls,
  `logging.error`/`logging.info`, no raising).

#### [AI] Implement watchlist polling
- **Labels:** Feature
- **Priority:** Medium
- **Notes:** Depends on "Scaffold social-feed-service".
- **What:** Poll the chosen data source (X API or RSS) for posts matching
  `config/watchlist.yaml` — a broad, human-curated ticker list, never derived
  from live positions.

#### [AI] Implement AI extraction step
- **Labels:** Feature
- **Priority:** Medium
- **Notes:** Depends on "Scaffold social-feed-service".
- **What:** For each new post, call the chosen LLM with a privacy-preserving
  prompt — extract tickers mentioned, sentiment (-1 to 1), a one-sentence
  summary, and category (earnings/analyst-rating/macro/rumor/other). Send
  only the public post text/metadata; nothing about DeKalb.

#### [AI] Add social_signals schema + writer
- **Labels:** Feature
- **Priority:** Medium
- **Notes:** Depends on "Resolve open design questions".
- **What:** Add a `social_signals` table (source, `source_post_id`,
  `author_handle`, `posted_at`, `raw_text`, `tickers[]`, `sentiment`,
  `summary`, `category`, unique on `(source, source_post_id)`, GIN index on
  `tickers`) to `schemas/`, and write extraction results to it.

### Milestone 3: API + frontend

#### [Platform] Add GET /news/relevant endpoint
- **Labels:** Feature
- **Priority:** Medium
- **Notes:** Depends on "Add social_signals schema + writer". This is the
  ONLY place positions and social signals ever meet.
- **What:** New `trade-tracker/api/routers/news.py` — query current open
  positions, then `social_signals WHERE tickers && <open symbols>`. Subject
  to normal `AUTH_ENABLED` auth like every other endpoint.

#### [Platform] Build NewsSidebar frontend component
- **Labels:** Feature
- **Priority:** Medium
- **Notes:** Depends on "Add GET /news/relevant endpoint".
- **What:** New collapsible sidebar on the Dashboard, polling
  `/news/relevant` (reuse the existing polling pattern). Each item: ticker
  badge(s), sentiment indicator, AI summary, timestamp, link to source post.
  Wire into `Layout.tsx`.

### Milestone 4: Hardening

#### [AI] Add retention/cleanup job for social_signals
- **Labels:** Chore
- **Priority:** Low
- **What:** Periodic job to delete rows older than the retention window
  decided in "Resolve open design questions".

#### [AI] Add tests for AI extraction and /news/relevant
- **Labels:** Chore
- **Priority:** Medium
- **What:** Mock the LLM call for extraction tests; test the
  positions/`social_signals` join logic in `/news/relevant`.

#### [AI] Privacy review sign-off
- **Labels:** Security
- **Priority:** High
- **Notes:** Depends on everything else in this project.
- **What:** Confirm the watchlist stays broad/static and that no portfolio
  data appears in any `social-feed-service` logs, prompts, or outbound
  requests — sign off on the privacy invariant above before going live.

---

## Standalone issues

#### [Platform] QuestDB schema isn't auto-applied
- **Labels:** Improvement
- **Priority:** Low
- **What:** Unlike the two Postgres databases (auto-provisioned via
  `docker-compose.yml` + `db.py`'s `_ensure_db_exists`),
  `schemas/questdb_schema.sql` must be run manually via the QuestDB console
  (`http://localhost:9000`) — `ingestion-service` writes to QuestDB silently
  fail until then. Add a one-shot init container that POSTs the schema to
  QuestDB's `/exec` endpoint on startup, or a documented script.
