# Progress

Updated 15 September 2026.

## Milestone 1 — working core — IMPLEMENTED AND TESTED

| Requirement | State | Evidence |
|---|---|---|
| F01 import without modification | implemented | `test_ingest.py::test_sources_are_not_modified` — source hash identical before and after; blob store is `0444` |
| F02 resolve node to source fragment | implemented | dual anchors on every node; `test_ingest.py`, `test_anchors.py` |
| F03 explicit relations | implemented | SUM / ratio / addition / direct reference; `test_explicit.py` |
| F06 review decisions | implemented | manifest layer; actor, reason and time appended to `decisions` |
| F07 eligibility gate | implemented | 8 gate tests; rejected, stale, unapproved and `DEPENDS_ON` all refused |
| F10 audit export | implemented | `store.export(run_id)` + rendered findings; `test_pipeline.py` |
| F12 cost measurement | implemented | per-stage ledger incl. human review minutes |
| N01 isolation | implemented | macros flagged and never run; injection fixture cannot force a PASS |
| N03 failure visibility | implemented | `NOT_CHECKED` / `NEEDS_REVIEW` never collapse into `PASS` |

**Scenarios demonstrated on synthetic fixtures:** T01 (workbook changes, narrative
does not → `FAIL` with the exact difference), T03 (equal values, different period →
`NEEDS_REVIEW`, not a false inconsistency), T04 (millions vs base units → `PASS`
after scaling), T08 (`INDIRECT` → reported and `NOT_CHECKED`, never a clean audit).

## Milestone 3 — revision handling — CORE IMPLEMENTED AND TESTED

`ddg revise <package>` carries a maintained graph onto new source versions;
`ddg replay <run_id>` re-executes a recorded run and compares verdicts.

| Requirement | State | Evidence |
|---|---|---|
| F08 change tracking | implemented | `revision/remap.py`: quote-first re-anchoring, context-anchored edits, row-label/column-header anchors for cells, injectivity guard; `test_revision.py`, `test_anchors.py` |
| F09 invalidate / revalidate | implemented | `revision/closure.py`: value and context propagation, `KEEP` / `RECHECK` / `REREVIEW` / `UNRESOLVED`; stale-before-publish verified through a second DB connection |
| F11 historical versions | implemented | `revision/replay.py`: blob hashes re-verified, recorded checks re-executed, divergence reported rather than overwritten |

**Scenarios demonstrated on synthetic fixtures** (labels follow PROJECT_PLAN.md
§5, §8 and M3; the brief's own wording of T05–T07 was not available to check):

- **T05** — a row inserted inside the CAPEX block moves Equipment onto the address
  Contingency used to hold, *with the same value*. Contingency follows its row label
  to `B6`; nothing re-attaches to the old coordinate. A renumbered clause follows its
  text, not its paragraph index. The reviewed narrative-total relation is withdrawn
  for re-review because the formula it mirrors gained an operand.
- **T06** — an amended definition invalidates the CAPEX relation although the CAPEX
  number's own text is unchanged; the unrelated revenue check keeps its verdict. A
  changed scale word beside an unchanged number is treated the same way.
- **T07** — a removed row label leaves its relation `UNRESOLVED`; a duplicated label
  is `AMBIGUOUS`. Neither executes.
- **Changed value** — a revised narrative figure keeps its review, is re-checked, and
  the inconsistency is reported as `FAIL`.
- **Closure self-check** — after every revision, verdicts the closure called
  unaffected are compared with their prior values; any change is reported as a
  closure miss.

**Tests:** 108 passing in total, including those for the sections below.

### Fixed along the way

The Milestone 1 store keyed nodes by position alone and overwrote relation and node
records on every run, and the eligibility gate treated every version ever imported
as current. None of that surfaced in single-version audits; all of it would have
broken revisions. Nodes are now version-scoped, relations and checks are recorded
per run, and current versions come from per-run lineage. Databases from the old
schema are refused with a clear message rather than misread.

## Re-review queue — IMPLEMENTED AND TESTED

- **`ddg review list`** shows every relation a revision withdrew (`STALE`) or
  stranded (`UNRESOLVED`), with the recorded reason and each endpoint's current
  evidence and context.
- **`ddg review decide`** accepts, rejects or amends a relation, records the
  reviewer, reason and minutes, and republishes the graph as a new run. An
  amendment can replace endpoints, change context on endpoints, or both.

How it behaves:

- A batch of decisions is validated whole. If one decision is invalid, the batch is
  refused and nothing is written.
- An `UNRESOLVED` relation cannot simply be accepted, because its endpoints point at
  a superseded version. It must be amended with current endpoints.
- A stranded endpoint is never displayed as whatever now occupies its old address.
- Verdicts that a decision or an amended context could change are marked stale
  before new verdicts are written.
- Node records are kept per run (store schema v3). An amended context applies from
  the review run onward, and every earlier run still replays exactly.
- A review that was withdrawn because an endpoint's meaning was questioned cannot be
  accepted until that meaning is confirmed or amended.
- Amending a node's meaning withdraws every other approval that relied on it.
- Decisions carry into later revisions.

**Demonstrated:**

- **T06:** confirming the questioned scale and accepting lets the check run again, and
  it passes.
  Amending the scale to thousands turns the result into FAIL.
- **T07:** a blind accept is refused. Amending with the renamed row restores a pass.

## Foundations repaired — IMPLEMENTED AND TESTED

On 15 September, quick feasibility probes confirmed a set of defects. All are repaired,
each with a regression test (`tests/test_foundations.py`, `tests/test_review_queue.py`).
Checker and parser versions moved to 0.2.0.

- **Identity** now requires the same metric, as well as the same entity, period,
  scenario, unit and currency.
- **Scale** is never assumed. A missing scale gives NEEDS_REVIEW, unless no value
  declares one and all values come from one document.
- **Rounding** follows a declared `rounding_policy`: `exact`, `displayed` or
  `nearest:<step>`. Without one, it uses the precision the figure was written to, and
  the trace says so.
- **Sums and ratios** no longer ignore context. Declared values that contradict each
  other give NEEDS_REVIEW. This gap was found during the repair.
- **Prose figures:** "m", "bn", "k", accounting brackets, minus signs and percentages
  are now read. "m" and "k" count as a scale only next to a currency.
- **Report tables:** cells carry their row and column headers, and any currency and
  scale those headers state.
- **Review:** a revision records the meanings it questions, and accepting waits until
  each one is answered. Amending a node's meaning withdraws every other approval that
  relied on it.

## Not yet started

- **M2 semantic discovery** — candidate retrieval and LLM-proposed edges. No
  authorised source package yet.
- **M3 remainder** — new-link discovery over changed regions (depends on M2 retrieval).
- **M4 comparative evaluation** — arms, ablations and statistics not built.

## Next executable step

Build the generated correctness harness from
[FEASIBILITY_USE_CASES.md](FEASIBILITY_USE_CASES.md) (draft v0.2). Start with four
use cases: identity and rounding (G04), planted errors (G05), structural edits (G06)
and review bypass (G14).

For the real-document semantic gate (Part 2), an Anthropic API key is configured in
the git-ignored `.env`, but no code calls the API yet. Part 2 still needs an
authorised package, confirmation that the package may be sent to the API, and an
independent annotator.
