"""The re-review queue: what a revision handed back to a person (F06 after F09).

A revision can withdraw an approval (``STALE``), strand a relation whose
evidence could not be followed (``UNRESOLVED``), or put a value's meaning in
question. Only a person brings any of these back. This module lists what is
waiting, records each decision with actor, reason and minutes, and republishes
the graph as a new run - so earlier runs, and what they concluded, stay exactly
as recorded.

Two rules keep a decision from outrunning its reason:

* a review withdrawn because an endpoint's meaning was questioned cannot be
  accepted until that meaning is confirmed as it stands or amended;
* amending the context of a node withdraws every other approval that relied on
  the node's old meaning.

A batch of decisions is validated whole before anything is written: one bad
decision refuses the batch rather than publishing part of it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from ddg.cost import CostLedger
from ddg.models import (
    CheckResult,
    Endpoint,
    ExtractionMethod,
    Node,
    QuantityContext,
    Relation,
    ReviewState,
)
from ddg.store import Store

ACTOR = "ddg.review"

#: Review states that stop a relation executing until a person decides.
WAITING = frozenset({ReviewState.STALE, ReviewState.UNRESOLVED, ReviewState.PROPOSED})

#: Approvals an amended meaning withdraws. Formula-derived relations are not
#: judgements of meaning - the workbook asserts them - so they are re-run instead.
APPROVED = frozenset({ReviewState.ACCEPTED, ReviewState.AMENDED})


class Verdict(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    AMEND = "amend"


class Decision(BaseModel):
    """One reviewer's decision on one relation."""

    model_config = ConfigDict(frozen=True)

    relation_id: str
    verdict: Verdict
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    minutes: float = Field(default=0.0, ge=0)
    #: Amend only: the complete replacement endpoint list.
    endpoints: tuple[Endpoint, ...] | None = None
    #: Amend only: context fields to set on endpoint nodes; ``None`` clears one.
    contexts: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    #: Accept or amend: endpoints whose questioned meaning is confirmed as it stands.
    confirm_context: tuple[str, ...] = ()


