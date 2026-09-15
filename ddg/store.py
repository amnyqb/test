"""SQLite graph store.

Append-only by construction: snapshots, nodes, relations, checks and results
are written once and superseded rather than mutated, so an older audit can be
reconstructed from what was actually recorded (F11).
"""

from __future__ import annotations

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    version_hash TEXT PRIMARY KEY,
    package_id   TEXT NOT NULL,
    document_id  TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT PRIMARY KEY,
    source_version TEXT NOT NULL,
    payload        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    relation_id  TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    review_state TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    check_id    TEXT PRIMARY KEY,
    relation_id TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    row_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id TEXT NOT NULL,
    run_id   TEXT NOT NULL,
    status   TEXT NOT NULL,
    stale    INTEGER NOT NULL DEFAULT 0,
    payload  TEXT NOT NULL
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
"""


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.commit()
        self.close()

    # -- writes ---------------------------------------------------------

    def add_snapshot(self, s: SourceSnapshot) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
            (s.version_hash, s.package_id, s.document_id, s.model_dump_json()),
        )

    def add_nodes(self, nodes: Iterable[Node]) -> int:
        rows = [(n.node_id, n.source_version, n.model_dump_json()) for n in nodes]
        self.conn.executemany("INSERT OR REPLACE INTO nodes VALUES (?,?,?)", rows)
        return len(rows)

    def add_relations(self, rels: Iterable[Relation]) -> int:
        rows = [
            (r.relation_id, r.type.value, r.review_state.value, r.model_dump_json())
            for r in rels
        ]
        self.conn.executemany("INSERT OR REPLACE INTO relations VALUES (?,?,?,?)", rows)
        return len(rows)

    def add_checks(self, checks: Iterable[CheckDefinition]) -> int:
        rows = [(c.check_id, c.relation_id, c.model_dump_json()) for c in checks]
        self.conn.executemany("INSERT OR REPLACE INTO checks VALUES (?,?,?)", rows)
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

    def mark_results_stale(self, check_ids: Iterable[str]) -> int:
        ids = list(check_ids)
        if not ids:
            return 0
        qs = ",".join("?" * len(ids))
        cur = self.conn.execute(
            f"UPDATE results SET stale = 1 WHERE check_id IN ({qs}) AND stale = 0", ids
        )
        return cur.rowcount

    # -- reads ----------------------------------------------------------

    def nodes(self) -> Iterator[Node]:
        for row in self.conn.execute("SELECT payload FROM nodes"):
            yield Node.model_validate_json(row["payload"])

    def node(self, node_id: str) -> Node | None:
        row = self.conn.execute(
            "SELECT payload FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        return Node.model_validate_json(row["payload"]) if row else None

    def relations(self) -> Iterator[Relation]:
        for row in self.conn.execute("SELECT payload FROM relations"):
            yield Relation.model_validate_json(row["payload"])

    def relation(self, relation_id: str) -> Relation | None:
        row = self.conn.execute(
            "SELECT payload FROM relations WHERE relation_id = ?", (relation_id,)
        ).fetchone()
        return Relation.model_validate_json(row["payload"]) if row else None

    def checks(self) -> Iterator[CheckDefinition]:
        for row in self.conn.execute("SELECT payload FROM checks"):
            yield CheckDefinition.model_validate_json(row["payload"])

    def results(self, include_stale: bool = True) -> Iterator[CheckResult]:
        sql = "SELECT payload FROM results" + ("" if include_stale else " WHERE stale = 0")
        for row in self.conn.execute(sql):
            yield CheckResult.model_validate_json(row["payload"])

    def current_versions(self) -> set[str]:
        return {
            row["version_hash"]
            for row in self.conn.execute("SELECT version_hash FROM snapshots")
        }

    def export(self) -> dict[str, object]:
        """Machine-readable export of the whole graph (F10)."""
        return {
            "snapshots": [
                json.loads(r["payload"])
                for r in self.conn.execute("SELECT payload FROM snapshots")
            ],
            "nodes": [n.model_dump(mode="json") for n in self.nodes()],
            "relations": [r.model_dump(mode="json") for r in self.relations()],
            "checks": [c.model_dump(mode="json") for c in self.checks()],
            "results": [r.model_dump(mode="json") for r in self.results()],
        }
