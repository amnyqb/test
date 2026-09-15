# Document Dependency Graphs (DDG) — Implementation Plan

**Repository:** `amnyqb/test` • **Branch:** `claude/smart-documents-project-xqo43c`
**Source brief:** *Document Dependency Graphs — Research context and Claude build brief, integrated working draft v3, 14 September 2026* (34 pp.)
**Plan date:** 14 September 2026 • **Status:** plan only — no prototype code written yet, no empirical result claimed.

---

## 1. What is being built

A **local, isolated audit workbench** for one analyst reviewing a feasibility or investment report together with its supporting Excel workbook.

The system builds a **persistent, evidence-bearing Document Dependency Graph** over immutable snapshots of DOCX / digital-text PDF / XLSX sources, proposes typed semantic relations with an LLM, requires human review before execution, runs deterministic arithmetic and bounded logical checks, and survives document revisions without silently re-attaching old dependencies to new content.

**The hypothesis under test** (brief §"The most defensible remaining question"): does a *maintained* graph improve error detection and repeat-audit effort versus the *same* model, parsers, retrieval and checkers reconstructing dependencies at every audit? Novelty, reliability and economic benefit are all treated as open. A negative result is a valid result.

**Explicitly out of scope for v1:** scanned/handwritten input, live enterprise connectors, autonomous edits to sources, general legal interpretation, unrestricted theorem proving, multi-user access control, approvals or external communication, any claim of legal compliance or financial assurance.

---

## 2. Design principles (non-negotiable)

| # | Principle | Consequence in code |
|---|---|---|
| 1 | **The graph is an index over immutable snapshots**, never a replacement for the files | Content-addressed blob store; sources opened read-only; hashes re-verified on every run |
| 2 | **Semantic identity ≠ equal values** | `SAME_QUANTITY_AS` candidates are generated *without* value equality; equal-value negative pairs are first-class test fixtures |
| 3 | **Absence of evidence is a visible state** | `NOT_CHECKED` and `NEEDS_REVIEW` are rendered beside `PASS`/`FAIL`; never collapsed into `PASS` |
| 4 | **Documents are untrusted data, never instructions** | All extracted text enters prompts inside a data envelope; injection fixtures in the test suite |
| 5 | **Nothing executes without an approved, grounded, version-current relation** | Single eligibility gate in front of every checker; refusals logged with reason |
| 6 | **Multi-input relations are hyperedges** | `SUM_OF` keeps every operand and its role; never flattened into pairwise arrows |
| 7 | **Every cost is counted** | Token, runtime, retry and human-review-minute ledger from day one (F12) |

---

## 3. Architecture

```
 sources/ (read-only)          ┌──────────────────────────────────────────┐
   report.docx ───┐            │ 1. INGEST                                 │
   model.xlsx  ───┼──────────► │ hash → immutable snapshot → parse →       │
   appendix.pdf ──┘            │ node inventory + reconciliation report     │
                               └───────────────┬──────────────────────────┘
                                               ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ 2. ANCHOR LAYER   dual anchors: positional + quote (+ structural)  │
   │    DOCX: para idx + TextQuote(prefix/exact/suffix)                 │
   │    PDF : page + bbox + TextQuote                                   │
   │    XLSX: sheet!cell + defined-name + header-path + quote           │
   └───────────────┬───────────────────────────────────────────────────┘
                   ▼
   ┌─────────────────────────┐      ┌──────────────────────────────────┐
   │ 3a. EXPLICIT EXTRACTION │      │ 3b. SEMANTIC DISCOVERY            │
   │  cross-refs, numbering, │      │  BM25 + fuzzy + embedding recall  │
   │  formulas, named ranges │      │   → LLM typed-edge proposal       │
   │  (deterministic)        │      │   → schema + grounding validation │
   └───────────┬─────────────┘      └──────────────┬───────────────────┘
               └──────────────┬────────────────────┘
                              ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ 4. GRAPH STORE (SQLite + JSON export) — versioned, append-only     │
   │    nodes • quantity_context • relations • checks • results • costs │
   └───────────────┬───────────────────────────────────────────────────┘
                   ▼
   ┌──────────────────────┐   ┌──────────────────────────────────────┐
   │ 5. REVIEW QUEUE      │──►│ 6. ELIGIBILITY GATE                   │
   │ accept/reject/amend  │   │ approved ∧ grounded ∧ version-current │
   │ (actor, time, reason)│   └───────────────┬──────────────────────┘
   └──────────────────────┘                   ▼
                               ┌──────────────────────────────────────┐
                               │ 7. CHECKERS                           │
                               │  numeric: Decimal equality/sum/ratio  │
                               │  logic  : restricted Bool/date AST,   │
                               │           optional Z3 w/ timeouts     │
                               └───────────────┬──────────────────────┘
                                               ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ 8. REVISION ENGINE  re-anchor → remap or UNRESOLVED → invalidate   │
   │    dependency closure → re-retrieve → republish                    │
   └───────────────┬───────────────────────────────────────────────────┘
                   ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ 9. WORKBENCH UI (FastAPI + server-rendered HTML)                   │
   │    import summary • dependency table • focused graph • review      │
   │    queue • findings • revision comparison • exports                │
   └───────────────────────────────────────────────────────────────────┘
```

