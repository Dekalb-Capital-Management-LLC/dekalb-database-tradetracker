# Linear ↔ GitHub Workflow

## Day-to-day workflow

1. Create the issue in Linear first using the **DeKalb Issue** template
   (standalone or inside a **DeKalb Feature** project — see
   [`PROJECT_TEMPLATE.md`](PROJECT_TEMPLATE.md)).
2. On the issue, click **"Create branch"** (or use **Copy git branch name**,
   Cmd+Shift+.). Linear gives you `feature/eng-123-short-issue-title` — copy
   it, and paste it into the issue's `## Branch / PR` section.
3. Locally:
   ```bash
   git fetch origin
   git checkout -b feature/eng-123-short-issue-title origin/main
   ```
4. Work, commit, push:
   ```bash
   git push -u origin feature/eng-123-short-issue-title
   ```
   Because the branch name contains `eng-123`, Linear automatically:
   - Links the branch to the issue
   - Moves the issue to **In Progress**
5. Open a PR. As the PR moves through draft → ready → merged, the Linear
   issue status updates automatically (per the mapping in Settings →
   Integrations → GitHub).
6. When the PR merges, the issue moves to **Done**. If the change is
   user-facing, update [`../FEATURES.md`](../FEATURES.md).

## Multiple issues / one PR

If a PR addresses more than one Linear issue, list them all in the PR
description:

```
Closes ENG-123
Closes ENG-124
```

Each gets moved to Done independently on merge.

## Where the "confusion" usually comes from

- **Branch name drift**: if you rename a branch or create it manually without
  the `eng-123` token, Linear loses the link. Always start branches via
  Linear's "Create branch" button or Copy git branch name so you get the
  exact `feature/eng-123-...` string.
- **Multiple repos**: if DeKalb ever splits into multiple repos (e.g. a
  separate marketing site), make sure each repo is connected under the same
  Linear team, or issue links won't resolve across repos.
- **Personal forks**: the GitHub integration matches on the connected repo;
  PRs from forks may not auto-link. Prefer branches on
  `Dekalb-Capital-Management-LLC/dekalb-database-tradetracker` directly for
  this reason.
- **Status mapping**: Linear's default mapping (Todo → In Progress → In
  Review → Done/Cancelled) assumes your Linear workflow states are named
  similarly. If you've customized workflow state names, re-check the mapping
  in Settings → GitHub after any workflow rename.

## Recommended branch/PR naming summary

| Thing | Convention |
|---|---|
| Branch | `feature/eng-123-short-description` (Linear-generated) |
| Commit messages | Free text, but reference `ENG-123` in at least the final commit if the branch itself somehow lacks the ID |
| PR title | `[ENG-123] Short description` or just a clear description — the body link is what matters for automation |
| PR body | Include `Closes ENG-123` |
