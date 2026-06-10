# Project Status

_Last updated: 2026-06-10_

This is an audit of what's working, what's broken or unfinished, and what's next. It exists because months of context/documentation about this repo were lost as the team grew — this is the new source of truth. Items below that need tracking/prioritization are mirrored as issues in `docs/linear/` so they can be assigned and scheduled.

## What's working

- **Ingestion service** (`ingestion-service/`) — ZMQ → Postgres (`trading`) + QuestDB routing. Matches its original spec closely; functionally complete.
- **Trade Tracker API** (`trade-tracker/api/`) — trades CRUD/list/filter, portfolio summary/positions/performance/metrics, Fidelity CSV import, market data (yfinance), IBKR status/account/positions/sync.
- **Frontend** (`trade-tracker/frontend/`) — Dashboard, Trades, Import pages with charts (Recharts), Tailwind styling.
- **Auth** — Google Workspace SSO (Google Identity Services + server-side ID token verification), gated by `AUTH_ENABLED`, off by default for local dev.
- **IBKR integration** — direct connection to the local IBKR Client Portal Gateway (port 5001), no proxy/VPN. Account info, positions, and a 24h trade-fill sync.
- **Local dev** — `docker-compose up --build` provisions both Postgres databases (`trading`, `trade_tracker`) and auto-applies schemas on first boot. QuestDB needs one manual step (see below).

## Fixed in this session (2026-06-10)

- Removed stale "Pangolin proxy" / port 5000 / VPN references throughout `ibkr_client.py`, `market_data.py`, `routers/ibkr.py`, `routers/market.py` — these described an earlier architecture that was replaced by the direct gateway connection on port 5001.
- Added `FRONTEND_URL` config var (`config.py`) and wired it into CORS `allow_origins` in `main.py` — previously referenced in `railway.toml`'s comments but never implemented, so the deployed frontend would have been blocked by CORS.
- Fixed `trade-tracker` healthcheck in `docker-compose.yml` — it called `curl`, which doesn't exist in the `python:3.11-slim` image, so the container always reported unhealthy. Switched to a `urllib`-based check.
- Removed the dead placeholder URL (`your-railway-app.up.railway.app`) from `trade-tracker/frontend/vercel.json` and replaced the rewrite-based approach with `VITE_API_BASE_URL` (build-time env var, see `src/api/client.ts` and the new `src/vite-env.d.ts`). Vercel rewrites can't reference env vars, so a static rewrite could never have pointed at the real Railway URL.
- Rewrote `README.md` and `CLAUDE.md` to reflect the current architecture (auth, deployment, corrected IBKR docs).

## Known issues / gaps

These are documented in more detail as Linear issues in [`docs/linear/`](linear/).

1. **No automated tests for the Trade Tracker API.** `tests/` only has manual ZMQ scripts for the ingestion service. There's no pytest setup, no CI.
2. **`cash_flows` table is completely unused.** It exists in `trade_tracker_schema.sql` (with a comment saying it should be "excluded from NAV performance calc") but nothing inserts into it and nothing reads from it. As written, any deposit or withdrawal will look like a portfolio gain/loss in `/portfolio/performance` and `/portfolio/metrics` — beta, Sharpe, alpha, and drawdown will all be wrong around deposit/withdrawal dates.
3. **`RISK_FREE_RATE_ANNUAL` is hardcoded to `0.0`** in `portfolio_metrics.py`. Sharpe ratio is therefore really "return / volatility" with no risk-free adjustment.
4. **No token refresh for Google SSO.** ID tokens expire after ~1h; `client.ts` redirects to `/login` on a 401 with no silent refresh, so users get logged out mid-session once `AUTH_ENABLED=true`.
5. **Win-rate calculation is simplified**, not FIFO-matched: it's "% of SELL trades with positive `net_amount`", not a true realized-P&L-per-round-trip calculation. Fine as a rough number, misleading if someone treats it as exact.
6. **IBKR Gateway has no production story.** It's a desktop app requiring interactive 2FA — it cannot run on Railway. Production should run with `IBKR_ENABLED=false` (yfinance fallback) until there's a plan (e.g. a separate always-on machine running the gateway, exposed to Railway via a tunnel).
7. **Railway deployment needs one-time manual setup per service** that can't be done via code: in the Railway dashboard, each service (trade-tracker API, and separately the ingestion-service if/when it's deployed) needs its **Root Directory** set (`trade-tracker/api`, `ingestion-service`) and its env vars configured (see README "Deploying to Production"). This is likely why the deploy has been "in progress and broken."
8. **QuestDB schema isn't auto-applied.** Unlike the two Postgres databases, `schemas/questdb_schema.sql` has to be run manually via the QuestDB web console (`http://localhost:9000`) — easy to forget on a fresh environment, and ingestion-service writes will silently fail until the tables exist.

## Next steps

1. Walk through the Railway dashboard setup (root directory + env vars per the README) and confirm `trade-tracker/api` deploys and passes `/health`.
2. Walk through the Vercel setup, set `VITE_API_BASE_URL`, then set `FRONTEND_URL` back on Railway and confirm the deployed frontend can reach the deployed API (CORS + auth).
3. Set up the Linear workspace using [`docs/linear/`](linear/) (project template + GitHub integration), and import the issues listed there.
4. Decide on the `cash_flows` design (issue #2 above) before it causes a confusing metrics bug in production.
5. Scope and design the **Portfolio AI News Sidebar** feature — see [`docs/features/`](features/).
