# Repo Audit & Roadmap

_Last updated: 2026-06-11_

Single source of truth for outstanding work across the repo. This is the input
for Linear — each bullet below is roughly one issue. Projects are the top-level
headings; bullets under them are the work. Priorities are inline (**Urgent /
High / Medium / Low**). No milestone structure yet — keep it flat and easy to
triage.

> **Reality check:** the Trade Tracker is **not** production-ready. Auth is wired
> up in the frontend but not enforced by the backend, IBKR has never been
> verified end-to-end against a live account, the production deploy was never
> finished, parts of the dashboard are visually broken, and the docs (README /
> CLAUDE.md) describe an older architecture that no longer matches the code. The
> sections below break that down so it can be assigned out.

---

## Status snapshot

**Actually working:**
- Ingestion service (ZMQ → Postgres `trading` + QuestDB). Functionally complete,
  quant-team owned. Not the focus of this audit.
- `docker compose up --build` provisions both Postgres DBs and starts all
  services.
- Trade Tracker API boots, serves `/health`, `/docs`, and the portfolio / trades
  / import / market endpoints against the `trade_tracker` DB.
- yfinance price fetching + in-process auto-refresh loop (every 5 min) writing
  NAV snapshots.
- Frontend Dashboard / Trades / Import pages render and call the API.
- A **custom XLSX** portfolio upload (`Ticker | Date Acquired | Amount | Price
  Acquired`) imports into `trades` + `imported_positions` — but only for a single
  hardcoded `PORTFOLIO` account, and it's not an actual Fidelity export (see the
  Import project).

**Broken / unverified / unfinished (the rest of this document):**
- Google SSO auth is **not enforced** — backend never registers the auth router
  or middleware.
- IBKR (cloud Web API, RSA OAuth) **connects but can't pull positions or
  pricing** — effectively non-functional; everything runs on yfinance.
- Fidelity **CSV** import doesn't work — the CSV parser is dead code and the live
  endpoint only takes a hand-built XLSX.
- Production deploy (Railway + Vercel) was never completed or smoke-tested.
- Dashboard has visible UI bugs (invisible header text, dead nav buttons, mixed
  light/dark theming).
- DB schema has drifted — three tables (`imported_positions`, `ibkr_tokens`,
  `instrument_conids`) exist only as runtime migrations in `db.py`, not in the
  schema file.

_(The docs themselves — README, CLAUDE.md, FEATURES.md — have been rewritten to
match this reality as of 2026-06-11.)_

---

## Project: Trade Tracker — Auth (broken, top priority)

The frontend has a full Google SSO flow (`AuthContext`, `Login.tsx`,
`client.ts` bearer headers + `handle401`), but the **backend does not enforce
any of it**. As written, `AUTH_ENABLED=true` does nothing and the app is
effectively open.

- **Register the auth router** — **Urgent.** `main.py` imports
  `from routers import auth as auth_router` but never calls
  `app.include_router(auth_router.router)`. So `/auth/config`, `/auth/verify`,
  and `/auth/me` all return 404. The frontend's `AuthContext` fetches
  `/api/auth/config`, gets a 404, hits its `.catch()`, and silently defaults to
  `auth_enabled: false` — so the login page never even shows.
- **Add the AuthMiddleware** — **Urgent.** `main.py` imports
  `BaseHTTPMiddleware`, `AuthError`, and `verify_google_id_token` but never adds
  a middleware that uses them. No endpoint verifies the `Authorization: Bearer`
  token the frontend sends. Even with the router registered, `AUTH_ENABLED=true`
  would protect nothing and `/auth/me` would always 401 (nothing ever sets
  `request.state.user`). Build the middleware: verify the Google ID token on
  every request, skip bypass paths (`/health`, `/docs`, `/redoc`,
  `/openapi.json`, `/auth/*`), set `request.state.user`. CLAUDE.md already
  describes this behavior as if it exists — make the code match.
- **Verify the full sign-in flow end-to-end** — **High.** Depends on the two
  above. With `AUTH_ENABLED=true`: confirm the login page renders, a
  `@dekalbcapitalmanagement.com` Google account can sign in, the ID token is
  accepted, protected endpoints return data, and a non-allowed domain is
  rejected.
