# Repo Audit & Roadmap

_Last updated: 2026-07-21_

Single source of truth for outstanding work across the repo. This is the input
for Linear — each bullet below is roughly one issue. Projects are the top-level
headings; bullets under them are the work. Priorities are inline (**Urgent /
High / Medium / Low**). No milestone structure yet — keep it flat and easy to
triage.

> **Reality check:** this doc was last rewritten 2026-06-11, and a lot has
> shipped since (IBKR integration, Fidelity CSV import wizard, Railway deploy,
> dashboard UI fixes, cash-flow tracking, win-rate fix). Most of what used to
> be "broken" below is now genuinely working — verified by reading the current
> code, not just taken on faith. What's left is a shorter, more honest list:
> mostly incomplete route-level coverage and a couple of configuration gaps.
> The production deploy (Railway done; Google OAuth + Cloudflare
> Pages in progress) is the active focus as of this update.

---

## Status snapshot

**Actually working:**
- Ingestion service (ZMQ → Postgres `trading` + QuestDB). Functionally complete,
  quant-team owned. Not the focus of this audit.
- `docker compose up --build` provisions both Postgres DBs and starts all
  services.
- Trade Tracker API boots, serves `/health`, schema-aware `/health/ready`,
  `/docs`, and the portfolio / trades / import / market / ibkr endpoints
  against the `trade_tracker` DB.
- The canonical Trade Tracker schema defines all seven application tables;
  compatibility migrations upgrade existing databases and a parity test keeps
  Railway's bundled schema copy synchronized.
- **Google SSO auth** — `AuthMiddleware` is registered and `routers/auth.py` is
  included in `main.py`. `AUTH_ENABLED=true` genuinely enforces sign-in,
  restricted to `@<ALLOWED_EMAIL_DOMAIN>`. (Currently set to `false` while
  testing the dashboard without it — see Auth project below for what to check
  before flipping it back on.)
- **IBKR integration** — the cloud Web API (RSA OAuth) connects *and* returns
  real positions, live pricing, and trade history. `services/ibkr_client.py`
  has retry logic for IBKR's first-call-empty quirk, a `portfolio2` fallback,
  US-listed conid disambiguation, and 429-rate-limit backoff. `market_data.py`
  now prefers FirstRateData when `FIRST_RATE_DATA_PATH` is configured, then
  routes through IBKR and yfinance fallback.
- **Fidelity CSV import** — real Fidelity Activity and Portfolio Positions CSV
  exports parse correctly (`services/fidelity_parser.py`), via a
  preview/diff/commit wizard (`/import/preview` → `/import/commit`,
  `FidelityUpdateWizard.tsx`). Multi-account (per-row Account Name/Number),
  cash-sweep funds represented as $1-NAV positions instead of dropped.
- **Cash flow tracking** — `/portfolio/cash-flows` CRUD writes to `cash_flows`,
  and `portfolio_metrics.py` excludes them from the return/Sharpe/drawdown
  calc.
- **Win rate** — now real FIFO-matched per-sell P&L, not the old
  "positive cash proceeds" approximation that read ~100% unconditionally.
- Dashboard header/theme is consistent (no more invisible white-on-light
  text or mixed dark-theme classes); Sign-out button is wired to `signOut()`.
- Shared market-data price fetching + in-process auto-refresh loop (every 5 min)
  writing NAV snapshots.
- A custom XLSX portfolio upload (`Ticker | Date Acquired | Amount | Price
  Acquired`) still works as a secondary import path for the single
  `PORTFOLIO` account (`/import/trades`, legacy — the live UI uses the CSV
  wizard above for everything else).
- Railway backend deploy is live (`docs/DEPLOY_RAILWAY.md`); Vercel has been
  dropped in favor of Cloudflare Pages for the frontend.

**Still open / worth tracking (the rest of this document):**
- Google OAuth Cloud Console setup + Cloudflare Pages deploy — in progress,
  not yet smoke-tested end-to-end in production.
