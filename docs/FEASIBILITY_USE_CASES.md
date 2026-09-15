# Feasibility tests — draft for discussion

**Status:** draft v0.2, 15 September 2026, revised after an external review of v0.1.
The plan is agreed and the defects confirmed by quick probes are repaired. The Part 1
harness is built, and its first two use cases, G04 and G06, are running (results below).

## What changed from v0.1

- **Three kinds of evidence are kept apart:** generated correctness tests,
  real-document semantic tests, and comparative workflow experiments. Thousands of
  generated examples can test the software's mechanics. They cannot show that it
  understands real documents or saves audit work.
- **Three kinds of failure are kept apart:** a defect to repair, a missed
  performance target, and evidence against the research hypothesis. A parser bug
  calls for a repair; it does not show the project is infeasible.
- **Meaning is tested on all context fields together.** Matching values alone
  cannot establish matching quantities.
- **Revision tests include new dependencies introduced by an edit**, not only the
  invalidation of existing ones. Traversing the graph cannot find a dependency
  that was never recorded.
- **Economics counts the whole workflow**, including building and reviewing the
  graph in the first place. Cheap later checks can hide expensive construction.
- **The document-instruction test is reworded.** Document text must not override
  the checking rules, but legitimate edits to amounts, definitions or exceptions
  must still change results.
- **Semantic discovery moves up to an early gate on real material.** Before it was
  the last test, and optional. Otherwise the project could build an excellent
  revision engine around relationships that still need extensive manual
  construction.
- **Cases were added** for implicit clause dependencies, new dependencies after
  revision, insufficient or conflicting evidence, and capture during authoring.
  Some v0.1 cases were merged to keep the total at 20.

## Three kinds of evidence

| Kind | Input | What it can show | What it cannot show |
|---|---|---|---|
| **Generated correctness tests** | tiny synthetic inputs with the answer planted; thousands per case | whether the mechanics work, or a specific defect | whether the system understands real documents or saves work |
| **Real-document semantic tests** | a small authorised package; relationships annotated independently and kept out of the model's inputs | whether meaningful dependencies can be found in unfamiliar material (about 50 relationships gives a diagnosis, not reliability) | reliability, which needs held-out packages |
| **Comparative workflow experiments** | the same model and tools either maintaining the graph or rebuilding it each time; an expert-reviewed graph as a diagnostic condition | whether maintaining the graph saves audit work overall | anything beyond the packages tested |

## Three kinds of failure

| Kind | Example | Consequence |
|---|---|---|
| **Defect** | "£3bn" is read as £3 | repair it, add a regression test, rerun |
| **Target missed** | detection rate below the agreed floor | tune, narrow the scope, or accept a narrower claim |
| **Evidence against the hypothesis** | links in real material can't be found without extensive manual work, or maintaining the graph saves nothing | a strategic decision about the project |

## Part 1: Generated correctness tests

Each test covers one capability, with a planted answer and a tiny input. Every test
is seeded, so any failure reproduces exactly. The inputs are synthetic, so a pass
shows the mechanics work, not that real documents behave the same way.

