"""The single gate in front of every deterministic checker (F07).

Nothing executes unless it is accepted, grounded, current and - for clause
rules - separately approved by a reviewer. Every refusal carries its reason, so
a check that did not run is visible rather than absent.
"""

from __future__ import annotations

from dataclasses import dataclass

from ddg.models import (
    EXECUTABLE_RELATIONS,
    REVIEW_BEFORE_EXECUTION,
    Node,
    Relation,
    ReviewState,
)


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    reason: str


def eligible(
    relation: Relation,
    nodes: dict[str, Node],
    current_versions: set[str],
) -> Eligibility:
    if relation.type not in EXECUTABLE_RELATIONS:
        return Eligibility(False, f"{relation.type.value} is not an executable relation")

    if relation.review_state is not ReviewState.ACCEPTED:
        return Eligibility(False, f"review state is {relation.review_state.value}, not ACCEPTED")

    if relation.type in REVIEW_BEFORE_EXECUTION and not relation.rule_approved:
        return Eligibility(
            False, "clause rule has not been approved by a reviewer; formalisation unverified"
        )

    for ep in relation.endpoints:
        node = nodes.get(ep.node_id)
        if node is None:
            return Eligibility(False, f"endpoint {ep.node_id} does not resolve to a node")
        if node.source_version not in current_versions:
            return Eligibility(
                False, f"endpoint {ep.node_id} belongs to superseded version "
                       f"{node.source_version[:12]}"
            )

    stale_versions = [
        v for v in relation.valid_source_versions if v not in current_versions
    ]
    if stale_versions:
        return Eligibility(
            False, f"relation was asserted against superseded version "
                   f"{stale_versions[0][:12]}"
        )

    return Eligibility(True, "accepted, grounded and version-current")