- `RISK_FREE_RATE_ANNUAL` still hardcoded to `0.0` in `portfolio_metrics.py`.
- No token-refresh flow for Google ID tokens (expire ~1h, hard-redirect to
  `/login` on expiry instead of silent re-auth).
- Settings/Notifications buttons in `Dashboard.tsx` still have no handler.
- Positions-as-synthetic-trades still double-counts in the trade ledger (both
  import paths).
- Three unreconciled refresh cadences (in-process 5 min, hourly
  `snapshot-cron`, frontend 5 min poll).
- Zero automated tests; CI is lint+build only, all steps report-only.

_(The docs themselves — CLAUDE.md, this file, FEATURES.md — were rewritten to
match this reality as of 2026-06-28, after roughly two weeks of unlogged
progress on IBKR, Fidelity import, and the production deploy.)_

---

## Project: Trade Tracker — Auth (working, finishing touches)

The frontend has a full Google SSO flow (`AuthContext`, `Login.tsx`,
`client.ts` bearer headers + `handle401`), and the backend now enforces it —
`main.py` registers `AuthMiddleware` and includes the auth router.
`AUTH_ENABLED` is currently set to `false` on Railway for dashboard testing
without sign-in friction.

- **Before flipping `AUTH_ENABLED=true` in production** — **Urgent.** Confirm
  `GOOGLE_CLIENT_ID` and `ALLOWED_EMAIL_DOMAIN` are set to real values (not
  placeholders) on Railway — `verify_google_id_token` raises immediately if
  `GOOGLE_CLIENT_ID` is empty, which would 401 every request. Also confirm the
  Google Cloud Console OAuth client's Authorized JavaScript origins include
  the live Cloudflare Pages URL (see `docs/DEPLOY_GOOGLE_OAUTH.md`).
- **Hardcoded `/api/...` fetches bypassing `BASE`** — **Fixed 2026-06-28.**
  `AuthContext.tsx` and `Login.tsx` used to call `fetch('/api/auth/config')` /
  `fetch('/api/auth/verify')` directly instead of routing through `client.ts`'s
  `BASE` constant. This worked locally (Vite proxies `/api/*`) but would have
  silently broken sign-in on Cloudflare Pages, where there's no backend behind
  `/api/*` — the request would hit the SPA's catch-all `_redirects` rule and
  get back HTML instead of JSON. Both files now import `BASE` from
  `client.ts`. Watch for this pattern in any new auth-adjacent code.
- **Token refresh** — **Medium.** ID tokens expire after ~1h; `client.ts`'s
  `handle401()` hard-redirects to `/login` with no warning. Use Google Identity
  Services silent re-auth (`google.accounts.id.prompt()` / One Tap) to refresh
  before expiry, falling back to the redirect.
- **Wire up Settings/Notifications buttons** — **Low.** `Dashboard.tsx`'s
  Settings and Notifications buttons (next to the now-working Sign-out button)
  have no `onClick`. Either implement or remove them.

## Project: Trade Tracker — IBKR Integration (working)

The implementation is the **IBKR cloud Web API** (`api.ibkr.com`) using RSA
key-based OAuth 2.0 / JWT bearer flow — server-to-server, no browser login, no
desktop gateway. See `services/ibkr_client.py` and `config.py`.

**Current state: positions, pricing, and trade history all return real data.**
The historical blocker — IBKR's `/portfolio/{account}/positions` returning
`[]` on first call, snapshot endpoints needing a poll-until-populated loop —
is handled: retries + `portfolio2` fallback for positions, polling for
`field 31` (with status-character stripping like `"C"` for stale closes) for
snapshots, US-listed conid preference for symbol resolution.

- **Keep an eye on `IBKR_SERVER_IP`** — **Medium.** IBKR ties the OAuth session
  to an outbound IP. On Railway this is the static outbound IP (Pro plan
  feature); `config.validate_ibkr_oauth_config` logs a warning if the detected
  IP doesn't match what's configured. Re-verify after any Railway plan/region
  change.
