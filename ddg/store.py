"""SQLite graph store.

Append-only by construction: snapshots, nodes, relations, checks and results
are written once and superseded rather than mutated, so an older audit can be
reconstructed from what was actually recorded (F11).

Two identities matter across revisions and are kept apart deliberately:

* a **node** is scoped to one source version - ``(node_id, source_version)`` -
  because a positional id such as ``Model!B8`` names different content in
  different versions;
* a **relation** row is scoped to the run that asserted it, so the graph as it
  stood at any run can be read back exactly.

Which version of each document is current is recorded per run in
``document_versions``; it is never inferred from "every snapshot ever seen".
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator

from ddg.models import (
    CheckDefinition,
    CheckResult,
    Node,
    Relation,
    ReviewState,
    SourceSnapshot,
)

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    package_id   TEXT NOT NULL,
    parent_run   TEXT,
    started_at   TEXT NOT NULL,
    published_at TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    version_hash TEXT PRIMARY KEY,
    package_id   TEXT NOT NULL,
    document_id  TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_versions (
    row_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    package_id   TEXT NOT NULL,
    document_id  TEXT NOT NULL,
    version_hash TEXT
);
CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT NOT NULL,
    source_version TEXT NOT NULL,
    payload        TEXT NOT NULL,
    PRIMARY KEY (node_id, source_version)
);
CREATE TABLE IF NOT EXISTS node_mappings (
    row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    old_node_id TEXT,
    old_version TEXT,
    new_node_id TEXT,
    new_version TEXT,
    status      TEXT NOT NULL,
    reason      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    row_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_id  TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    type         TEXT NOT NULL,
    review_state TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id    TEXT NOT NULL,
    relation_id TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    row_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id     TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    status       TEXT NOT NULL,
    stale        INTEGER NOT NULL DEFAULT 0,
    stale_reason TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_id TEXT NOT NULL,
    actor       TEXT NOT NULL,
    decided_at  TEXT NOT NULL,
    state       TEXT NOT NULL,
    reason      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS costs (
    row_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    stage         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    seconds       REAL    NOT NULL DEFAULT 0,
    retries       INTEGER NOT NULL DEFAULT 0,
    human_minutes REAL    NOT NULL DEFAULT 0,
    note          TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_nodes_version ON nodes(source_version);
CREATE INDEX IF NOT EXISTS idx_results_check ON results(check_id);
CREATE INDEX IF NOT EXISTS idx_results_run ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_relations_run ON relations(run_id);
CREATE INDEX IF NOT EXISTS idx_docver_run ON document_versions(run_id);
"""