---

## 4. Data model

Six record types, all Pydantic v2 models persisted to SQLite and exportable as JSON. Schema validation is a **gate**, not a formality: a record that fails validation is rejected, logged and never executed (N03, S01).

| Record | Required content |
|---|---|
| `SourceSnapshot` | package_id, document_id, version_hash (SHA-256), media_type, import_time, permissions, parser_version, immutable local blob ref |
| `Node` | stable internal ID, source_version, node_kind, `LocationSelector`, exact evidence text, normalized value (where relevant), semantic context |
| `QuantityContext` | entity, metric, period, scenario, unit, currency, scale, sign, rounding_policy — **missing fields are explicit `null`, never guessed** |
| `Relation` | ID, type, role-labelled endpoints, evidence spans, extraction_method, model+prompt version, review_state, valid_source_versions |
| `CheckDefinition` | typed expression, operand IDs, preconditions, tolerance or rule scope, checker_version, expected result type |
| `CheckResult` | PASS / FAIL / NEEDS_REVIEW / NOT_CHECKED / ERROR, input hashes, evidence, run_id, reproducible computation trace |

### Relation families (v1)

| Type | Executable? | Critical semantics |
|---|---|---|
| `REFERENCES` | target-resolution only | carries no logical meaning beyond the reference |
| `SAME_QUANTITY_AS` | yes, after context compatibility | asserts identity, **not** equality — differing values may be the defect |
| `VALUE_FROM` | yes | adds authoritative direction; direction must be explicit or reviewed |
| `SUM_OF` | yes (hyperedge) | every operand + role preserved; stated rounding policy |
| `RATIO_OF` | yes | numerator/denominator named; scale and zero-denominator behaviour defined |
| `REQUIRES` / `DATE_CONSTRAINT` | only after reviewer approval | direction, conditions, exceptions, temporal scope preserved |
| `DEPENDS_ON` | **never auto-executable** | impact analysis and investigation only |

---

## 5. The anchoring strategy (the crux)

T05 (row inserted, clause renumbered) and F08 are where naive designs fail. Every node therefore carries **two independent anchors plus a structural path**:

- **Positional** — DOCX paragraph/run index; PDF page + bounding box; XLSX `Sheet!A1`.
- **Quote** — W3C Web Annotation `TextQuoteSelector`: `prefix` + `exact` + `suffix` (≈32 chars either side).
- **Structural** — heading path, table header path, defined name, list numbering path.

**Re-anchoring on revision runs quote-first, position-second, structure-as-tiebreak.** Agreement → remap. Disagreement or ambiguity → `UNRESOLVED`, surfaced to the analyst. A node is **never** identified by coordinate alone, so an inserted row cannot silently inherit a neighbour's edges.

---

## 6. Semantic layer

1. **Candidate generation** — union of BM25 (`rank_bm25`), fuzzy string (`rapidfuzz`), structural proximity, and embedding similarity. Deliberately **not** filtered by numerical equality, so T01 (changed workbook value, unchanged narrative) and T03 (same number, different year) both remain reachable. Candidate recall is measured *before* classification (F04, S03).
2. **LLM proposal** — replaceable model adapter; typed JSON output; every proposed edge must cite verbatim source spans.
3. **Grounding validation** — each cited span is re-located in the cited source version. A span that does not resolve verbatim **fails the edge**, regardless of how plausible it reads. This is the primary hallucination defence (S01).
4. **Calibration honesty** — a model confidence number is stored as a raw score and labelled uncommitted until calibration is actually measured. It is never presented as a probability.
5. **Review queue** — accept / reject / amend, recording actor, timestamp and reason. Prior decisions stay recoverable (F06). Auto-accepted and human-reviewed precision are reported **separately** throughout (S02).