- **`ibkr_parser.py` (CSV import) is dead code** — **Low, not a bug.**
  Unreferenced by any router. Superseded by the live API integration, which
  gets the same data without a manual IBKR Activity Statement export. Fine to
  leave as-is; flag if asked to clean up unused services.
- **Decide whether to keep both sync endpoints** — **Low.** `routers/ibkr.py`
  has `/ibkr/sync/trades` (PA transactions + recent iserver fills) feeding the
  `trades` table; confirm this is the only one the snapshot-cron and dashboard
  actually rely on, and document it if there's a second path still in use.

## Project: Trade Tracker — CSV / Fidelity Import (working, one cleanup item)

The live UI path (`FidelityUpdateWizard.tsx` → `/import/preview` →
`/import/commit`) correctly parses real Fidelity Activity and Portfolio
Positions CSV exports, supports multiple accounts per file (via the Account
Name/Number columns), and represents cash-sweep funds (SPAXX/FDRXX/FCASH) as
$1-NAV positions instead of dropping them. Options (dash-prefixed or
`YYMMDD[PC]strike` symbols) are intentionally skipped — not a bug, just an
unsupported instrument type for now.

- **Positions-as-synthetic-trades still double counts** — **Medium.** Each
  holding row in a Positions-format CSV (or the legacy XLSX) becomes a
  synthetic BUY/SELL in `trades` **and** is aggregated into
  `imported_positions`; both feed `/portfolio/summary`. A position snapshot is
  not a set of fills — these synthetic trades have made-up dates (today, for
  positions snapshots) and would corrupt anything that reads the trade ledger
  for realized P&L/performance by date. Win-rate already works around this by
  doing real FIFO matching: separating "holdings snapshot" from "trade
  history" as distinct concepts would still be the cleaner long-term fix.
- **Legacy `/import/trades` (XLSX) stays hardcoded to one account** —
  **Low.** `upload_trades` still hardcodes `account_id='PORTFOLIO'` and wipes
  that account's positions on each import. Not currently a live UI bug — the
  wizard (multi-account, CSV+XLSX via `/import/preview`) is what the frontend
  actually calls — but worth deciding whether to keep the legacy endpoint
  around or retire it in favor of routing XLSX uploads through the wizard too.

## Project: Trade Tracker — Production Deployment (Railway done, finishing the rest)

Backend is deployed and running on Railway. Vercel has been dropped — the
frontend deploy target is Cloudflare Pages (free tier, no new vendor to
evaluate). Step-by-step docs: `docs/DEPLOY_RAILWAY.md` →
`docs/DEPLOY_GOOGLE_OAUTH.md` → `docs/DEPLOY_CLOUDFLARE_PAGES.md`.

- **Finish the Google OAuth Cloud Console setup** — **Urgent.** Create/confirm
  the OAuth client, set Authorized JavaScript origins (localhost + the
  Cloudflare Pages URL once live), set `GOOGLE_CLIENT_ID` and
  `ALLOWED_EMAIL_DOMAIN` on Railway with real values.
