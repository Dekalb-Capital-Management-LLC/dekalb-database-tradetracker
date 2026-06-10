# Linear Setup

How we use Linear for project/issue tracking on this repo, and how it connects to GitHub.

- [`PROJECT_TEMPLATE.md`](PROJECT_TEMPLATE.md) — a reusable "New Project" template (matches the fields in Linear's project creation screen) plus a description template that includes a GitHub branch field.
- [`GITHUB_WORKFLOW.md`](GITHUB_WORKFLOW.md) — how the Linear↔GitHub integration works and the branch/PR conventions to use going forward.
- [`TRADE_TRACKER_BACKLOG.md`](TRADE_TRACKER_BACKLOG.md) — ready-to-paste project + issues for hardening the Trade Tracker and finishing the Railway/Vercel deploy.
- [`../features/PORTFOLIO_AI_NEWS_SIDEBAR.md`](../features/PORTFOLIO_AI_NEWS_SIDEBAR.md) — design doc + project/issue breakdown for the new AI news sidebar feature.

## Quick orientation

There's one Linear workspace. Suggested teams (create if they don't exist yet):

- **Equities** — `trade-tracker/` (API + frontend), Fidelity/IBKR import, portfolio metrics.
- **Quant** — `ingestion-service/`, trading engine integration.
- **Platform** — cross-cutting infra: Docker, Railway, Vercel, schemas, CI.

Each "major initiative" (the things on your roadmap — Trade Tracker hardening, the AI news sidebar, etc.) is a **Project**. Day-to-day bugs/tasks are **Issues** inside those projects. Use [`PROJECT_TEMPLATE.md`](PROJECT_TEMPLATE.md) every time you start a new initiative so they're consistent.
