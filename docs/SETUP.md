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

## Run the feasibility harness

These are the generated correctness tests from the feasibility plan. Each use case
builds tiny inputs from a seed, so every run and every failure can be reproduced.

```bash
.venv/bin/python -m feasibility list
.venv/bin/python -m feasibility run G04 --n 5000
.venv/bin/python -m feasibility run G06 --n 1000 --seed 2 --json g06.json
.venv/bin/python -m feasibility repro G06 G06:2:17
```

The exit code is 1 if any generated case failed. The results are synthetic evidence
that the mechanics work, not a measure of performance on real documents.

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

## Review what a revision handed back

```bash
.venv/bin/python -m ddg.cli review list --db ddg.sqlite

.venv/bin/python -m ddg.cli review decide rev:capex accept \
    --reviewer "A. Analyst" --reason "the sentence states millions" --minutes 2 \
    --confirm-context 'report.docx#p3q0' --db ddg.sqlite

.venv/bin/python -m ddg.cli review decide rev:revenue amend \
    --reviewer "A. Analyst" --reason "Turnover is the renamed revenue line" \
    --endpoint 'narrative=report.docx#p4q0' --endpoint 'workbook=model.xlsx#Model!B8' \
    --context 'model.xlsx#Model!B8:period=FY26' --db ddg.sqlite
```

The three verdicts:

- **accept** re-approves the relation as it stands.
- **reject** removes it from execution.
- **amend** changes the relation, then accepts it:
  - `--endpoint ROLE=NODE_ID` replaces the endpoints. Give every endpoint.
  - `--context NODE_ID:FIELD=VALUE` sets a context field on an endpoint. An empty
    value clears the field.

`--confirm-context NODE_ID` confirms the meaning of an endpoint that a revision put in
question, leaving the meaning as it is. Acceptance is refused until every questioned
endpoint has been confirmed or amended. Amending a node's context also withdraws
every other approval that relied on that node.

An `UNRESOLVED` relation must be amended with current endpoints. A refused decision
writes nothing and exits with code 2.

Quote node ids in the shell. zsh treats the `!` in `Model!B8` as history expansion.

## Rounding

Every check allows for the rounding of each value it compares. To set the rounding
explicitly, put `rounding_policy` in the node's context:

- `exact`: no rounding is allowed.
- `displayed`: use the precision the figure is written to.
- `nearest:<step>`: the step is in the value's own scale. For example, `nearest:0.5`
  means a figure in millions rounded to the nearest half million.

With no policy, the check uses the precision the figure was written to; "12.4
million" allows ±50,000. Every check's trace says which rule applied.

## Graph store schema

The store is at schema v3. A database from an earlier schema is refused with a
message rather than misread. Audit into a new `--db`.

## Credentials

Milestones 1 and 3 need no credentials. Milestone 2 (semantic discovery) will read
`ANTHROPIC_API_KEY` from the environment or from `.env`, which git ignores. Keep that
file readable only by you (`chmod 600 .env`). No code calls the API yet.
