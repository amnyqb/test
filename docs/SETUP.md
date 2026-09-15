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

## Credentials

Milestone 1 needs none. Milestone 2 (semantic discovery) reads
`ANTHROPIC_API_KEY` from the environment or a git-ignored `.env`.
