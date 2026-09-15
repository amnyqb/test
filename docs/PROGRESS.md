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
| F10 audit export | implemented | `store.export()` + rendered findings; `test_pipeline.py` |
| F12 cost measurement | implemented | per-stage ledger incl. human review minutes |
| N01 isolation | implemented | macros flagged and never run; injection fixture cannot force a PASS |
| N03 failure visibility | implemented | `NOT_CHECKED` / `NEEDS_REVIEW` never collapse into `PASS` |

**Scenarios demonstrated on synthetic fixtures:** T01 (workbook changes, narrative
does not → `FAIL` with the exact difference), T03 (equal values, different period →
`NEEDS_REVIEW`, not a false inconsistency), T04 (millions vs base units → `PASS`
after scaling), T08 (`INDIRECT` → reported and `NOT_CHECKED`, never a clean audit).

**Tests:** 43 passing.

## Not yet started

- **M2 semantic discovery** — candidate retrieval and LLM-proposed edges. Credential
  is configured; no authorised source package yet.
- **M3 revision handling** — the anchor remapping primitives exist and are tested;
  closure invalidation and audit replay are not built.
- **M4 comparative evaluation** — arms, ablations and statistics not built.

## Next executable step

Wire `revision/` on top of the existing `anchors.remap`: diff two versions,
re-anchor, compute the affected closure, mark prior verdicts stale before
publishing new ones (F08/F09, scenarios T05–T07).
