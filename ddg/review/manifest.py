"""The reviewed layer of the graph.

Quantity context cannot be recovered from a cell's coordinate: whether B6 is
FY26 base-case CAPEX in USD base units is a judgement a person makes. Until the
semantic layer proposes these in Milestone 2, an analyst authors them here, and
either way a person accepts them before anything executes.

The manifest is data, never code. Nothing in it is evaluated.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ddg.models import (
    Endpoint,
    ExtractionMethod,
    Node,
    QuantityContext,
    Relation,
    RelationType,
    ReviewState,
)
from ddg.store import Store


class ReviewedRelation(BaseModel):
    model_config = ConfigDict(frozen=True)

    relation_id: str
    type: RelationType
    endpoints: tuple[Endpoint, ...]
    review_state: ReviewState = ReviewState.ACCEPTED
    reviewer: str = "unattributed"
    reason: str = ""
    rule_approved: bool = False
    review_minutes: float = 0.0


class ReviewManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    contexts: dict[str, QuantityContext] = {}
    relations: tuple[ReviewedRelation, ...] = ()


def load_manifest(path: Path) -> ReviewManifest:
    return ReviewManifest.model_validate_json(Path(path).read_text())


def apply_manifest(
    manifest: ReviewManifest,
    nodes: list[Node],
    store: Store | None = None,
) -> tuple[list[Node], list[Relation], list[str]]:
    """Attach reviewed context to nodes and build reviewed relations.

    Returns the updated nodes, the relations, and any problems found. A context
    or endpoint naming a node that does not exist is reported, never ignored.
    """
    by_id = {n.node_id: n for n in nodes}
    problems: list[str] = []

    updated: list[Node] = []
    for n in nodes:
        ctx = manifest.contexts.get(n.node_id)
        updated.append(n.model_copy(update={"context": ctx}) if ctx else n)
    by_id = {n.node_id: n for n in updated}

    for node_id in manifest.contexts:
        if node_id not in by_id:
            problems.append(f"context declared for unknown node {node_id}")

    relations: list[Relation] = []
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    for rr in manifest.relations:
        missing = [e.node_id for e in rr.endpoints if e.node_id not in by_id]
        if missing:
            problems.append(
                f"{rr.relation_id}: endpoint(s) not found: {', '.join(missing)}"
            )
            continue
        versions = tuple({by_id[e.node_id].source_version for e in rr.endpoints})
        relations.append(Relation(
            relation_id=rr.relation_id,
            type=rr.type,
            endpoints=rr.endpoints,
            extraction_method=ExtractionMethod.HUMAN_AUTHORED,
            review_state=rr.review_state,
            valid_source_versions=versions,
            rule_approved=rr.rule_approved,
            note=rr.reason or None,
        ))
        if store is not None:
            store.record_decision(rr.relation_id, rr.reviewer, now, rr.review_state, rr.reason)

    return updated, relations, problems