- **Wire up the frontend sign-out button** — **Medium.** `Layout.tsx`'s "Sign
  out" button (and the Settings / Notifications buttons next to it) have no
  `onClick`. `AuthContext` exposes `signOut()` but nothing calls it. Hook it up;
  remove or implement the dead Settings/Notifications buttons.
- **Token refresh** — **Medium.** ID tokens expire after ~1h; `client.ts`'s
  `handle401()` hard-redirects to `/login` with no warning. Use Google Identity
  Services silent re-auth (`google.accounts.id.prompt()` / One Tap) to refresh
  before expiry, falling back to the redirect.

## Project: Trade Tracker — IBKR Integration (does not work)

The implementation is the **IBKR cloud Web API** (`api.ibkr.com`) using RSA
key-based OAuth 2.0 / JWT bearer flow — server-to-server, no browser login, no
desktop gateway. See `services/ibkr_client.py` and `config.py`. The docs
(README/CLAUDE.md) still describe the old Client Portal Gateway (port 5001,
desktop app, 2FA) — that architecture is gone; see the Docs project below.

**Current real state: the session connects, but nothing past connection works.**
We can authenticate (bearer token → SSO session → iserver init), but we have
**never successfully pulled positions or pricing** out of the API. So IBKR is
effectively non-functional today — the dashboard runs entirely on yfinance +
Fidelity CSVs. The bullets below are roughly in dependency order.

- **Get positions out of IBKR** — **Urgent.** `get_positions()` /
  `GET /ibkr/positions` / `POST /ibkr/sync/positions` return nothing usable.
  IBKR's `/portfolio/{account}/positions` is notorious for returning `[]` on the
  first call (subscription warm-up) and for needing `/portfolio/accounts` called
  first — the code attempts both with retries/pagination but it still isn't
  yielding data. Investigate against a live session: log the raw responses,
  confirm the account ID format, confirm the warm-up sequence, and figure out
  why positions come back empty. This is the single biggest blocker.
- **Get pricing/snapshots out of IBKR** — **Urgent.** `get_market_snapshot_batch()`
  (`/iserver/marketdata/snapshot`) also isn't returning prices. Same class of
  problem: the snapshot endpoint requires conid resolution (`/trsrv/stocks`) and
  a poll-until-populated loop (field `31` = last). Confirm conid lookup works,
  then confirm the snapshot poll actually fills in. Until this works, all pricing
  is yfinance.
- **Confirm `IBKR_SERVER_IP` handling** — **High.** IBKR ties the session to an
  outbound IP. A wrong/changing IP is a likely cause of the session being
  "connected" but unauthorized for data. Locally this is the home IP; on Railway
  it's the static outbound IP (Pro plan). The `_detect_ip()` fallback is fragile.
  Pin down the canonical way to set this per environment and verify it's the IP
  IBKR actually sees.
- **Verify account summary + trade sync once data flows** — **High.** Depends on
  the above. Re-check `GET /ibkr/account` (NAV/ledger) and
  `POST /ibkr/sync/trades` (→ `trades`) actually return real data and dedupe.
  Document which sync endpoint the hourly cron + dashboard rely on (there are two:
  `sync/positions` vs `sync/trades`).
- **Decide the production IBKR story** — **Medium.** Unlike the old desktop
  gateway, this Web API client *can* run headless on Railway (it just needs a
  stable outbound IP). But until positions + pricing actually work, prod must run
  `IBKR_ENABLED=false` (yfinance). Revisit once the blockers above are fixed.
- **`market_data.py` IBKR routing** — **Medium.** It falls back to yfinance when
  IBKR is unreachable; confirm the fallback triggers cleanly (no hangs/timeouts)
  when `IBKR_ENABLED=true` but the session can't return data — which is the
  current state, so this fallback path is what's actually in use.

## Project: Trade Tracker — CSV / Fidelity Import (built wrong)

This is one of the most broken areas. The "Fidelity import" doesn't actually
import Fidelity's CSV format — the careful CSV parser is dead code, and the
only live path takes a hand-built XLSX and force-fits everything into a single
account. Treat this as a near-rewrite, not a set of tweaks.

