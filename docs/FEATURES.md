# Features

Concise catalog of what this repo does. Update this table as features ship,
change status, or move — when a PR closes a Linear issue from
[`REPO_AUDIT.md`](REPO_AUDIT.md) that's user-facing, update the relevant row
here too (see [`linear/GITHUB_WORKFLOW.md`](linear/GITHUB_WORKFLOW.md)).

Status values: **Planned** (in production or ready to be), **Planned** (designed,
not built yet — see `REPO_AUDIT.md`), **Deprecated** (being removed, don't
build on it).

| Feature | Description | Status | Code |
|---|---|---|---|
| Google SSO auth | Google Workspace sign-in, domain-restricted, gated by `AUTH_ENABLED` | Planned | `trade-tracker/api/services/auth.py`, `routers/auth.py`, `main.py` (`AuthMiddleware`), `frontend/src/auth/` |
| Trade tracking | List/filter/label trades | Planned | `trade-tracker/api/routers/trades.py`, `frontend/src/pages/Trades.tsx` |
| Fidelity CSV import | Parse Fidelity activity export into `trades` | Planned | `trade-tracker/api/services/fidelity_parser.py`, `routers/imports.py`, `frontend/src/pages/Import.tsx` |
| Portfolio dashboard | Summary, positions, performance chart, snapshots | Planned | `trade-tracker/api/routers/portfolio.py`, `frontend/src/pages/Dashboard.tsx` |
| Portfolio performance metrics | Beta, Sharpe, alpha, max drawdown, win rate | Planned (approximate — see `REPO_AUDIT.md`) | `trade-tracker/api/services/portfolio_metrics.py` |
| Market data | Quotes/history via yfinance, optional IBKR | Planned | `trade-tracker/api/services/market_data.py`, `routers/market.py` |
| IBKR integration | Gateway status, account, positions, trade-fill sync | Planned (local only — no production story yet) | `trade-tracker/api/services/ibkr_client.py`, `routers/ibkr.py`, `ibkr-gateway/` |
| Daily snapshot cron | Hourly `POST /portfolio/snapshots/generate` | Planned | `docker-compose.yml` (`snapshot-cron` service) |
| Cash flow tracking | Deposits/withdrawals excluded from NAV performance | Planned | `schemas/trade_tracker_schema.sql` (`cash_flows` table, unused) |
| Production deploy | Railway (API) + Vercel (frontend) | Planned / in progress | `trade-tracker/api/railway.toml`, `trade-tracker/frontend/vercel.json` |
| Trading event ingestion | ZMQ listener → Postgres (`trading`) + QuestDB | Planned | `ingestion-service/`, `schemas/postgresql_schema.sql`, `schemas/questdb_schema.sql` |
| Portfolio AI news sidebar | AI-summarized X/Twitter content relevant to open positions, without exposing positions to the AI | Planned | none yet — see `REPO_AUDIT.md` (Project: `[AI] Portfolio News Sidebar`) |
