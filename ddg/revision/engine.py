"""Revise a maintained graph onto new source versions (F08/F09).

Order matters and is enforced here:

1. snapshot the new versions (old ones are retained);
2. re-anchor every prior node - remap on evidence or report it;
3. compute the affected closure, including definitions whose dependents' text
   did not change;
4. mark every invalidated verdict STALE and commit, *before* any new verdict
   is written;
5. carry reviewed relations forward, recording for each what was kept and why;
6. re-run every eligible check, then compare the verdicts the closure said
   were unaffected against their prior values - a disagreement is a closure
   miss, reported loudly, never absorbed;
7. publish.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ddg.cost import CostLedger
from ddg.extract import extract_explicit_relations
from ddg.ingest.docx_parser import document_text as docx_text
from ddg.ingest.pdf_parser import document_text as pdf_text
from ddg.models import CheckResult, Node, QuantityContext, SourceSnapshot
from ddg.pipeline import (
    MANIFEST_NAME,
    AuditReport,
    execute_checks,
    ingest_package,
    new_run_id,
)
from ddg.revision.closure import Closure, Disposition, compute_closure
from ddg.revision.remap import ChangeStatus, NodeChange, map_document
from ddg.store import Store

ACTOR = "ddg.revision"


@dataclass
class RevisionReport:
    run_id: str
    parent_run: str
    package_id: str
    documents: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    changes: list[NodeChange] = field(default_factory=list)
    closure: Closure | None = None
    stale_marked: int = 0
    results: list[CheckResult] = field(default_factory=list)
    refusals: list[tuple[str, str]] = field(default_factory=list)
    closure_misses: list[str] = field(default_factory=list)
    costs: dict[str, float] = field(default_factory=dict)

    def change_tally(self) -> dict[str, int]:
        counts = {s.value: 0 for s in ChangeStatus}
        for c in self.changes:
            counts[c.status.value] += 1
        return counts


def _flat_text(snap: SourceSnapshot) -> str | None:
    suffix = Path(snap.blob_path).suffix.lower()
    if suffix == ".docx":
        return docx_text(Path(snap.blob_path))
    if suffix == ".pdf":
        return pdf_text(Path(snap.blob_path))
    return None


def _carry_context(old: Node, new: Node) -> Node:
    """Reviewed context travels with the node; the new text fills only gaps."""
    merged = {
        f: getattr(old.context, f) if getattr(old.context, f) is not None
        else getattr(new.context, f)
        for f in QuantityContext.model_fields
    }
    return new.model_copy(update={"context": QuantityContext(**merged)})


def revise(package_dir: Path, store: Store, package_id: str = "pkg1") -> RevisionReport:
    parent = store.latest_run()
    if parent is None:
        raise ValueError("nothing to revise: no published audit in this graph store")

    run_id = new_run_id()
    store.begin_run(run_id, "revise", package_id, parent)
    ledger = CostLedger(store, run_id)
    report = RevisionReport(run_id=run_id, parent_run=parent, package_id=package_id)
    old_versions = store.document_versions(parent)
    shim = AuditReport(run_id=run_id, package_id=package_id)

    with ledger.timed("ingest"):
        snaps, parsed = ingest_package(
            package_dir, store, run_id, package_id, shim,
            reuse_versions={v for v in old_versions.values() if v},
        )
    report.unsupported = shim.unsupported
    if (Path(package_dir) / MANIFEST_NAME).is_file():
        report.notices.append(
            f"{MANIFEST_NAME} was not applied: during a revision, reviewed relations are "
            f"carried by re-anchoring. A manifest keyed by node id would re-attach by "
            f"coordinate, which is the failure this engine exists to prevent."
        )

    new_by_doc: dict[str, list[Node]] = defaultdict(list)
    for n in parsed:
        new_by_doc[n.selector.document_id].append(n)
    snap_by_doc = {s.document_id: s for s in snaps}

    with ledger.timed("remap"):
        for doc_id in sorted(set(old_versions) | set(snap_by_doc)):
            old_hash = old_versions.get(doc_id)
            snap = snap_by_doc.get(doc_id)
            old_nodes = [n for n in store.nodes([old_hash], as_of=parent)
                         if n.selector.document_id == doc_id] if old_hash else []

            if snap is None:
                store.record_document_version(run_id, package_id, doc_id, None)
                report.documents.append(f"{doc_id}: REMOVED from the package")
                report.changes += [NodeChange(ChangeStatus.UNRESOLVED,
                                              "document removed from the package", n)
                                   for n in old_nodes]
                continue
            if old_hash == snap.version_hash:
                report.documents.append(f"{doc_id}: unchanged ({old_hash[:12]})")
                report.changes += [NodeChange(ChangeStatus.UNCHANGED, "same version", n, n)
                                   for n in old_nodes]
                continue

            label = f"{old_hash[:12]} -> {snap.version_hash[:12]}" if old_hash else \
                f"new document ({snap.version_hash[:12]})"
            report.documents.append(f"{doc_id}: {label}")
            doc_changes = map_document(old_nodes, new_by_doc[doc_id], _flat_text(snap))
            carried = {c.new.node_id: _carry_context(c.old, c.new)
                       for c in doc_changes if c.mapped and c.old and c.new}
            new_by_doc[doc_id] = [carried.get(n.node_id, n) for n in new_by_doc[doc_id]]
            store.add_nodes(new_by_doc[doc_id], run_id=run_id)
            report.changes += doc_changes

        for c in report.changes:
            store.add_node_mapping(
                run_id,
                c.old.node_id if c.old else None, c.old.source_version if c.old else None,
                c.new.node_id if c.new else None, c.new.source_version if c.new else None,
                c.status.value, c.reason,
            )

    all_new = [n for nodes in new_by_doc.values() for n in nodes]
    with ledger.timed("closure"):
        new_explicit = extract_explicit_relations(all_new)
        closure = compute_closure(
            report.changes, list(store.relations(parent)), new_explicit,
            {n.node_id: n.source_version for n in all_new},
        )
    report.closure = closure

    # Record which meanings this revision put in question, and carry forward the
    # ones still open, so a withdrawn review cannot return without an answer.
    version_of = {n.node_id: n.source_version for n in all_new}
    for node_id, (kind, why) in closure.affected.items():
        if kind == "context" and node_id in version_of:
            store.add_context_review(run_id, node_id, version_of[node_id], "questioned",
                                     ACTOR, why)
    still_open = store.open_context_questions(
        {v for v in old_versions.values() if v}, as_of=parent)
    carried_prefix = "still open from an earlier revision: "
    for c in report.changes:
        if not (c.mapped and c.old and c.new) or c.new.source_version == c.old.source_version:
            continue
        reason = still_open.get((c.old.node_id, c.old.source_version))
        if reason is not None and closure.affected.get(c.new.node_id, ("",))[0] != "context":
            store.add_context_review(
                run_id, c.new.node_id, c.new.source_version, "questioned", ACTOR,
                reason if reason.startswith(carried_prefix) else carried_prefix + reason)

    # Stale before publish: committed before a single new verdict exists.
    for o in closure.outcomes:
        if o.disposition is not Disposition.KEEP:
            report.stale_marked += store.mark_results_stale(
                [f"chk:{o.old.relation_id}"],
                f"revision {run_id} ({o.disposition.value}): " + "; ".join(o.reasons),
            )
    store.conn.commit()

    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    why = {
        Disposition.KEEP: "kept: every endpoint re-anchored and nothing it depends on changed",
        Disposition.RECHECK: "review kept, verdict re-run: ",
        Disposition.REREVIEW: "review withdrawn until a person looks again: ",
        Disposition.UNRESOLVED: "cannot execute, endpoint not re-anchored: ",
    }
    carried_relations = []
    for o in closure.outcomes:
        if o.carried is None:
            continue
        carried_relations.append(o.carried)
        store.record_decision(o.old.relation_id, ACTOR, now, o.carried.review_state,
                              why[o.disposition] + "; ".join(o.reasons))
    relations = carried_relations + new_explicit
    store.add_relations(relations, run_id=run_id)

    with ledger.timed("check"):
        report.results, report.refusals = execute_checks(
            relations, {n.node_id: n for n in all_new},
            store.current_versions(run_id), store, run_id,
        )

    prior = {r.check_id: r for r in store.results(run_id=parent)}
    kept = {f"chk:{o.old.relation_id}" for o in closure.by_disposition(Disposition.KEEP)}
    for res in report.results:
        before = prior.get(res.check_id)
        if res.check_id in kept and before and (
                before.status != res.status or before.detail != res.detail):
            report.closure_misses.append(
                f"{res.check_id}: closure said unaffected, but {before.status.value} "
                f"-> {res.status.value}"
            )
    if report.closure_misses:
        ledger.record("closure_miss", note=f"{len(report.closure_misses)} verdicts changed "
                      "outside the computed closure; full re-run results were published")

    store.publish_run(run_id)
    report.costs = ledger.totals()
    return report


def render_revision(report: RevisionReport) -> str:
    lines: list[str] = []
    a = lines.append
    a(f"DDG revision  run={report.run_id}  parent={report.parent_run}  "
      f"package={report.package_id}")
    a("")
    a("Documents:")
    for d in report.documents:
        a(f"  - {d}")
    for n in report.notices:
        a(f"  ! {n}")
    a("")
    a(f"Node re-anchoring: {report.change_tally()}")
    for c in report.changes:
        if c.status is ChangeStatus.UNCHANGED and not c.moved:
            continue
        if c.status is ChangeStatus.ADDED:
            a(f"  + {c.new.node_id}")
            continue
        target = f" -> {c.new.node_id}" if c.new and c.new.node_id != c.old.node_id else ""
        a(f"  {c.status.value:10} {c.old.node_id}{target}: {c.reason}")
    a("")
    cl = report.closure
    if cl is not None:
        a("Carried relations:")
        for o in cl.outcomes:
            a(f"  [{o.disposition.value}] {o.old.relation_id}")
            for r in o.reasons:
                a(f"        {r}")
    a("")
    a(f"Prior verdicts marked STALE before publishing: {report.stale_marked}")
    a("New-link discovery over changed regions: NOT RUN (semantic retrieval is Milestone 2)")
    a("")
    a(f"Checks refused by the eligibility gate ({len(report.refusals)}):")
    for rid, reason in report.refusals:
        a(f"  x {rid}: {reason}")
    a("")
    a("Findings:")
    for res in report.results:
        a(f"  [{res.status.value}] {res.check_id}")
        a(f"        {res.detail}")
    a("")
    if report.closure_misses:
        a(f"CLOSURE MISSES ({len(report.closure_misses)}) - selective maintenance was wrong:")
        for m in report.closure_misses:
            a(f"  !! {m}")
    else:
        a("Closure self-check: every verdict outside the closure reproduced unchanged.")
    a(f"Cost:  {report.costs}")
    return "\n".join(lines)