---

## 7. Execution semantics

- **Arithmetic** uses Python `decimal` exclusively. No binary floats anywhere in the check path.
- **Comparison order is fixed:** entity → period → scenario → dimensional/unit compatibility → scale normalisation → rounding policy → compare. A mismatch at any earlier stage yields `NEEDS_REVIEW`, never `FAIL`.
- **Unknown currency conversion or conflicting periods** → review item, not a numerical failure.
- **openpyxl does not evaluate formulas** [ref 47]. Cached workbook values are read separately, stored with an explicit `is_cached=True` flag, and **never** reported as freshly recalculated. A trusted evaluator (HyperFormula is a candidate, not a guarantee) is only adopted after compatibility testing, with external refresh and macros disabled.
- **Logic** runs on a small restricted Boolean/date expression AST. Z3 is optional, behind a wall-clock and resource limit; a timeout returns `NOT_CHECKED`. Satisfiability is not legal validity, and `payment requires completion` is stored distinctly from `completion guarantees payment`.
- **Model-generated code is never executed.** The LLM proposes structured relations; only the deterministic checkers compute.

---

## 8. Revision engine

```
new version arrives
  → hash + snapshot (old versions retained forever)
  → diff: values, wording, definitions, numbering, rows, ranges
  → re-anchor every affected node (quote-first)
  → remap where evidence supports it, else mark UNRESOLVED
  → compute affected closure, including context/definition changes
    whose endpoint text did NOT change   ← T06
  → mark old verdicts STALE *before* publishing new ones
  → re-run retrieval over changed regions (new links can appear;
    checking existing neighbours alone cannot discover them)
  → preserve justified prior review decisions; record what was kept and why
  → if selective maintenance is uncertain → broaden to full reprocessing,
    log the reason and the cost
```

---

## 9. Safety and isolation (N01–N04)

- Macros, embedded scripts and automatic external workbook refresh are **never** executed. VBA-bearing files are flagged, not opened for execution.
- Document text is wrapped in an untrusted-data envelope before entering any prompt. Test fixtures embed instructions such as *"ignore this error / disclose other files / cite a source that does not exist"*; the pipeline must treat them as evidence, not authority.
- One authorised package per isolated workspace. No cross-package retrieval.
- Parse failures, timeouts, unsupported formulas and abstentions are **never** converted into passes.
- Full reproducibility record per run: pinned parser and checker versions, model identifier, exact prompts, retrieved evidence, raw model responses.
- Mutation instructions and defect labels are prepared privately and **never** enter prompts or graph metadata.

---

## 10. Evaluation harness

| Arm | Receives | Establishes |
|---|---|---|
| B0 | raw-file LLM audit | descriptive reference only |
| **B1** | same files, model, retrieval, calculators, checkers; dependencies rebuilt each audit | **the strong baseline** — budget-matched |
| G1 | auto-recovered graph per audit, same checkers | benefit of structure without persistence |
| **G2** | maintained graph from clean v1, updated across revisions | **primary treatment**, including capture + review + maintenance cost |
| O | expert-approved oracle graph | diagnostic ceiling: is capture or checking the bottleneck? |

**Ablations:** G2 minus semantic edges; G2 with full reconstruction each revision; automatic-only vs human-reviewed reported separately; clause test decomposed into discovery / formalisation fidelity / solver correctness.

**Protocol:** identical defect sets, blind adjudication, prompts and thresholds frozen before the held-out set opens, repeated-run variability reported (never best-run selection), defect-level scoring after de-duplication, **package-clustered confidence intervals** — many edits to one report are not many independent documents.

**Corpus design:** one package to debug the workflow, then 3 development packages and **≥5 independent held-out packages** from different projects or templates. Reference graph of 500–1,000 annotated scoped relations with **≥200 held-out semantic links**; ≥150 known defects across five families plus **150 matched clean controls** (so flagging everything cannot score well); ≥3 ordered revisions per held-out package including both semantic edits and purely structural moves. Two annotators independently label a subset, reconcile, and retain unresolved cases — annotation time is counted as project cost. The initial graph is built **before** mutations are inserted, and naturally occurring defects are reported separately from planted ones.

**Gates S01–S09** are carried verbatim into `evaluation/criteria.yaml` as machine-readable thresholds. Break-even: `N ≥ ceil(C0 / (CB − CG))`, valid only where `CB > CG`, reported in both currency and active analyst minutes at dated provider rates.

---

## 11. Toolchain — verified, not assumed

