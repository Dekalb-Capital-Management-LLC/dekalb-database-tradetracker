# Features

Honest catalog of what this repo does and what state each feature is actually
in. Update a row as features ship or break — when a PR closes a Linear issue
from [`REPO_AUDIT.md`](REPO_AUDIT.md) that's user-facing, update the row here too
(see [`linear/GITHUB_WORKFLOW.md`](linear/GITHUB_WORKFLOW.md)).

**Status values:**
- **Working** — verified to work in at least one environment.
- **Broken** — code exists but does not work / is not wired up. See `REPO_AUDIT.md`.
- **Partial** — works in a limited/incorrect way; needs rework.
- **Planned** — designed but not built yet.

| Feature | Description | Status | Notes / Code |
|---|---|---|---|
| Trading event ingestion | ZMQ listener → Postgres (`trading`) + QuestDB | **Working** | Quant-team owned, functionally complete. `ingestion-service/` |
| Portfolio dashboard | Summary, positions, performance chart, snapshots | **Working** | Header/theme fixed (consistent light theme); the flat-performance-graph bug (blank Sharpe/Beta/Std Dev on accounts with only 2+ recent snapshot rows) was fixed 2026-07-10 in `portfolio_metrics.py`. Verified live end-to-end 2026-07-20 with real positions and a full YTD graph. `frontend/src/pages/Dashboard.tsx`, `routers/portfolio.py` |
| Trade tracking | List/filter/label trades | **Partial** | API + tab work, but the trade ledger is still polluted by synthetic BUY/SELL rows generated from position-snapshot imports (both the Fidelity Positions CSV and legacy XLSX paths) — these get FIFO-matched alongside real fills and can skew realized P&L/win-rate. Tracked as Linear DEK-46 (fix) and DEK-49 (test coverage). `routers/trades.py`, `frontend/src/pages/Trades.tsx`, `routers/portfolio.py` (`_compute_realized_pnl_fifo`) |
| Portfolio (XLSX) import | Upload custom `Ticker \| Date \| Amount \| Price` XLSX | **Partial** | Legacy path (`/import/trades`), still single hardcoded `PORTFOLIO` account; superseded for live use by the Fidelity wizard below, which also accepts `.xlsx`. `routers/imports.py`, `services/universal_parser.py` |
| Fidelity CSV import | Parse real Fidelity Activity/Positions CSV exports | **Working** | Preview/diff/commit wizard, multi-account, cash-sweep funds handled. Parsing itself is correct; see the Trade tracking row above for the downstream synthetic-trade double-counting issue this feeds. `services/fidelity_parser.py`, `routers/imports.py` (`/import/preview`, `/import/commit`), `frontend/src/components/FidelityUpdateWizard.tsx` |
| Market data | Quotes/history via IBKR (primary) + yfinance (fallback) | **Working** | IBKR-first when `IBKR_ENABLED=true`; falls back to yfinance per-symbol when IBKR has no data. `services/market_data.py`, `routers/market.py` |
| IBKR integration | Cloud Web API (RSA OAuth) — account/positions/pricing/sync | **Working** | Positions, live pricing, and trade history (PA transactions + recent fills) all return real data. `IBKR_SERVER_IP` still needs to be finalized against Railway's Pro-plan static outbound IPs (non-blocking — a mismatch only logs a warning today). `services/ibkr_client.py`, `routers/ibkr.py` |
| Google SSO auth | Domain-restricted Google Workspace sign-in, gated by `AUTH_ENABLED` | **Working** | `AUTH_ENABLED=true` in production, genuinely enforced end-to-end — verified live: `/trades` returns `401` with no token, a real domain account can sign in. No token-refresh flow yet (`handle401()` hard-redirects to `/login` on the ~1h ID token expiry instead of silent re-auth); Settings/Notifications buttons next to Sign-out still have no handler. `services/auth.py`, `routers/auth.py`, `frontend/src/auth/` |
| Portfolio performance metrics | Beta, Sharpe, alpha, max drawdown, win rate | **Partial** | Cash flows now excluded and win-rate is real FIFO-matched P&L; risk-free rate is still hardcoded to 0 instead of configurable. `services/portfolio_metrics.py` |
| NAV snapshots / auto-refresh | Periodic NAV snapshot for the performance chart | **Partial** | Works, but three overlapping cadences (in-process 5 min, hourly cron, frontend 5 min) still need reconciling. `main.py`, `docker-compose.yml` (`snapshot-cron`) |
| Cash flow tracking | Deposits/withdrawals excluded from NAV performance | **Working** | `/portfolio/cash-flows` CRUD + `CashFlowModal.tsx`; excluded from the return calc in `portfolio_metrics.py`. |
| Production deploy | Railway (API + Postgres) + Cloudflare (frontend) + Google OAuth | **Working** | Done and verified end-to-end 2026-07-20: Railway backend, Cloudflare frontend, and Google OAuth are all live and wired together, smoke-tested with a real signed-in session (real positions, full YTD graph, populated metrics). Cloudflare deploys the frontend as a **Worker with static assets**, not classic Pages (`wrangler.jsonc`) — production URL is currently `*.workers.dev`; a custom domain is tracked separately (Linear DEK-47, low priority, since custom domains are off by default for Workers). Railway was rebuilt from scratch under new account ownership on 2026-07-20 after the original trial expired (billing issue, not a code issue) — see `REPO_AUDIT.md` for the env-var rebuild checklist if this recurs. Vercel and classic Cloudflare Pages have both been dropped. `railway.toml`, `docs/DEPLOY_RAILWAY.md`, `docs/DEPLOY_CLOUDFLARE_PAGES.md`, `docs/DEPLOY_GOOGLE_OAUTH.md` |
| Portfolio AI news sidebar | AI-summarized X/Twitter content relevant to open positions, without exposing positions to the AI | **Planned** | Not started — see `REPO_AUDIT.md` (Project: `[AI] Portfolio News Sidebar`) |