- **Deploy the frontend to Cloudflare Pages** — **Urgent.** Root directory
  `trade-tracker/frontend`, build command `npm run build`, output `dist`, env
  var `VITE_API_BASE_URL` = the Railway API URL (set before the first build —
  it's baked in at build time).
- **Wire `FRONTEND_URL` + Google OAuth origins together** — **Urgent.** Set
  `FRONTEND_URL` on Railway to the Cloudflare Pages URL and redeploy; add the
  same URL to the Google OAuth client's Authorized JavaScript origins.
  `main.py` already splits `FRONTEND_URL` on commas, so a custom domain can be
  added alongside the `.pages.dev` URL later without code changes.
- **Production smoke test** — **High.** On the live URLs, with
  `AUTH_ENABLED=true`: sign in with a `@dekalbcapitalmanagement.com` account,
  confirm Dashboard loads (summary + chart), Trades tab works, a Fidelity CSV
  imports via the wizard, IBKR tab shows live positions, `/health`/`/docs`
  reachable without auth, a non-domain Google account is rejected.

## Project: Trade Tracker — Frontend / UI (mostly fixed)

- **Header/theme — fixed.** `Dashboard.tsx`'s header now uses a consistent
  light theme (`#1a2744` text on the light card background, no more
  `text-white`/`bg-gray-900` leftovers from a dark theme).
- **Sign-out — fixed.** Wired to `signOut()`.
- **Dead Settings/Notifications buttons** — **Low.** Still no `onClick`. See
  Auth project above.
- **No `Layout.tsx`/sidebar exists** — **Low, doc-accuracy only.** The nav is
  inline in `Dashboard.tsx`'s header; there's no separate routed page for
  Trades/Import — they're tabs/components inside the one `Dashboard` view
  (`react-router-dom` is installed and `BrowserRouter` wraps the app, but
  there's no `<Routes>`/`<Route>` yet). Not a bug, just don't go looking for
  files that don't exist.
- **Audit remaining tab states** — **Low.** IronBeam tab is a disabled
  placeholder; haven't done a full visual pass on empty/error/loading states
  across all tabs.

## Project: Trade Tracker — Data Model & Metrics correctness

- **Schema drift — fixed.** `schemas/trade_tracker_schema.sql` is the canonical
  definition of all seven application tables, including `imported_positions`,
  `ibkr_tokens`, and `instrument_conids`. The Railway-bundled copy is covered
  by a parity test; runtime migrations remain only for existing databases.
- **Cash flow tracking — fixed.** `/portfolio/cash-flows` CRUD + excluded from
  the return calc in `portfolio_metrics.py`.
- **Make the risk-free rate configurable** — **Medium, still open.**
  `RISK_FREE_RATE_ANNUAL` is hardcoded to `0.0` in `portfolio_metrics.py`. Move
  it to `config.py` as an env var with a documented default; Sharpe should
  change when it changes.
- **Win-rate — fixed.** Real FIFO-matched per-sell P&L now (see
  `_calculate_win_rate`), not the old "SELL trades with positive net_amount"
  approximation.
- **Reconcile refresh cadences** — **Low, still open.** Three different
  cadences are in play: the in-process `_auto_refresh_loop` in `main.py`
  (every 5 min), the `snapshot-cron` docker service (hourly: snapshot + IBKR
  trade sync), and the frontend's `setInterval(loadSummary, 300_000)` (5 min).
  Pick the intended behavior, remove the redundant one, and make the docs
  match.

## Project: Trade Tracker — Testing & CI

- **Add pytest setup for `trade-tracker/api`** — **Medium.** Zero automated
  tests exist. Add `pytest` / `pytest-asyncio` / `httpx`, a throwaway test
  Postgres seeded from the real schema (including the `db.py` migrations), and
  tests for `/health`, trades CRUD, the auth middleware, and `portfolio_metrics`
  (covering the cash-flow exclusion).
- **CI currently report-only** — **Medium.** `.github/workflows/ci.yml` runs
  frontend typecheck+build and Python lint+compile on every PR, but every step
  is `continue-on-error: true` — nothing blocks a merge yet. Once pytest exists
  and the codebase is stable enough, make the relevant steps blocking.

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
holds.** Nothing is built yet. Lower priority than the production deploy
above, but captured here so the design isn't lost.

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
  summary, timestamp, source link. Wire into the Dashboard header area (there's
  no `Layout.tsx` to hook into — see Frontend/UI project above).
- **Retention/cleanup job for `social_signals`** — **Low.** Periodic delete of
  rows past the retention window.
- **Tests for extraction + `/news/relevant`** — **Medium.** Mock the LLM for
  extraction tests; test the positions/`social_signals` join.
- **Privacy review sign-off** — **High, gates go-live.** Confirm the watchlist
  stays broad/static and that no portfolio data appears in any
  `social-feed-service` logs, prompts, or outbound requests.
