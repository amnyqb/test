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
from ddg.models import CheckDefinition, CheckResult, CheckStatus, Node, Relation
from ddg.review import apply_manifest, load_manifest
from ddg.store import Store

SUPPORTED = {".docx", ".xlsx", ".xlsm", ".pdf"}

#: An analyst's reviewed contexts and relations, if the package carries any.
MANIFEST_NAME = "review.json"


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


def audit(package_dir: Path, store: Store, package_id: str = "pkg1") -> AuditReport:
    run_id = uuid.uuid4().hex[:12]
    ledger = CostLedger(store, run_id)
    report = AuditReport(run_id=run_id, package_id=package_id)
    blob_dir = Path(package_dir).parent / "_blobs"

    all_nodes: list[Node] = []
    with ledger.timed("ingest"):
        for path in sorted(Path(package_dir).iterdir()):
            if not path.is_file() or path.name == MANIFEST_NAME:
                continue
            if path.suffix.lower() not in SUPPORTED:
                report.unsupported.append(f"{path.name}: unsupported file type")
                continue
            snap, nodes = ingest_file(path, package_id, blob_dir)
            store.add_snapshot(snap)
            store.add_nodes(nodes)
            all_nodes.extend(nodes)
            report.documents.append(f"{snap.document_id} ({snap.version_hash[:12]})")
            report.unsupported.extend(f"{snap.document_id}: {u}" for u in snap.unsupported_objects)
    report.node_count = len(all_nodes)

    manifest_path = Path(package_dir) / MANIFEST_NAME
    if manifest_path.is_file():
        with ledger.timed("apply_review"):
            manifest = load_manifest(manifest_path)
            all_nodes, reviewed, problems = apply_manifest(manifest, all_nodes, store)
            store.add_nodes(all_nodes)
            store.add_relations(reviewed)
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
        relations = extract_explicit_relations(all_nodes)
        store.add_relations(relations)
    report.relations = reviewed + relations
    relations = report.relations

    node_map = {n.node_id: n for n in all_nodes}
    versions = store.current_versions()

    with ledger.timed("check"):
        for rel in relations:
            verdict = eligible(rel, node_map, versions)
            if not verdict.ok:
                report.refusals.append((rel.relation_id, verdict.reason))
                continue
            chk = CheckDefinition(
                check_id=f"chk:{rel.relation_id}",
                relation_id=rel.relation_id,
                expression=rel.type.value,
                operand_node_ids=tuple(e.node_id for e in rel.endpoints),
                tolerance=Decimal("0.005"),
                checker_version=CHECKER_VERSION,
            )
            store.add_checks([chk])
            res = run_check(chk, rel, node_map, run_id)
            store.add_result(res)
            report.results.append(res)

    store.conn.commit()
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