class ReviewError(ValueError):
    """A decision batch was refused. Nothing was written."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("review refused; nothing was written:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass
class WaitingEndpoint:
    endpoint: Endpoint
    node: Node | None
    #: Why the node is not on the current version, when it is not.
    note: str = ""
    #: Why a revision put this node's meaning in question, while that is still open.
    question: str = ""


@dataclass
class QueueItem:
    relation: Relation
    why: str
    endpoints: list[WaitingEndpoint]


@dataclass
class ReviewReport:
    run_id: str
    parent_run: str
    applied: list[tuple[str, ReviewState]] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)
    stale_marked: int = 0
    results: list[CheckResult] = field(default_factory=list)
    refusals: list[tuple[str, str]] = field(default_factory=list)
    costs: dict[str, float] = field(default_factory=dict)


def _latest_reason(store: Store, relation_id: str) -> str:
    row = store.conn.execute(
        "SELECT reason FROM decisions WHERE relation_id = ? ORDER BY row_id DESC LIMIT 1",
        (relation_id,),
    ).fetchone()
    return row["reason"] if row else ""


def pending(store: Store) -> list[QueueItem]:
    """Relations in the latest published run that wait for a person."""
    run = store.latest_run()
    if run is None:
        return []
    versions = store.current_versions(run)
    current = {n.node_id: n for n in store.nodes(versions, as_of=run)}
    questions = store.open_context_questions(versions, as_of=run)

    items: list[QueueItem] = []
    for rel in store.relations(run):
        if rel.review_state not in WAITING:
            continue
        endpoints: list[WaitingEndpoint] = []
        for ep in rel.endpoints:
            node = current.get(ep.node_id)
            # A positional id can name different content in a newer version, so
            # an endpoint is current only if the relation was asserted against it.
            if node is not None and node.source_version in rel.valid_source_versions:
                endpoints.append(WaitingEndpoint(
                    ep, node, question=questions.get((node.node_id, node.source_version), "")))
                continue
            old = next((n for v in rel.valid_source_versions
                        if (n := store.node(ep.node_id, v, as_of=run)) is not None), None)
            versions_of_rel = list(rel.valid_source_versions) or [""]
            mapping = store.conn.execute(
                "SELECT status, reason FROM node_mappings WHERE old_node_id = ?"
                f" AND old_version IN ({','.join('?' * len(versions_of_rel))})"
                " ORDER BY row_id DESC LIMIT 1", (ep.node_id, *versions_of_rel),
            ).fetchone()
            note = "not on the current version"
            if mapping is not None:
                note += f" ({mapping['status']}: {mapping['reason']})"
            endpoints.append(WaitingEndpoint(ep, old, note))
        items.append(QueueItem(rel, _latest_reason(store, rel.relation_id), endpoints))
    return items


def _touched(rel: Relation, amended: dict[str, Node]) -> set[str]:
    """Amended nodes a relation relies on, matched by id *and* version."""
    return {e.node_id for e in rel.endpoints
            if e.node_id in amended
            and amended[e.node_id].source_version in rel.valid_source_versions}


def _validate(
    decisions: list[Decision],
    relations: dict[str, Relation],
    nodes: dict[str, Node],
    versions: set[str],
    questions: dict[tuple[str, str], str],
    parent: str,
) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    assigned: dict[tuple[str, str], str | None] = {}
    known_fields = set(QuantityContext.model_fields)
    # A questioned meaning answered anywhere in the batch counts for every decision.
    answered = {node_id for d in decisions if d.verdict is not Verdict.REJECT
                for node_id in (*d.confirm_context, *d.contexts)}

    for d in decisions:
        rid = d.relation_id
        rel = relations.get(rid)
        if rel is None:
            problems.append(f"{rid}: no such relation in run {parent}")
            continue
        if rid in seen:
            problems.append(f"{rid}: decided more than once in one batch")
        seen.add(rid)

        if d.verdict is not Verdict.AMEND and (d.endpoints is not None or d.contexts):
            problems.append(f"{rid}: endpoints and context can only be changed by amend")
        if d.verdict is Verdict.REJECT:
            if d.confirm_context:
                problems.append(f"{rid}: a rejection cannot confirm context")
            continue

        stranded = (
            rel.review_state is ReviewState.UNRESOLVED
            or not set(rel.valid_source_versions) <= versions
            or any(e.node_id not in nodes for e in rel.endpoints)
        )
        if d.verdict is Verdict.ACCEPT and stranded:
            problems.append(
                f"{rid}: cannot accept an UNRESOLVED relation - its endpoints point at a "
                f"superseded version; amend it with current endpoints")
            continue
        if d.verdict is Verdict.AMEND:
            if d.endpoints is None and not d.contexts:
                problems.append(f"{rid}: amend needs new endpoints, context changes, or both")
            if stranded and d.endpoints is None:
                problems.append(
                    f"{rid}: an UNRESOLVED relation must be amended with current endpoints")
                continue
            if d.endpoints is not None:
                if len(d.endpoints) < 2:
                    problems.append(f"{rid}: a relation needs at least two endpoints")
                problems += [f"{rid}: endpoint {e.node_id} is not a node of the current version"
                             for e in d.endpoints if e.node_id not in nodes]

        endpoint_ids = {e.node_id for e in (d.endpoints if d.endpoints is not None
                                            else rel.endpoints)}
        for node_id, values in d.contexts.items():
            if node_id not in endpoint_ids:
                problems.append(
                    f"{rid}: context change on {node_id}, which is not an endpoint of this relation")
            elif node_id not in nodes:
                problems.append(f"{rid}: {node_id} is not a node of the current version")
            unknown = sorted(set(values) - known_fields)
            if unknown:
                problems.append(f"{rid}: unknown context field(s) {', '.join(unknown)}")
            for name, value in values.items():
                prior = assigned.setdefault((node_id, name), value)
                if prior != value:
                    problems.append(
                        f"{node_id}: {name} set to both {prior!r} and {value!r} in one batch")
        for node_id in d.confirm_context:
            if node_id not in endpoint_ids:
                problems.append(
                    f"{rid}: confirms {node_id}, which is not an endpoint of this relation")
            elif node_id not in nodes:
                problems.append(f"{rid}: {node_id} is not a node of the current version")

        # A withdrawn review cannot come back while the meaning that withdrew it is open.
        for node_id in sorted(endpoint_ids):
            node = nodes.get(node_id)
            reason = questions.get((node_id, node.source_version)) if node else None
            if reason is not None and node_id not in answered:
                problems.append(
                    f"{rid}: the meaning of {node_id} was questioned ({reason}); "
                    f"confirm it as it stands or amend its context")
    return problems


def decide(store: Store, decisions: list[Decision]) -> ReviewReport:
    """Validate a batch of decisions, then publish them as one review run."""
    # Imported here: ddg.pipeline imports ddg.review for the manifest layer.
    from ddg.pipeline import execute_checks, new_run_id

    parent = store.latest_run()
    if parent is None:
        raise ReviewError(["nothing to review: no published run in this graph store"])
    versions = store.current_versions(parent)
    nodes = {n.node_id: n for n in store.nodes(versions, as_of=parent)}
    relations = {r.relation_id: r for r in store.relations(parent)}
    questions = store.open_context_questions(versions, as_of=parent)

    problems = _validate(decisions, relations, nodes, versions, questions, parent)
    if problems:
        raise ReviewError(problems)

    run_id = new_run_id()
    store.begin_run(run_id, "review", store.run(parent)["package_id"], parent)
    store.copy_document_versions(parent, run_id)
    ledger = CostLedger(store, run_id)
    report = ReviewReport(run_id=run_id, parent_run=parent)
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    amended: dict[str, Node] = {}
    amended_by: dict[str, set[str]] = {}
    for d in decisions:
        for node_id, values in d.contexts.items():
            base = amended.get(node_id, nodes[node_id])
            amended[node_id] = base.model_copy(
                update={"context": base.context.model_copy(update=values)})
            amended_by.setdefault(node_id, set()).add(d.reviewer)
    nodes.update(amended)
    store.add_nodes(amended.values(), run_id=run_id)
    for d in decisions:
        if d.verdict is Verdict.REJECT:
            continue
        for node_id in d.contexts:
            store.add_context_review(run_id, node_id, nodes[node_id].source_version,
                                     "amended", d.reviewer, d.reason)
        for node_id in set(d.confirm_context) - set(d.contexts):
            store.add_context_review(run_id, node_id, nodes[node_id].source_version,
                                     "confirmed", d.reviewer, d.reason)

    # Stale before publish: every verdict a decision or an amended context could change.
    by_id = {d.relation_id: d for d in decisions}
    for rel in relations.values():
        hit = _touched(rel, amended)
        if rel.relation_id in by_id or hit:
            why = "decision recorded" if rel.relation_id in by_id else \
                "context amended on " + ", ".join(sorted(hit))
            report.stale_marked += store.mark_results_stale(
                [f"chk:{rel.relation_id}"], f"review {run_id}: {why}")
    store.conn.commit()

    updated: list[Relation] = []
    for rel in relations.values():
        d = by_id.get(rel.relation_id)
        if d is None:
            hit = _touched(rel, amended)
            if (hit and rel.review_state in APPROVED
                    and rel.extraction_method is not ExtractionMethod.EXPLICIT_FORMULA):
                reviewers = sorted({r for node_id in hit for r in amended_by[node_id]})
                rel = rel.model_copy(update={"review_state": ReviewState.STALE})
                store.record_decision(
                    rel.relation_id, ACTOR, now, ReviewState.STALE,
                    f"review withdrawn until a person looks again: the context of "
                    f"{', '.join(sorted(hit))} was amended by {', '.join(reviewers)}")
                report.withdrawn.append(rel.relation_id)
            updated.append(rel)
            continue
        if d.verdict is Verdict.REJECT:
            new = rel.model_copy(update={"review_state": ReviewState.REJECTED})
            logged = ReviewState.REJECTED
        else:
            eps = d.endpoints if d.endpoints is not None else rel.endpoints
            new = rel.model_copy(update={
                "endpoints": tuple(eps),
                "review_state": ReviewState.ACCEPTED,
                "valid_source_versions": tuple(sorted({nodes[e.node_id].source_version
                                                       for e in eps})),
            })
            logged = ReviewState.AMENDED if d.verdict is Verdict.AMEND else ReviewState.ACCEPTED
        store.record_decision(rel.relation_id, d.reviewer, now, logged, d.reason)
        report.applied.append((rel.relation_id, logged))
        updated.append(new)
    store.add_relations(updated, run_id=run_id)
    ledger.record("human_review", human_minutes=sum(d.minutes for d in decisions),
                  note=f"{len(decisions)} review decision(s)")

    with ledger.timed("check"):
        report.results, report.refusals = execute_checks(
            updated, nodes, store.current_versions(run_id), store, run_id)

    store.publish_run(run_id)
    report.costs = ledger.totals()
    return report


def _context_text(node: Node) -> str:
    fields = {k: v for k, v in node.context.model_dump().items() if v is not None}
    return " ".join(f"{k}={v}" for k, v in fields.items()) or "(none)"


def render_queue(items: list[QueueItem], run_id: str | None) -> str:
    lines = [f"DDG review queue  run={run_id}  waiting={len(items)}", ""]
    if not items:
        lines.append("Nothing is waiting for a person.")
    for item in items:
        rel = item.relation
        lines.append(f"[{rel.review_state.value}] {rel.relation_id}  {rel.type.value}")
        if item.why:
            lines.append(f"    why: {item.why}")
        for w in item.endpoints:
            ep = w.endpoint
            if w.node is None:
                lines.append(f"    {ep.role:<11} {ep.node_id}  {w.note}")
                continue
            evidence = (w.node.raw_value or w.node.evidence_text).replace("\n", " ")
            if len(evidence) > 60:
                evidence = evidence[:57] + "..."
            lines.append(f"    {ep.role:<11} {ep.node_id}  {w.node.selector.describe()}  "
                         f"{evidence!r}")
            lines.append(f"    {'':<11} context: {_context_text(w.node)}")
            if w.question:
                lines.append(f"    {'':<11} ! meaning questioned: {w.question}")
            if w.note:
                lines.append(f"    {'':<11} ! {w.note}")
        lines.append("")
    lines.append("Decide with: ddg review decide RELATION_ID accept|reject|amend "
                 "--reviewer NAME --reason TEXT [--confirm-context NODE_ID]")
    return "\n".join(lines)


def render_review(report: ReviewReport) -> str:
    lines = [f"DDG review  run={report.run_id}  parent={report.parent_run}", "", "Decisions:"]
    lines += [f"  {state.value:<9} {rid}" for rid, state in report.applied]
    if report.withdrawn:
        lines += ["", "Reviews withdrawn because a node they rely on was given a new meaning:"]
        lines += [f"  STALE     {rid}" for rid in report.withdrawn]
    lines += ["", f"Prior verdicts marked STALE before publishing: {report.stale_marked}", "",
              f"Checks refused by the eligibility gate ({len(report.refusals)}):"]
    lines += [f"  x {rid}: {why}" for rid, why in report.refusals] or ["  (none)"]
    lines += ["", "Findings:"]
    for res in report.results:
        lines += [f"  [{res.status.value}] {res.check_id}", f"        {res.detail}"]
    lines += ["", f"Cost:  {report.costs}"]
    return "\n".join(lines)
