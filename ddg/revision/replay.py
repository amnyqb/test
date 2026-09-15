"""Historical replay (F11).

Rebuild an earlier audit from what was recorded - the snapshots it read, the
node records of those versions, the relations and check definitions of that
run - re-execute it, and compare with the verdicts stored at the time. Replay
reports agreement or divergence; it never overwrites history to make the two
match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ddg.check import eligible, run_check
from ddg.ingest.snapshot import verify
from ddg.store import Store


@dataclass
class ReplayReport:
    run_id: str
    blobs: list[tuple[str, bool]] = field(default_factory=list)
    reproduced: int = 0
    divergences: list[str] = field(default_factory=list)

    @property
    def faithful(self) -> bool:
        return all(ok for _, ok in self.blobs) and not self.divergences


def replay(store: Store, run_id: str) -> ReplayReport:
    if store.run(run_id) is None:
        raise ValueError(f"unknown run {run_id}")
    report = ReplayReport(run_id=run_id)

    versions = store.current_versions(run_id)
    for v in sorted(versions):
        snap = store.snapshot(v)
        ok = snap is not None and verify(snap)
        report.blobs.append((snap.document_id if snap else v[:12], ok))
        if not ok:
            report.divergences.append(f"snapshot {v[:12]} is missing or its blob changed")

    nodes = {n.node_id: n for n in store.nodes(versions, as_of=run_id)}
    relations = {r.relation_id: r for r in store.relations(run_id)}
    recorded = {r.check_id: r for r in store.results(run_id=run_id)}

    for chk in store.checks(run_id):
        rel = relations.get(chk.relation_id)
        before = recorded.get(chk.check_id)
        if rel is None or before is None:
            report.divergences.append(f"{chk.check_id}: relation or recorded result missing")
            continue
        gate = eligible(rel, nodes, versions)
        if not gate.ok:
            report.divergences.append(f"{chk.check_id}: gate now refuses - {gate.reason}")
            continue
        again = run_check(chk, rel, nodes, run_id)
        same = (again.status, again.detail, again.input_hashes, again.computation_trace) == \
            (before.status, before.detail, before.input_hashes, before.computation_trace)
        if same:
            report.reproduced += 1
        else:
            report.divergences.append(
                f"{chk.check_id}: recorded {before.status.value}, replayed {again.status.value}"
            )
    return report


def render_replay(report: ReplayReport) -> str:
    lines = [f"DDG replay  run={report.run_id}", ""]
    lines += [f"  blob {'ok      ' if ok else 'MISMATCH'} {doc}" for doc, ok in report.blobs]
    lines.append(f"  verdicts reproduced exactly: {report.reproduced}")
    lines += [f"  !! {d}" for d in report.divergences]
    lines.append("")
    lines.append("FAITHFUL" if report.faithful else "NOT FAITHFUL - see divergences above")
    return "\n".join(lines)