All packages below were **installed successfully in this environment on 14 September 2026** (Python 3.11.15, Node 22.22.2, 4 cores, 15 GB RAM, PyPI reachable through the agent proxy).

| Need | Choice | Why / limitation |
|---|---|---|
| Runtime | Python 3.11 in a venv | System `pip` has a broken `cryptography`/`_cffi_backend` binding — **a venv is required**, this is a real environment finding |
| DOCX | `python-docx` 1.2.0 | paragraph/run granularity for anchors |
| PDF | `pdfplumber` 0.11.10 (+ `pymupdf` 1.28.2) | pdfplumber gives word-level bboxes for region anchors; PyMuPDF as the faster fallback |
| XLSX | `openpyxl` 3.1.5 | structure + formulas; **does not evaluate** — cached values flagged |
| Schema | `pydantic` 2.13.5 + `jsonschema` 4.26.0 | validation gate for N03/S01 |
| Storage | **SQLite (stdlib)** + JSON export | schema, provenance and invalidation rules are the real work; a graph server stays optional |
| Retrieval | `rank-bm25` 0.2.2 + `rapidfuzz` 3.14.6 | lexical recall; embeddings added when a model endpoint is authorised |
| Logic | `z3-solver` 5.1.0.0 | optional, resource-limited; restricted AST is the default path |
| LLM | `anthropic` 1.5.0 | replaceable adapter interface; **no API key present in this environment** |
| UI | `fastapi` 0.141.1 + `uvicorn` 0.53.0, server-rendered HTML | no SPA, no Office plugin, no new file standard |
| Tests | `pytest` 9.1.1 | golden fixtures + property tests on decimal arithmetic |
| Arithmetic | stdlib `decimal` | no floats in the check path |

**Deliberately not adopted now:** GraphRAG (repository is largely in maintenance mode — useful as an extraction reference, unsafe as a sole dependency), Graphiti (strong maintenance comparator, but its fact-history model is not Word/Excel source-anchor migration), HyperFormula (candidate evaluator, requires a compatibility test and licence review first), any dedicated graph database (unjustified at this scale).

---

## 12. Repository layout

```
ddg/
  models/        pydantic records + SQLite schema + migrations
  ingest/        docx.py  pdf.py  xlsx.py  snapshot.py  reconcile.py
  anchors/       selectors.py  resolve.py  remap.py
  extract/       explicit.py  candidates.py  llm_adapter.py  grounding.py
  graph/         store.py  query.py  closure.py  export.py
  review/        queue.py  decisions.py
  check/         eligibility.py  numeric.py  logic.py  registry.py
  revision/      diff.py  invalidate.py  replay.py
  cost/          ledger.py
  ui/            app.py  templates/
  cli.py
corpus/          packages/ (real sources, git-ignored)  ledger.md
evaluation/      criteria.yaml  arms/  metrics.py  report.py
tests/           fixtures/  test_*.py  injection/
docs/            SETUP.md  SPECIFICATION.md  PROGRESS.md  LIMITATIONS.md
```

`corpus/packages/` is git-ignored from the first commit: authorised private source material must not enter the repository.

---

## 13. Milestones

Mapped to the brief's P0–P5. Each milestone ends with a **running demonstration**, not a document.

| # | Stage | Deliverable | Exit evidence |
|---|---|---|---|
| **M1** | P0 + P1 — working core | Ingest, dual anchors, explicit relations, reviewed graph, decimal checks, evidence-linked findings, cost ledger, CLI + minimal UI | ~50 reviewed relations and ~20 specified checks run end-to-end; **T01, T04, T08 behave correctly**; hash manifest unchanged; every audited node opens its true source location |
| **M2** | P2 — semantic discovery | Candidate retrieval, LLM-proposed typed edges, grounding validation, review queue, calibration record | Paraphrased quantity match (T02) and a prerequisite clause example work; **hard negatives (T03) correctly rejected**; review minutes recorded |
| **M3** | P3 — revision handling | Diff, re-anchoring, closure invalidation, re-review, historical replay | T05, T06, T07 pass; changed values, moved rows and an amended definition produce correct updates or explicit `UNRESOLVED`; **zero silent misattachment** |
| **M4** | P4 — comparative evaluation | B1 / G1 / G2 / oracle arms, ablations, clustered intervals, cost accounting, failure analysis | S01–S09 applied to held-out packages; inconclusive results reported as inconclusive |
| **M5** | P5 — extension | Authoring-time capture study *or* one external pilot | Only after the core works; instruments real writing/edit events — M1–M4 do **not** establish the authoring-by-product hypothesis |

