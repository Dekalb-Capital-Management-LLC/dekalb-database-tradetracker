# Linear ↔ GitHub Workflow

The Linear-GitHub integration is already enabled for this workspace/repo. This doc explains what it actually does and the conventions to follow so it stays useful instead of confusing.

## What the integration does automatically

Once enabled (Linear → Settings → Integrations → GitHub, connected to `yyardi/dekalb-database-tradetracker`):

1. **Branch names**: Each Linear issue gets a suggested branch name like `yyardi/eng-123-fix-cors-for-vercel` (team key + issue number + slug of the title). On an issue, click **"Copy git branch name"** (or the GitHub icon) to get it.
2. **PR/commit linking**: If a PR title, description, or any commit message contains the issue ID (e.g. `ENG-123`), Linear links that PR to the issue automatically. Linear shows the PR's status (draft/open/merged) directly on the issue.
3. **Auto status transitions** (if configured under Settings → Integrations → GitHub):
   - Opening a linked PR → issue moves to **In Progress**.
   - Marking the PR "Ready for review" → issue moves to **In Review**.
   - Magic words in the PR description/commit (`Fixes ENG-123`, `Closes ENG-123`, `Resolves ENG-123`) + merging the PR → issue moves to **Done** automatically.
4. **Back-references**: The GitHub PR gets a comment/link back to the Linear issue, so anyone reviewing the PR on GitHub can jump to the full context.

## The workflow to use going forward

For any new piece of work:

1. **Create the issue in Linear first**, inside the relevant project (see [`PROJECT_TEMPLATE.md`](PROJECT_TEMPLATE.md) for project setup, [`TRADE_TRACKER_BACKLOG.md`](TRADE_TRACKER_BACKLOG.md) for the current backlog). Give it a clear title — it becomes the PR/branch slug.
2. **Copy the branch name** from the issue (GitHub icon on the issue → "Copy branch name", or "Create branch" if you have the GitHub CLI/desktop integration set up).
3. Create the branch from `main` using that name, do the work, push.
4. **Open the PR** with the issue ID in the title or description, e.g.:
   ```
   Fixes ENG-123: wire FRONTEND_URL into CORS for Railway/Vercel
   ```
5. Linear will show the PR on the issue and move it through the workflow states as the PR progresses. When the PR merges, the issue auto-closes.

## Existing branches that don't follow this convention

Branches like `claude/finish-ibkr-setup-EXd3O` predate this convention and won't auto-link. To connect a PR from one of these branches to a Linear issue retroactively:

- Add `Fixes ENG-123` (or `Part of ENG-123` if it's not the whole fix) to the PR description before merging, **or**
- Open the Linear issue and paste the GitHub PR URL into a comment — Linear will detect and link it.

Going forward, prefer creating the Linear issue first and using its generated branch name, even for AI-assisted branches — it keeps the auto-linking working.

## Multiple repos / monorepo note

This is a single monorepo (`ingestion-service/`, `trade-tracker/api/`, `trade-tracker/frontend/`, etc.) but the GitHub integration is per-*repository*, not per-folder. That's fine — just make sure issue titles/descriptions make clear which part of the repo they touch (e.g. prefix with `[frontend]`, `[ingestion]`, `[deploy]`), since the Team (Equities/Quant/Platform) and Project should already convey most of that context.
