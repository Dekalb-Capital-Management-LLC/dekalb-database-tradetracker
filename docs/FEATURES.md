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
| Portfolio dashboard | Summary, positions, performance chart, snapshots | **Working** | Header/theme bugs fixed (consistent light theme). `frontend/src/pages/Dashboard.tsx`, `routers/portfolio.py` |
| Dashboard compatibility manifest | Read-only module/capability contract for current dashboard and future quant panels | **Working** | `/dashboard/capabilities` advertises stable module keys, data contracts, and quant compatibility state without coupling the dashboard to quant DB reads yet. `routers/dashboard.py`, `services/dashboard_capabilities.py` |
| Trade tracking | List/filter/label trades | **Partial** | API + tab work; trade ledger is still polluted by synthetic position rows from imports. `routers/trades.py`, `frontend/src/pages/Trades.tsx` |
| Portfolio (XLSX) import | Upload custom `Ticker \| Date \| Amount \| Price` XLSX | **Partial** | Legacy path (`/import/trades`), still single hardcoded `PORTFOLIO` account; superseded for live use by the Fidelity wizard below, which also accepts `.xlsx`. `routers/imports.py`, `services/universal_parser.py` |
| Fidelity CSV import | Parse real Fidelity Activity/Positions CSV exports | **Working** | Preview/diff/commit wizard, multi-account, cash-sweep funds handled. `services/fidelity_parser.py`, `routers/imports.py` (`/import/preview`, `/import/commit`), `frontend/src/components/FidelityUpdateWizard.tsx` |
| Market data | Quotes/history via FirstRateData, IBKR, and yfinance | **Working** | FirstRateData is preferred when `FIRST_RATE_DATA_PATH` is configured; otherwise IBKR is primary when `IBKR_ENABLED=true`, with yfinance as final fallback. Dashboard shows the active provider from `/market/provider/status`. `services/first_rate_data.py`, `services/market_data.py`, `routers/market.py` |
| IBKR integration | Cloud Web API (RSA OAuth) — account/positions/pricing/sync | **Working** | Positions, live pricing, and trade history (PA transactions + recent fills) all return real data. `services/ibkr_client.py`, `routers/ibkr.py` |
| Google SSO auth | Domain-restricted Google Workspace sign-in, gated by `AUTH_ENABLED` | **Working** | Backend registers the auth router + `AuthMiddleware`; genuinely enforced. Currently set to `AUTH_ENABLED=false` while testing the dashboard without sign-in friction — flip back to `true` for production (see `REPO_AUDIT.md` Auth project for the pre-flight checklist). `services/auth.py`, `routers/auth.py`, `frontend/src/auth/` |
| Portfolio performance metrics | Beta, Sharpe, alpha, max drawdown, win rate | **Partial** | Beta uses regression/SLOPE on finite, date-aligned portfolio and configurable benchmark returns and reports the paired sample count; cash flows are excluded and win-rate is real FIFO-matched P&L; risk-free rate is still hardcoded to 0 instead of configurable. `services/portfolio_metrics.py` |
| Factor analysis workspace | Interactive benchmark beta and portfolio/holding correlation matrix | **Working** | `/portfolio/factor-analysis` returns a typed OLS/daily-return contract with per-cell observation counts and top-position weights; `FactorAnalysisPanel.tsx` renders it for SPY, QQQ, IWM, and DIA. |
| NAV snapshots / auto-refresh | Periodic NAV snapshot for the performance chart | **Partial** | Works, but three overlapping cadences (in-process 5 min, hourly cron, frontend 5 min) still need reconciling. `main.py`, `docker-compose.yml` (`snapshot-cron`) |
| Cash flow tracking | Deposits/withdrawals excluded from NAV performance | **Working** | `/portfolio/cash-flows` CRUD + `CashFlowModal.tsx`; excluded from the return calc in `portfolio_metrics.py`. |
| Production deploy | Railway (API) + Cloudflare Pages (frontend) | **Partial** | Railway backend deployed and live. Cloudflare Pages (frontend) and the Google OAuth Cloud Console setup are in progress, not yet smoke-tested end-to-end. Vercel has been dropped. `railway.toml`, `docs/DEPLOY_RAILWAY.md`, `docs/DEPLOY_CLOUDFLARE_PAGES.md`, `docs/DEPLOY_GOOGLE_OAUTH.md` |
| Portfolio AI news sidebar | AI-summarized X/Twitter content relevant to open positions, without exposing positions to the AI | **Planned** | Not started — see `REPO_AUDIT.md` (Project: `[AI] Portfolio News Sidebar`) |
