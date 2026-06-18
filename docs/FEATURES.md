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
| Portfolio dashboard | Summary, positions, performance chart, snapshots | **Partial** | Renders, but header text is invisible (white-on-light) and theme is mixed. `frontend/src/pages/Dashboard.tsx`, `routers/portfolio.py` |
| Trade tracking | List/filter/label trades | **Partial** | API + page work; trade ledger is polluted by synthetic position rows. `routers/trades.py`, `frontend/src/pages/Trades.tsx` |
| Portfolio (XLSX) import | Upload custom `Ticker \| Date \| Amount \| Price` XLSX | **Partial** | Single hardcoded `PORTFOLIO` account; wipes positions on each import. `routers/imports.py`, `services/universal_parser.py` |
| Fidelity CSV import | Parse Fidelity Activity/Positions CSV exports | **Broken** | Parser is dead code; `/import/trades` only accepts `.xlsx`. `services/fidelity_parser.py` (unused) |
| Market data | Quotes/history via yfinance, optional IBKR | **Partial** | yfinance works; IBKR pricing does not. `services/market_data.py`, `routers/market.py` |
| IBKR integration | Cloud Web API (RSA OAuth) — account/positions/pricing/sync | **Broken** | Session connects, but positions and pricing never return data. `services/ibkr_client.py`, `routers/ibkr.py` |
| Google SSO auth | Domain-restricted Google Workspace sign-in, gated by `AUTH_ENABLED` | **Broken** | Frontend flow exists; backend never registers the auth router or middleware, so nothing is enforced. `services/auth.py`, `routers/auth.py`, `frontend/src/auth/` |
| Portfolio performance metrics | Beta, Sharpe, alpha, max drawdown, win rate | **Partial** | Computed but inaccurate — cash flows not excluded, win-rate approximate, risk-free rate hardcoded to 0. `services/portfolio_metrics.py` |
| NAV snapshots / auto-refresh | Periodic NAV snapshot for the performance chart | **Partial** | Works, but three overlapping cadences (in-process 5 min, hourly cron, frontend 5 min) need reconciling. `main.py`, `docker-compose.yml` (`snapshot-cron`) |
| Cash flow tracking | Deposits/withdrawals excluded from NAV performance | **Broken** | `cash_flows` table exists but nothing reads or writes it. `schemas/trade_tracker_schema.sql` |
| Production deploy | Railway (API) + Vercel (frontend) | **Planned** | Configs exist; never deployed or smoke-tested. `railway.toml`, `vercel.json` |
| Portfolio AI news sidebar | AI-summarized X/Twitter content relevant to open positions, without exposing positions to the AI | **Planned** | Not started — see `REPO_AUDIT.md` (Project: `[AI] Portfolio News Sidebar`) |