| ID | Question | Varied across thousands | A failure is |
|---|---|---|---|
| G01 | Are numbers in prose read with the right value, sign, scale and currency, while look-alikes (years, clause numbers, dates) stay out? | formats such as 1,234.5, (3.1) million, 12.4m, £3bn and "per cent"; currencies; look-alike numbers | a defect |
| G02 | Does each workbook formula become the right relation, or an honest abstention? | SUM ranges, additions, ratios, cross-sheet, absolute and named references, unsupported functions | a defect (a wrong relation is worse than none) |
| G03 | Are numbers in report tables tied to their headers, scale and currency? | header layouts, merged cells, units in headers | a defect |
| G04 | Is a quantity's identity judged on everything that defines it? | entity, metric, period, scenario, currency, scale and rounding, varied **together**; missing fields | a defect, but any false PASS between different quantities blocks use |
| G05 | Is the arithmetic exact, and are planted errors caught without false alarms? | operand counts, negatives, zeros, decimal traps; error sizes near the rounding boundary; matched clean controls | a defect, or a missed target |
| G06 | Do structural edits keep every link on the right content? | inserting, deleting, moving and renumbering rows, paragraphs and sections | **any silent misattachment is evidence against the design** |
| G07 | Are content edits classified correctly? | value edits, rewording, renamed labels, duplicated labels | a defect |
| G08 | Does a change invalidate everything that depends on it? | graph shapes, where the change is, definition changes versus value changes | a missed dependent is evidence against the design; reaching too far is measured as review cost |
| G09 | When an edit plants a new dependency, is it found, or at least flagged, rather than silently missed? | where the new dependency appears and how it is worded | a missed target (full discovery is tested in Part 2) |
| G10 | Does history stay exact over long chains of edits? | 5–20 random revisions in sequence | a defect |
| G11 | Does it stay fast as packages grow? | 10 to 100,000 cells; relation density | a missed target, or an architectural problem if cost grows far faster than size |
| G12 | Can document text override the checking rules, while legitimate edits still change results? | injected instructions (must change nothing), paired with real edits to amounts, definitions and exceptions (must change the right verdicts) | a defect |
| G13 | Are "inconsistent", "unresolved" and "not checked" kept distinct, each with its evidence? | unsupported functions, missing values, conflicting sources, circular references | a defect |
| G14 | Can a review decision bypass meaning? | accepting while a definition is still unreviewed; amending context on a node other relations share; missing context | a defect |

## Part 2: Real-document semantic tests (the early gate)

An Anthropic API key is configured. Three things are still needed, and only you
can provide them: one authorised report and workbook (plus any relevant clauses),
confirmation that they may be sent to the API, and an independent annotator.

| ID | Question | Evidence | A failure is |
|---|---|---|---|
| S01 | Does retrieval surface the right candidates for paraphrased quantities? | about 50 independently annotated relationships, including paraphrases and deliberately chosen unrelated quantities | evidence against the hypothesis, if recall is low |
| S02 | Does a model propose correct, grounded links and refuse look-alikes? | the same annotations, kept out of the model's inputs; automatic and human-reviewed results reported separately | evidence against the hypothesis |
| S03 | Are implicit clause dependencies found, with the right direction and scope? | prerequisites expressed through paraphrase, definitions and exceptions | evidence against the clause extension |
| S04 | After a real revision, are new dependencies found without being told where to look? | controlled edits to copies of the package | evidence against the maintained-graph hypothesis |

## Part 3: Comparative workflow experiments

| ID | Question | Design | A failure is |
|---|---|---|---|
| C01 | Does maintaining the graph save audit work, counted end to end? | the same model and tools, rebuilding versus maintaining across a series of revisions. Count initial extraction, review, corrections, revisions and repeated audits. Use an expert-reviewed graph as a diagnostic condition. Report sample sizes and uncertainty. | evidence against the hypothesis |
| C02 | Does capturing relationships while writing cost less than recovering them afterwards? | a separate, later experiment | evidence against the authoring extension only |

## Confirmed by quick probes, then repaired

These were found on 15 September, before any harness existed, and repaired the same
day. Each has a regression test in `tests/test_foundations.py` or
`tests/test_review_queue.py`. All were defects, not evidence about feasibility.

| Defect | Now |
|---|---|
| `metric` was not compared, so revenue and contingency could pass as the same quantity | Identity requires the same entity, metric, period, scenario, unit and currency. |
| A missing scale was treated as units | A missing scale gives NEEDS_REVIEW, unless no value declares a scale and all values come from one document. |
| No rounding: "USD 12.4 million" against 12,437,210 was a FAIL | A declared `rounding_policy` (`exact`, `displayed` or `nearest:<step>`) is applied. Without one, the check uses the precision the figure was written to, and says so in the trace. The example now passes, but only when all context matches. |
| Sums and ratios ignored context (found during the repair) | If participants' declared values contradict each other, the check gives NEEDS_REVIEW. For ratios this covers entity, period and scenario; for sums, unit and currency too. |
| "12.4m", "£3bn", "(3.1) million" and percentages were misread | Abbreviations, accounting brackets, minus signs and percentages are read. "m" and "k" count as a scale only next to a currency, because "12.4m" alone may be metres. |
| Report table cells kept only their position | Cells carry their row and column headers, plus any currency and scale those headers state. |
| Accepting restored PASS while the meaning that withdrew the review was still in question | A revision records the meanings it questions. Accepting is refused until each is confirmed or amended, and open questions carry into later revisions. |
| Amending a shared cell flipped another relation to a false PASS | Amending a node's context withdraws every other approval that relies on it. |