class SchemaError(RuntimeError):
    """The database predates this schema and cannot be read safely."""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='nodes'"
        ).fetchone()[0]
        if has_tables and version < SCHEMA_VERSION:
            self.conn.close()
            raise SchemaError(
                f"{self.path} uses graph schema v{version or 1}, which cannot represent "
                f"revisions (nodes were keyed by position alone). Audit into a new --db."
            )
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.commit()
        self.close()

    # -- runs and lineage -----------------------------------------------

    def begin_run(
        self, run_id: str, kind: str, package_id: str, parent_run: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT INTO runs (run_id, kind, package_id, parent_run, started_at)"
            " VALUES (?,?,?,?,?)",
            (run_id, kind, package_id, parent_run, _now()),
        )

    def publish_run(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE runs SET published_at = ? WHERE run_id = ?", (_now(), run_id)
        )
        self.conn.commit()

    def run(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()

    def latest_run(self, *, published_only: bool = True) -> str | None:
        sql = "SELECT run_id FROM runs"
        if published_only:
            sql += " WHERE published_at IS NOT NULL"
        row = self.conn.execute(sql + " ORDER BY rowid DESC LIMIT 1").fetchone()
        return row["run_id"] if row else None

    def record_document_version(
        self, run_id: str, package_id: str, document_id: str, version_hash: str | None
    ) -> None:
        """``version_hash=None`` records that the document left the package."""
        self.conn.execute(
            "INSERT INTO document_versions (run_id, package_id, document_id, version_hash)"
            " VALUES (?,?,?,?)",
            (run_id, package_id, document_id, version_hash),
        )

    def document_versions(self, run_id: str) -> dict[str, str | None]:
        return {
            row["document_id"]: row["version_hash"]
            for row in self.conn.execute(
                "SELECT document_id, version_hash FROM document_versions"
                " WHERE run_id = ? ORDER BY row_id", (run_id,)
            )
        }

    def current_versions(self, run_id: str | None = None) -> set[str]:
        """Versions current as of ``run_id`` (default: the most recent run).

        Superseded versions are excluded, which is what lets the eligibility
        gate refuse a relation still pointing at an older version.
        """
        if run_id is None:
            row = self.conn.execute(
                "SELECT run_id FROM document_versions ORDER BY row_id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return set()
            run_id = row["run_id"]
        return {v for v in self.document_versions(run_id).values() if v}

    # -- writes ---------------------------------------------------------

    def add_snapshot(self, s: SourceSnapshot) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
            (s.version_hash, s.package_id, s.document_id, s.model_dump_json()),
        )

    def add_nodes(self, nodes: Iterable[Node]) -> int:
        """Nodes are version-scoped; re-writing one within its own version
        (e.g. attaching reviewed context) replaces only that version's record."""
        rows = [(n.node_id, n.source_version, n.model_dump_json()) for n in nodes]
        self.conn.executemany("INSERT OR REPLACE INTO nodes VALUES (?,?,?)", rows)
        return len(rows)

    def add_node_mapping(
        self, run_id: str, old_node_id: str | None, old_version: str | None,
        new_node_id: str | None, new_version: str | None, status: str, reason: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO node_mappings (run_id, old_node_id, old_version, new_node_id,"
            " new_version, status, reason) VALUES (?,?,?,?,?,?,?)",
            (run_id, old_node_id, old_version, new_node_id, new_version, status, reason),
        )

    def add_relations(self, rels: Iterable[Relation], *, run_id: str) -> int:
        rows = [
            (r.relation_id, run_id, r.type.value, r.review_state.value, r.model_dump_json())
            for r in rels
        ]
        self.conn.executemany(
            "INSERT INTO relations (relation_id, run_id, type, review_state, payload)"
            " VALUES (?,?,?,?,?)", rows,
        )
        return len(rows)

    def add_checks(self, checks: Iterable[CheckDefinition], *, run_id: str) -> int:
        rows = [(c.check_id, c.relation_id, run_id, c.model_dump_json()) for c in checks]
        self.conn.executemany(
            "INSERT INTO checks (check_id, relation_id, run_id, payload) VALUES (?,?,?,?)",
            rows,
        )
        return len(rows)

    def add_result(self, r: CheckResult) -> None:
        self.conn.execute(
            "INSERT INTO results (check_id, run_id, status, stale, payload) VALUES (?,?,?,?,?)",
            (r.check_id, r.run_id, r.status.value, int(r.stale), r.model_dump_json()),
        )

    def record_decision(
        self, relation_id: str, actor: str, decided_at: str, state: ReviewState, reason: str
    ) -> None:
        """Review decisions are appended, never overwritten (F06)."""
        self.conn.execute(
            "INSERT INTO decisions (relation_id, actor, decided_at, state, reason)"
            " VALUES (?,?,?,?,?)",
            (relation_id, actor, decided_at, state.value, reason),
        )

    def mark_results_stale(
        self, check_ids: Iterable[str], reason: str = "", *, run_id: str | None = None
    ) -> int:
        """Flag prior verdicts stale. Restricted to one run when ``run_id`` is given."""
        ids = list(check_ids)
        if not ids:
            return 0
        qs = ",".join("?" * len(ids))
        sql = (f"UPDATE results SET stale = 1, stale_reason = ?"
               f" WHERE check_id IN ({qs}) AND stale = 0")
        params: list[object] = [reason, *ids]
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        return self.conn.execute(sql, params).rowcount

    # -- reads ----------------------------------------------------------

    def snapshot(self, version_hash: str) -> SourceSnapshot | None:
        row = self.conn.execute(
            "SELECT payload FROM snapshots WHERE version_hash = ?", (version_hash,)
        ).fetchone()
        return SourceSnapshot.model_validate_json(row["payload"]) if row else None

    def nodes(self, versions: Iterable[str] | None = None) -> Iterator[Node]:
        if versions is None:
            rows = self.conn.execute("SELECT payload FROM nodes")
        else:
            vs = list(versions)
            if not vs:
                return
            qs = ",".join("?" * len(vs))
            rows = self.conn.execute(
                f"SELECT payload FROM nodes WHERE source_version IN ({qs})", vs
            )
        for row in rows:
            yield Node.model_validate_json(row["payload"])

    def node(self, node_id: str, source_version: str) -> Node | None:
        row = self.conn.execute(
            "SELECT payload FROM nodes WHERE node_id = ? AND source_version = ?",
            (node_id, source_version),
        ).fetchone()
        return Node.model_validate_json(row["payload"]) if row else None

    def relations(self, run_id: str | None = None) -> Iterator[Relation]:
        """Relations as asserted by one run (default: the latest run that wrote any)."""
        if run_id is None:
            row = self.conn.execute(
                "SELECT run_id FROM relations ORDER BY row_id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return
            run_id = row["run_id"]
        for row in self.conn.execute(
            "SELECT payload FROM relations WHERE run_id = ? ORDER BY row_id", (run_id,)
        ):
            yield Relation.model_validate_json(row["payload"])

    def checks(self, run_id: str | None = None) -> Iterator[CheckDefinition]:
        sql, params = "SELECT payload FROM checks", ()
        if run_id is not None:
            sql, params = sql + " WHERE run_id = ?", (run_id,)
        for row in self.conn.execute(sql + " ORDER BY row_id", params):
            yield CheckDefinition.model_validate_json(row["payload"])

    def results(
        self, include_stale: bool = True, run_id: str | None = None
    ) -> Iterator[CheckResult]:
        """Results carry their current stale flag, which may postdate the run."""
        clauses, params = [], []
        if not include_stale:
            clauses.append("stale = 0")
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        sql = "SELECT payload, stale FROM results"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        for row in self.conn.execute(sql + " ORDER BY row_id", params):
            res = CheckResult.model_validate_json(row["payload"])
            yield res.model_copy(update={"stale": bool(row["stale"])})

    def export(self, run_id: str | None = None) -> dict[str, object]:
        """Machine-readable export of the graph (F10), whole or as of one run."""
        versions = self.current_versions(run_id) if run_id else None
        return {
            "run_id": run_id,
            "snapshots": [
                json.loads(r["payload"])
                for r in self.conn.execute("SELECT payload FROM snapshots")
                if versions is None or json.loads(r["payload"])["version_hash"] in versions
            ],
            "nodes": [n.model_dump(mode="json") for n in self.nodes(versions)],
            "relations": [r.model_dump(mode="json") for r in self.relations(run_id)],
            "checks": [c.model_dump(mode="json") for c in self.checks(run_id)],
            "results": [r.model_dump(mode="json") for r in self.results(run_id=run_id)],
        }
