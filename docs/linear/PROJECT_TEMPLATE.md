# Linear Project Template

A reusable template for the "New Project" screen in Linear, so every initiative (Trade Tracker hardening, AI news sidebar, etc.) is documented the same way.

## Save it as a real Linear template (do this once)

1. Linear → workspace settings (click your workspace name, top left) → **Templates**.
2. **New template → Project**.
3. Name the template `DeKalb Project Template`.
4. Fill in the fields below as the template defaults.
5. Paste the **Description template** section into the template's description editor.
6. Save. From now on, **New Project → "Use template" → DeKalb Project Template** pre-fills all of this.

## Field-by-field defaults

These map to the fields on Linear's "New project" screen:

| Field | Default / convention |
|---|---|
| **Name / icon** | `<Area>: <Project Name>` — e.g. `Equities: Trade Tracker Hardening`, `Platform: AI News Sidebar`. Pick a distinct icon/color per area so projects are scannable on the roadmap view. |
| **Short summary** | One sentence: what this delivers and why it matters right now. Shows up in list views — write it so someone outside the project understands the goal at a glance. |
| **Status** | `Backlog` until the project is scoped and ready to start, then `Planned` → `In Progress`. |
| **Priority** | `No priority` at the project level — set priority per-issue instead. |
| **Lead** | The DRI (directly responsible individual) for this initiative. Every project needs exactly one. |
| **Members** | Everyone actively working on it. Add/remove as work shifts. |
| **Team** | `Equities`, `Quant`, or `Platform` (see [`README.md`](README.md)). |
| **Labels** | Cross-cutting tags, e.g. `deploy`, `security`, `auth`, `data-quality`. Issue-level labels can add more specific ones. |
| **Dependencies** | Link any project this blocks or is blocked by (e.g. "AI News Sidebar" depends on "Trade Tracker Hardening" if it reuses the positions API). |
| **Visibility** | Workspace-visible by default. Only mark a project private if it covers something sensitive (e.g. compensation, security incidents). |
| **Milestones** | 2-5 milestones representing major checkpoints, each with its own target date. See template below. |

## Description template

Paste this into the project description, then fill in the blanks:

```markdown
## Overview
<1-3 sentences: what is this project, why now, who asked for it>

## GitHub
- **Repo:** yyardi/dekalb-database-tradetracker
- **Primary branch:** <e.g. claude/finish-ibkr-setup-EXd3O, or "issue branches off main">
- **Key paths touched:** <e.g. trade-tracker/api/, trade-tracker/frontend/>

## Goals / Success Criteria
- [ ] <concrete, checkable outcome>
- [ ] <concrete, checkable outcome>

## Out of scope
- <explicitly excluded items, so scope doesn't creep>

## Key risks / open questions
- <anything that could block or change the plan>

## Related docs
- <links to design docs in docs/features/, docs/PROJECT_STATUS.md items, etc.>
```

### Why a "GitHub" section instead of a native field

Linear doesn't have a built-in "GitHub branch" field for *projects* (only individual *issues* get auto-suggested branch names via the GitHub integration — see [`GITHUB_WORKFLOW.md`](GITHUB_WORKFLOW.md)). Putting repo/branch/paths at the top of the description gives anyone opening the project the same "where does this live in code" context a dedicated field would, and it's visible in the project's activity feed and any linked GitHub PRs.

## Milestones template

Use Linear's **Milestones** section on the project to break work into checkpoints. Suggested shape for most projects here:

1. **Scoped & ready** — design doc / issue list reviewed, dependencies identified.
2. **Core implementation** — main feature/fix merged to its branch.
3. **Hardening** — tests, error handling, edge cases.
4. **Deployed** — live on Railway/Vercel (or relevant environment), verified.

Not every project needs all four — delete what doesn't apply, but keep "Deployed/verified" as the last milestone for anything user-facing.