Deliberately left open:
- numbers written in words
- "12.4m" with no currency stays unscaled, so checks on it abstain
- a table's first row and first column are assumed to be its headers
- periods are not inferred from headers

## Results so far (15 September 2026)

Run with `python -m feasibility run <ID> --n <N> --seed <S>`. Every figure below comes
from generated synthetic inputs. It shows whether the mechanics work, not how the
system performs on real documents.

| Use case | Cases | Result |
|---|---|---|
| G04 identity | 5,000 on seed 0, then 20,000 on seed 1 | All correct. No PASS between different quantities, and none with incomplete context. |
| G06 structural edits | 400 on seed 0, then 1,000 on seed 1, then 1,000 on seed 2 | See the rounds below. |

G06 ran in rounds. Each round on unseen seeds found a rarer failure:

| Round | Silent misattachments | Cause | Repair |
|---|---|---|---|
| Seed 0, 400 cases | 15 links in 14 cases | Figures in prose were followed by their digits and neighbouring text; labels were handed to other rows | Figures are now followed through their sentence, and label matches are checked for signs of a handover |
| Seed 1, 1,000 cases | 2 links in 1 case | Two rows swapped labels | A label match is not trusted if the label moved to a row with a different value while the old row kept the old value |
| Seed 2, 1,000 cases, run after all repairs and never used for tuning | None (95% confidence interval for any one case failing: 0% to 0.38%) | none | none |

Each failure became a regression test in `tests/test_remap_regressions.py`. In the
seed 2 round:
- 9,784 links were followed.
- 36 went to an identical repeat of the same sentence.
- 852 (about 8%) went to review.
- 459 were correctly left unresolved because their content was removed.
- 1 was misattached, but already flagged for review, so no check ran on it.

**Review cost after structural edits: early evidence for C01.** We measured how many
human-reviewed links need a person again after one to three structural edits. That
exposed one over-broad rule: whenever a formula gained a row, every reviewed link
touching its cells was withdrawn, so a single inserted row sent all links back for
review. The rule now withdraws only reviewed totals whose scope may have grown. On
the same 400 cases, the share of reviewed links needing a person again fell from 56%
to 31%. The remainder are links whose label or sentence could not be followed,
including content that was genuinely removed.

The generator deliberately uses few distinct values. That makes the label-handover
checks fire more often than they would on typical workbooks, so 31% is not an
estimate for real documents.

## Proposed order

1. **Repair the confirmed defects.** *Done, 15 September; see above.*
2. **Build the Part 1 harness.** *Started. The harness is built and G04 and G06 are
   running (see results above). Next: G05 (planted errors) and G14 (review bypass),
   then the remaining generated cases.*
3. **Run Part 2 on one small authorised package** as the early semantic gate. *The API
   key is configured. The package, permission to send it to the API, and an annotator
   are still needed.*
4. **Test revisions against complete re-evaluation**, including new dependencies
   (G09, S04).
5. **Run the Part 3 experiment.** Then apply the provisional success criteria to
   held-out material, reporting sample sizes, uncertainty and failure categories.
   A generated-test pass rate is never presented as real-document accuracy.

Step 2 can run while the remaining Part 2 inputs are being gathered.

## Open questions

1. Is this structure right?
2. For Part 2: which package, may it be sent to Anthropic's API, and who will
   annotate independently?
3. Should I propose default kill thresholds for your review in step 2?