- **The Fidelity CSV parser is dead code** — **High.** `fidelity_parser.py`
  (Activity + Positions CSV detection, `parse_fidelity_csv`,
  `extract_positions_snapshot`) and `ibkr_parser.py`'s CSV path are **never
  called by any router**. The only live import endpoint, `POST /import/trades`,
  uses `parse_portfolio_xlsx` (`universal_parser.py`) and **rejects anything that
  isn't `.xlsx`/`.xlsm`**. So uploading an actual Fidelity CSV export — the
  whole point — 400s. Decide: wire the CSV parser back in, or delete it and stop
  calling the feature "Fidelity import."
- **Frontend accepts file types the backend rejects** — **High.** `Import.tsx`
  accepts `.csv,.xlsx,.xlsm,.xls,.tsv,.txt` (and the error text says "Drop a
  .csv … file"), and `imports.py` even defines `_SUPPORTED_EXTENSIONS` including
  csv/tsv/txt — but `/import/trades` only honors `.xlsx`/`.xlsm`. A user dropping
  a CSV gets a confusing 400. Make the accepted types match on both ends.
- **The real input is a hand-built spreadsheet, not a broker export** — **High.**
  `parse_portfolio_xlsx` expects sheets of `Ticker | Date Acquired | Amount |
  Price Acquired`. That's a manually-maintained file, not anything Fidelity or
  IBKR exports. Decide the intended source of truth (real Fidelity Activity CSV?
  Positions CSV? IBKR Activity Statement? the custom XLSX?) and build to it —
  right now the feature's name and its behavior don't match.
- **Everything is hardcoded to one account** — **High.** `upload_trades`
  hardcodes `account_id='PORTFOLIO'`, `parse_portfolio_xlsx` defaults to
  `'PORTFOLIO'`, and every import runs
  `DELETE FROM imported_positions WHERE account_id='PORTFOLIO'` first — so each
  upload **wipes all existing positions** and multi-account tracking is
  impossible. The per-account `Account`/`Account Number` columns in the real
  exports are ignored. Multi-account support needs a real design.
- **Positions-as-synthetic-trades double counts** — **Medium.** Each holding row
  becomes a synthetic BUY in `trades` **and** is aggregated into
  `imported_positions`; both feed `/portfolio/summary`. A position snapshot is
  not a set of fills — these synthetic trades have made-up dates and corrupt
  anything that reads the trade ledger (win-rate, realized P&L, performance).
  Separate "holdings snapshot" from "trade history" as distinct concepts.
- **Silent data loss** — **Medium.** Options (any symbol starting `-` or matching
  the `YYMMDD[PC]strike` pattern) and cash/money-market positions (SPAXX/FDRXX/
  FCASH) are silently dropped on import, so NAV computed from positions excludes
  them and is understated. Meanwhile `main.py`'s auto-refresh treats `XXCASH` as
  price 1.0 even though such rows never make it into `imported_positions` —
  inconsistent cash handling. Decide how cash and options should be represented.

## Project: Trade Tracker — Production Deployment (not done)

Railway (`railway.toml`) and Vercel (`vercel.json`) configs exist but the deploy
was never finished or smoke-tested. Do these in order.

- **Configure the Railway service** — **Urgent.** Set Settings → Source → Root
  Directory to `trade-tracker/api` (picks up `railway.toml` / Dockerfile), add a
  Postgres plugin (`DATABASE_URL` auto-injected; `config.py` turns on
  `DB_SSL=require`).
- **Set Railway env vars** — **Urgent.** Depends on the service existing. At
  minimum `AUTH_ENABLED=true`, `GOOGLE_CLIENT_ID`,
  `ALLOWED_EMAIL_DOMAIN=dekalbcapitalmanagement.com`, an IBKR decision (see IBKR
  project), and a placeholder `FRONTEND_URL`. Acceptance:
  `curl https://<railway-url>/health` returns 200. **Note:** this only matters
  once the auth backend is actually wired up (Auth project) — otherwise
  `AUTH_ENABLED=true` is a no-op.
- **Deploy the frontend to Vercel** — **Urgent.** Root Directory =
  `trade-tracker/frontend`, set `VITE_API_BASE_URL` to the Railway URL (no
  trailing slash, no `/api` suffix).
- **Wire FRONTEND_URL + Google OAuth origins** — **Urgent.** Set `FRONTEND_URL`
  on Railway to the Vercel URL(s) and redeploy. Add the Vercel URL(s) to the
  Google OAuth client's Authorized JavaScript origins. **Blocker:** `main.py`
  appends `FRONTEND_URL` to CORS origins as a single string and never splits on
  commas — so the documented "comma-separated list" silently breaks with more
  than one origin. Fix the split as part of this.
- **Production smoke test** — **High.** On the live URLs: sign in, Dashboard
  loads summary + chart, Trades filters work, a small Fidelity CSV imports,
  `/health` and `/docs` reachable without auth. File any failures as new issues.

## Project: Trade Tracker — Frontend / UI (broken in parts)

- **Invisible dashboard header text** — **High.** `Dashboard.tsx` renders the
  "Portfolio Overview" title and the "As of …" line with `text-white`, but the
  page background (`Layout.tsx` main area) is light (`#e8edf5`). The header text
  is effectively invisible. The period selector also uses dark-theme classes
  (`bg-gray-900`, `border-gray-800`) that clash with the otherwise light theme.
  Pick one theme and make the page header consistent with the white cards below
  it.
- **Dashboard header layout** — **Medium.** The header is a single flex row with
  `justify-between` wrapping four separate groups (title, Update button +
  message, period selector, search). They spread unevenly and the update message
  can overflow. Restructure into a clean header / toolbar.
- **Dead nav buttons** — **Medium.** `Layout.tsx` Settings, Notifications, and
  Sign-out buttons have no handlers. The top-right "Account" avatar is static
  (no user name/picture even though `AuthContext` has them). Either implement or
  remove; wire Sign-out to `signOut()` (see Auth project).
- **Audit the other pages** — **Medium.** Trades and Import pages haven't been
  visually reviewed on this branch — check for the same light/dark theme drift
  and any broken states (empty data, error bars, loading).

## Project: Trade Tracker — Data Model & Metrics correctness

- **Schema drift — `imported_positions` / `ibkr_tokens` / `instrument_conids`** —
  **High.** These three tables exist **only** as runtime migrations in
  `db.py` (`_apply_migrations`), not in `schemas/trade_tracker_schema.sql`. The
  entire portfolio/positions path depends on `imported_positions`. Because
  `_apply_schema_if_empty` only runs the schema file on an empty DB and the
  migrations run separately, the schema file and the real schema have diverged.
  Decide the source of truth (fold migrations into the schema file, or adopt a
  real migration tool) and update all docs to list the actual 7 tables.
- **Cash flow tracking (deposits/withdrawals)** — **High.** `cash_flows` exists
  in the schema (comment: "excluded from NAV performance calc") but nothing
  writes or reads it, so deposits/withdrawals show up as portfolio gains/losses
  in `/portfolio/performance` and `/portfolio/metrics` (beta, Sharpe, alpha,
  drawdown all wrong around those dates). Add a way to record cash flows and
  adjust the return calc (Modified Dietz or similar) to exclude them.
- **Make the risk-free rate configurable** — **Medium.** `RISK_FREE_RATE_ANNUAL`
  is hardcoded to `0.0` in `portfolio_metrics.py`. Move it to `config.py` as an
  env var with a documented default; Sharpe should change when it changes.
- **Document or fix win-rate** — **Low.** Win rate is "% of SELL trades with
  positive `net_amount`", not FIFO-matched realized P&L. Either label it
  approximate in the UI or implement FIFO lot matching (separate, bigger issue).
- **Reconcile refresh cadences** — **Low.** Three different cadences are in play:
  the in-process `_auto_refresh_loop` in `main.py` (every 5 min), the
  `snapshot-cron` docker service (hourly: snapshot + IBKR trade sync), and the
  frontend's `setInterval(loadSummary, 300_000)` (5 min). The README claims "auto
  refreshes every 60 seconds." Pick the intended behavior, remove the redundant
  one, and make the docs match.

## Project: Trade Tracker — Testing & CI

- **Add pytest setup for `trade-tracker/api`** — **Medium.** Zero automated
  tests exist. Add `pytest` / `pytest-asyncio` / `httpx`, a throwaway test
  Postgres seeded from the real schema (including the `db.py` migrations), and
  tests for `/health`, trades CRUD, auth middleware (once it exists), and
  `portfolio_metrics` (covering the cash-flow fix).
- **Add CI workflow** — **Medium.** Depends on pytest setup. GitHub Actions:
  spin up Postgres, apply the schema + migrations, run `pytest` on PRs touching
  `trade-tracker/api/**`.

## Project: QuestDB schema auto-apply (quant-side, low priority)

- **Auto-apply the QuestDB schema** — **Low.** Unlike the two Postgres DBs,
  `schemas/questdb_schema.sql` must be run manually in the QuestDB console
  (`http://localhost:9000`); `ingestion-service` writes silently fail until
  then. Add a one-shot init container that POSTs the schema to QuestDB's `/exec`
  on startup, or a documented script.

---

## Project: [AI] Portfolio News Sidebar (future, not started)

A Dashboard sidebar surfacing AI-summarized X/Twitter content relevant to the
firm's open positions — **without ever telling the AI service what the firm
holds.** Nothing is built yet. Lower priority than getting the existing Trade
Tracker actually working, but captured here so the design isn't lost.

**Privacy invariant (do not relax):** the AI/analysis step must never receive
DeKalb's holdings, position sizes, P&L, or account values. The set of tickers
the AI analyzes is a broad, human-curated watchlist on a slow cadence — never
derived from live positions. The join between "what's noteworthy" and "what we
hold" happens **only** inside a new authenticated `GET /news/relevant` endpoint
in `trade-tracker/api`; the AI/vendor never sees positions or position-derived
queries.

- **Resolve open design questions** — **High, blocks everything else.** Decide:
  (a) X API vs RSS for source posts (X's filtered-stream tiers are paid, ~$100/mo+),
  (b) AI provider — Claude Haiku recommended, needs an `ANTHROPIC_API_KEY` env
  var, (c) who maintains the watchlist and how often, (d) source of truth for
  "current positions" (`trading.positions` vs `trade_tracker.imported_positions`),
  (e) retention policy for `social_signals` (recommend ~90 days).
- **Scaffold `social-feed-service`** — **Medium.** New top-level service (own
  Dockerfile, like `ingestion-service/`): config loading + polling loop skeleton,
  same conventions (try/except around external calls, `logging`, no raising).
- **Implement watchlist polling** — **Medium.** Poll the chosen source for posts
  matching the human-curated ticker list (never derived from live positions).
- **Implement the AI extraction step** — **Medium.** For each new post, call the
  LLM with a privacy-preserving prompt → tickers mentioned, sentiment (-1..1),
  one-sentence summary, category (earnings/analyst-rating/macro/rumor/other).
  Send only public post text/metadata.
- **Add `social_signals` schema + writer** — **Medium.** Table (source,
  `source_post_id`, `author_handle`, `posted_at`, `raw_text`, `tickers[]`,
  `sentiment`, `summary`, `category`, unique on `(source, source_post_id)`, GIN
  index on `tickers`) and write extraction results to it.
- **Add `GET /news/relevant`** — **Medium.** New `routers/news.py`: query open
  positions, then `social_signals WHERE tickers && <open symbols>`. The ONLY
  place positions and social signals meet. Subject to normal auth.
- **Build the NewsSidebar component** — **Medium.** Collapsible Dashboard
  sidebar polling `/news/relevant`; each item shows ticker badge(s), sentiment,
  summary, timestamp, source link. Wire into `Layout.tsx`.
- **Retention/cleanup job for `social_signals`** — **Low.** Periodic delete of
  rows past the retention window.
- **Tests for extraction + `/news/relevant`** — **Medium.** Mock the LLM for
  extraction tests; test the positions/`social_signals` join.
- **Privacy review sign-off** — **High, gates go-live.** Confirm the watchlist
  stays broad/static and that no portfolio data appears in any
  `social-feed-service` logs, prompts, or outbound requests.
