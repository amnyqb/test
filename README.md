# Alfa Document

A local, isolated audit workbench that builds a persistent, evidence-bearing
**Document Dependency Graph** (DDG) across a narrative report and its Excel model,
and keeps that graph honest through revisions.

**Status:** Milestone 1 (working core) and the core of Milestone 3 (revision
handling and replay) are implemented and tested on synthetic fixtures. No empirical
result is claimed — see [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

```bash
ddg audit  path/to/package    --db ddg.sqlite   # import, check, report findings
ddg revise path/to/package-v2 --db ddg.sqlite   # carry the graph onto a new version
ddg replay <run_id>           --db ddg.sqlite   # re-execute a recorded run
```

- **[PROJECT_PLAN.md](PROJECT_PLAN.md)** — scope, architecture, data model, safety
  rules, evaluation design, milestones and required inputs
- **[docs/SETUP.md](docs/SETUP.md)** — install, run the tests, use the CLI
- **[docs/PROGRESS.md](docs/PROGRESS.md)** — what is built and what demonstrates it

The Python package and CLI are named `ddg`.

Derived from *Document Dependency Graphs — Research context and Claude build brief, v3, 14 September 2026*.