The clause/date extension enters M2–M3 only once the numerical core works, and its metrics stay separate: a numerical success is not evidence of legal capability, and a legal failure must not obscure a numerical success.

---

## 14. Traceability (requirement → module → test)

| Req | Module | Test |
|---|---|---|
| F01 non-modifying import | `ingest/snapshot.py` | hash manifest before/after; inventory reconciliation |
| F02 fragment resolution | `anchors/resolve.py` | every audited node opens correct paragraph / PDF region / cell |
| F03 explicit relations | `extract/explicit.py` | golden fixtures: tables, named ranges, missing targets, external links |
| F04 context-based candidates | `extract/candidates.py` | recall measured pre-classification; mismatch-value positives + same-value negatives |
| F05 typed implicit edges | `extract/llm_adapter.py`, `grounding.py` | schema + endpoint + grounding checks; held-out precision/recall (S02/S03) |
| F06 human accept/reject/amend | `review/` | actor/time/reason recorded; decisions recoverable |
| F07 eligibility gate | `check/eligibility.py` | rejected/stale/ungrounded relations provably never execute |
| F08 change tracking | `revision/diff.py`, `anchors/remap.py` | renumbering, row insert, delete, changed definition, revised units |
| F09 invalidate/revalidate | `revision/invalidate.py`, `graph/closure.py` | stale-before-publish assertion; uncertainty widens reprocessing |
| F10 audit export | `graph/export.py` | issue carries versions, endpoints, rule, evidence, status, inputs |
| F11 historical versions | `revision/replay.py` | reconstruct an older audit from snapshots + recorded outputs |
| F12 cost measurement | `cost/ledger.py` | tokens, retrieval, runtime, failures, retries, human minutes |
| N01–N04 | `ingest/`, `check/`, `cost/` | injection fixtures, macro refusal, schema validation, run manifest |

---

## 15. Missing inputs — required from you

The brief is explicit that fabricated sources and simulated model success are unacceptable. Four inputs are genuinely yours to own:

1. **The authorised package** — one real report (DOCX/PDF) plus its Excel workbook, ideally with known revision history. *Blocks empirical work in M1 onward.*
2. **Hosted-model permission** — may that material be sent to a hosted model endpoint? *Blocks M2.*
3. **`ANTHROPIC_API_KEY`** — confirmed absent in this environment. Without it, semantic inference is **untested**, not simulated. *Blocks M2.*
4. **Domain reviewer** — who adjudicates quantity identity, authoritative source direction and clause meaning? *Blocks the S02/S03 gold graph and all clause execution.*

**Unblocked without any of the above:** the full M1 core, all deterministic checkers, the anchor and revision engines, the cost ledger, the injection-safety suite, the evaluation scaffolding, and clearly-labelled synthetic *unit* fixtures. Synthetic fixtures test mathematics; they never stand in for a corpus, and no empirical claim will be made from them.

---

## 16. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| PDF/table extraction corrupts evidence | high | reconcile cell/text inventories before any model work; test difficult real pages first |
| Plausible-but-wrong semantic edge | high | verbatim grounding gate, hard negatives, selective abstention, human review, separate auto/reviewed reporting |
| Anchors drift on revision | medium | dual anchors + structural path; `UNRESOLVED` over guessing; zero-silent-misattachment assertion |
| Clause formalisation reverses direction or drops an exception | medium | reviewer approval mandatory; bidirectional text↔rule check before execution |
| Cached Excel values mistaken for recalculated | medium | explicit `is_cached` flag; never compared as fresh |
| Capture/review cost exceeds audit saving | **unknown — the core economic risk** | full-workflow cost ledger from M1; break-even computed, not assumed |
| Over-claiming novelty | medium | prior art (DCR, Graphiti, Workiva, the five patent families) recorded in `docs/SPECIFICATION.md`; no broad novelty claim made |

---

## 17. Immediate next step

Begin **Milestone 1** on this branch: scaffold `ddg/`, implement the immutable snapshot store, the three parsers with dual anchoring, the SQLite schema with Pydantic validation, the decimal numeric checkers behind the eligibility gate, the cost ledger, and the pytest suite including the injection fixtures — then run it and show real output.

Real source files can be dropped into `corpus/packages/` at any point; the deterministic core does not wait on them.

---

*Plan only. No prototype has been built, no benchmark run, no vendor tested, and no performance figure in the source brief is treated as a forecast for this work.*
