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

**Tests:** 62 passing.

### Fixed along the way

The Milestone 1 store keyed nodes by position alone and overwrote relation and node
records on every run, and the eligibility gate treated every version ever imported
as current. None of that surfaced in single-version audits; all of it would have
broken revisions. Nodes are now version-scoped, relations and checks are recorded
per run, and current versions come from per-run lineage. Databases from the old
schema are refused with a clear message rather than misread.

## Not yet started

- **M2 semantic discovery** — candidate retrieval and LLM-proposed edges. No
  authorised source package yet.
- **M3 remainder** — a re-review queue for `STALE` relations, and new-link discovery
  over changed regions (which depends on M2 retrieval).
- **M4 comparative evaluation** — arms, ablations and statistics not built.

## Next executable step

A minimal review queue: list `STALE` / `UNRESOLVED` relations from the latest run,
record accept / reject / amend against the *current* node ids with actor and reason,
and republish. That closes the revise → re-review → re-check loop without M2.
