"""The Milestone 1 audit path: import -> anchor -> extract -> gate -> check -> findings."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from ddg import CHECKER_VERSION
from ddg.check import eligible, run_check
from ddg.cost import CostLedger
from ddg.extract import extract_explicit_relations
from ddg.ingest import ingest_file
from ddg.models import (
    CheckDefinition,
    CheckResult,
    CheckStatus,
    Node,
    Relation,
    SourceSnapshot,
)
from ddg.review import apply_manifest, load_manifest
from ddg.store import Store

SUPPORTED = {".docx", ".xlsx", ".xlsm", ".pdf"}

#: An analyst's reviewed contexts and relations, if the package carries any.
MANIFEST_NAME = "review.json"

#: Tolerance applied to every numerical check in this milestone.
TOLERANCE = Decimal("0.005")


@dataclass
class AuditReport:
    run_id: str
    package_id: str
    documents: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    node_count: int = 0
    relations: list[Relation] = field(default_factory=list)
    results: list[CheckResult] = field(default_factory=list)
    refusals: list[tuple[str, str]] = field(default_factory=list)
    review_problems: list[str] = field(default_factory=list)
    reviewed_count: int = 0
    costs: dict[str, float] = field(default_factory=dict)

    def tally(self) -> dict[str, int]:
        counts = {s.value: 0 for s in CheckStatus}
        for r in self.results:
            counts[r.status.value] += 1
        return counts


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def ingest_package(
    package_dir: Path, store: Store, run_id: str, package_id: str, report: AuditReport,
    reuse_versions: set[str] | None = None,
) -> tuple[list[SourceSnapshot], list[Node]]:
    """Snapshot and parse every source in a package, recording its version for this run.

    A version listed in ``reuse_versions`` is already in the graph: its stored
    nodes, which carry reviewed context, are used instead of re-parsed blanks.
    """
    blob_dir = Path(package_dir).parent / "_blobs"
    snaps: list[SourceSnapshot] = []
    all_nodes: list[Node] = []
    for path in sorted(Path(package_dir).iterdir()):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        if path.suffix.lower() not in SUPPORTED:
            report.unsupported.append(f"{path.name}: unsupported file type")
            continue
        snap, nodes = ingest_file(path, package_id, blob_dir)
        store.add_snapshot(snap)
        if reuse_versions and snap.version_hash in reuse_versions:
            nodes = [n for n in store.nodes([snap.version_hash])
                     if n.selector.document_id == snap.document_id]
        else:
            store.add_nodes(nodes)
        store.record_document_version(run_id, package_id, snap.document_id, snap.version_hash)
        snaps.append(snap)
        all_nodes.extend(nodes)
        report.documents.append(f"{snap.document_id} ({snap.version_hash[:12]})")
        report.unsupported.extend(f"{snap.document_id}: {u}" for u in snap.unsupported_objects)
    report.node_count = len(all_nodes)
    return snaps, all_nodes


def execute_checks(
    relations: list[Relation],
    node_map: dict[str, Node],
    versions: set[str],
    store: Store,
    run_id: str,
) -> tuple[list[CheckResult], list[tuple[str, str]]]:
    """Every relation passes the eligibility gate or is refused with its reason."""
    results: list[CheckResult] = []
    refusals: list[tuple[str, str]] = []
    for rel in relations:
        verdict = eligible(rel, node_map, versions)
        if not verdict.ok:
            refusals.append((rel.relation_id, verdict.reason))
            continue
        chk = CheckDefinition(
            check_id=f"chk:{rel.relation_id}",
            relation_id=rel.relation_id,
            expression=rel.type.value,
            operand_node_ids=tuple(e.node_id for e in rel.endpoints),
            tolerance=TOLERANCE,
            checker_version=CHECKER_VERSION,
        )
        store.add_checks([chk], run_id=run_id)
        res = run_check(chk, rel, node_map, run_id)
        store.add_result(res)
        results.append(res)
    return results, refusals


def audit(package_dir: Path, store: Store, package_id: str = "pkg1") -> AuditReport:
    run_id = new_run_id()
    store.begin_run(run_id, "audit", package_id)
    ledger = CostLedger(store, run_id)
    report = AuditReport(run_id=run_id, package_id=package_id)

    with ledger.timed("ingest"):
        _, all_nodes = ingest_package(package_dir, store, run_id, package_id, report)

    manifest_path = Path(package_dir) / MANIFEST_NAME
    if manifest_path.is_file():
        with ledger.timed("apply_review"):
            manifest = load_manifest(manifest_path)
            all_nodes, reviewed, problems = apply_manifest(manifest, all_nodes, store)
            store.add_nodes(all_nodes)
            store.add_relations(reviewed, run_id=run_id)
            report.review_problems = problems
            report.reviewed_count = len(reviewed)
            ledger.record(
                "human_review",
                human_minutes=sum(r.review_minutes for r in manifest.relations),
                note=f"{len(manifest.relations)} reviewed relations",
            )
    else:
        reviewed = []

    with ledger.timed("extract_explicit"):
        explicit = extract_explicit_relations(all_nodes)
        store.add_relations(explicit, run_id=run_id)
    report.relations = reviewed + explicit

    node_map = {n.node_id: n for n in all_nodes}
    with ledger.timed("check"):
        report.results, report.refusals = execute_checks(
            report.relations, node_map, store.current_versions(run_id), store, run_id
        )

    store.publish_run(run_id)
    report.costs = ledger.totals()
    return report


def render(report: AuditReport) -> str:
    """Human-readable findings. 'Not checked' stays visible beside pass and fail."""
    lines: list[str] = []
    a = lines.append
    a(f"DDG audit  run={report.run_id}  package={report.package_id}")
    a(f"generated {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}")
    a("")
    a(f"Documents imported ({len(report.documents)}):")
    for d in report.documents:
        a(f"  - {d}")
    a(f"Nodes anchored: {report.node_count}")
    a("")
    a(f"Unsupported or untrusted content ({len(report.unsupported)}):")
    for u in report.unsupported or ["  (none)"]:
        a(f"  ! {u}" if report.unsupported else u)
    a("")
    if report.review_problems:
        a(f"Review manifest problems ({len(report.review_problems)}):")
        for p in report.review_problems:
            a(f"  ! {p}")
        a("")
    a(f"Relations ({len(report.relations)} total, {report.reviewed_count} human-reviewed):")
    for r in report.relations:
        a(f"  - {r.relation_id}  {r.type.value}  "
          f"[{len(r.endpoints)} endpoints]  {r.review_state.value}")
    a("")
    a(f"Checks refused by the eligibility gate ({len(report.refusals)}):")
    for rid, why in report.refusals or []:
        a(f"  x {rid}: {why}")
    if not report.refusals:
        a("  (none)")
    a("")
    a("Findings:")
    for res in report.results:
        a(f"  [{res.status.value}] {res.check_id}")
        a(f"        {res.detail}")
        for t in res.computation_trace:
            a(f"          . {t}")
    a("")
    a(f"Tally: {report.tally()}")
    a(f"Cost:  {report.costs}")
    return "\n".join(lines)
