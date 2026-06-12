# Linear Setup

How we use Linear for project/issue tracking on this repo, and how it
connects to GitHub.

- [`PROJECT_TEMPLATE.md`](PROJECT_TEMPLATE.md) — the "DeKalb Feature" (project)
  and "DeKalb Issue" templates, including the label sets and branch format.
- [`GITHUB_WORKFLOW.md`](GITHUB_WORKFLOW.md) — how the Linear↔GitHub
  integration works and the branch/PR conventions to use going forward.
- [`LABELS_TODO.md`](LABELS_TODO.md) — one-time setup checklist for the
  label sets referenced by the templates; delete once done.
- [`../REPO_AUDIT.md`](../REPO_AUDIT.md) — the current project/issue backlog,
  ready to paste into Linear using the templates above.
- [`../FEATURES.md`](../FEATURES.md) — table of what the repo can do today,
  what's planned, and where the code lives.

## Quick orientation

One Linear team, key `ENG`. Branches look like
`feature/eng-123-short-issue-title` (see [`PROJECT_TEMPLATE.md`](PROJECT_TEMPLATE.md)).

Instead of separate teams per area, use the 6 **project labels** (`AI`,
`Equities`, `Quant`, `Platform`, `Frontend`, `IBKR`) to mark which part of the
repo a project/issue touches — see [`LABELS_TODO.md`](LABELS_TODO.md).

Each "major initiative" (the things on the roadmap — Trade Tracker hardening,
the AI news sidebar, etc.) is a **Project** ("DeKalb Feature" template).
Day-to-day bugs/tasks are **Issues** ("DeKalb Issue" template) inside those
projects, or standalone. [`../REPO_AUDIT.md`](../REPO_AUDIT.md) is laid out
this way already — copy each section straight into Linear.
