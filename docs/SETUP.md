# Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

> The system `pip` in some containers has a broken `cryptography` binding
> (`ModuleNotFoundError: _cffi_backend`). A virtual environment avoids it.

## Run the tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Audit a package

A package is a directory holding one report, its workbook, and optionally a
`review.json` manifest of reviewed contexts and relations.

```bash
.venv/bin/python -m ddg.cli audit path/to/package --db ddg.sqlite --export graph.json
```

Sources are copied into a read-only blob store beside the package and are never
modified. A failing check is a finding, not a tool error, so the exit code stays 0.

## Revise the graph onto a new version

Point `revise` at the directory holding the revised report and workbook, using the
same `--db`:

```bash
.venv/bin/python -m ddg.cli revise path/to/package-v2 --db ddg.sqlite
```

Every prior node is re-anchored or reported, invalidated verdicts are marked stale
before new ones are written, and each carried relation is labelled `KEEP`,
`RECHECK`, `REREVIEW` or `UNRESOLVED` with its reasons. A `review.json` in the
revised directory is not applied; reviewed relations travel by re-anchoring.

## Replay a recorded run

```bash
.venv/bin/python -m ddg.cli replay <run_id> --db ddg.sqlite
```

Exit code 0 means every blob re-verified and every recorded verdict reproduced
exactly; 1 means a divergence was found and listed.

## Graph store schema

The store is at schema v2. A database created before revision support is refused
with a message rather than read, because it keyed nodes by position alone. Audit
into a new `--db`.

## Credentials

Milestone 1 needs none. Milestone 2 (semantic discovery) reads
`ANTHROPIC_API_KEY` from the environment or a git-ignored `.env`.
