"""The affected closure of a revision (F09).

A change reaches further than the nodes whose text changed. A new operand
changes a total; a changed total changes the ratio built on it; an amended
definition changes what every dependent number *means* while leaving each of
those numbers' own text untouched (T06). The closure follows those edges and
sorts every carried relation into what the revision costs:

* ``KEEP``       - nothing it touches was affected;
* ``RECHECK``    - a value it depends on changed: re-run, keep the review;
* ``REREVIEW``   - its meaning may have changed: a person must look again;
* ``UNRESOLVED`` - an endpoint could not be re-anchored: it cannot run at all.

Only the first two may execute. Propagation is deliberately conservative: an
over-wide closure costs a re-run or a review, a narrow one costs a silent pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ddg.models import (
    Endpoint,
    ExtractionMethod,
    Relation,
    RelationType,
    ReviewState,
)
from ddg.revision.remap import ChangeStatus, NodeChange

#: Value-flow direction: (input roles, output role) per relation type.
DERIVATION: dict[RelationType, tuple[tuple[str, ...], str]] = {
    RelationType.SUM_OF: (("operand",), "total"),
    RelationType.RATIO_OF: (("numerator", "denominator"), "result"),
    RelationType.VALUE_FROM: (("source",), "reported"),
    RelationType.DEPENDS_ON: (("definition",), "dependent"),
}


class Disposition(str, Enum):
    KEEP = "KEEP"
    RECHECK = "RECHECK"
    REREVIEW = "REREVIEW"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class RelationOutcome:
    old: Relation
    disposition: Disposition
    reasons: list[str] = field(default_factory=list)
    #: The relation as carried onto the new version (None for explicit
    #: relations, which are re-extracted from the new workbook instead).
    carried: Relation | None = None


@dataclass
class Closure:
    outcomes: list[RelationOutcome]
    #: new node id -> ("value" | "context", why)
    affected: dict[str, tuple[str, str]]
    lost: dict[str, str]

    def by_disposition(self, d: Disposition) -> list[RelationOutcome]:
        return [o for o in self.outcomes if o.disposition is d]


def _flow(rel: Relation) -> tuple[list[str], list[str]]:
    """(inputs, outputs) of a relation. A DEPENDS_ON without roles is symmetric."""
    if rel.type not in DERIVATION:
        return [], []
    in_roles, out_role = DERIVATION[rel.type]
    ins = [n for r in in_roles for n in rel.role(r)]
    outs = rel.role(out_role)
    if rel.type is RelationType.DEPENDS_ON and not (ins and outs):
        everyone = [e.node_id for e in rel.endpoints]
        return everyone, everyone
    return ins, outs


def compute_closure(
    changes: list[NodeChange],
    old_relations: list[Relation],
    new_explicit: list[Relation],
    new_versions: dict[str, str],
) -> Closure:
    """``new_versions`` maps each new node id to its source version."""
    old_to_new: dict[str, str] = {}
    lost: dict[str, str] = {}
    affected: dict[str, tuple[str, str]] = {}
    added: set[str] = set()

    for c in changes:
        if c.status is ChangeStatus.ADDED and c.new is not None:
            added.add(c.new.node_id)
            affected[c.new.node_id] = ("value", f"{c.new.node_id} is new in this version")
        elif c.mapped and c.old is not None and c.new is not None:
            old_to_new[c.old.node_id] = c.new.node_id
            if c.context_suspect:
                affected[c.new.node_id] = ("context", f"{c.old.node_id}: {c.reason}")
            elif c.status is ChangeStatus.CHANGED:
                affected[c.new.node_id] = ("value", f"{c.old.node_id}: {c.reason}")
        elif c.old is not None:
            lost[c.old.node_id] = f"{c.status.value}: {c.reason}"

    def translate(rel: Relation) -> Relation:
        eps = tuple(Endpoint(role=e.role, node_id=old_to_new.get(e.node_id, e.node_id))
                    for e in rel.endpoints)
        return rel.model_copy(update={"endpoints": eps})

    graph = [translate(r) for r in old_relations] + list(new_explicit)

    # Fixpoint over value flow. Lost inputs count as affected inputs.
    changed = True
    while changed:
        changed = False
        for rel in graph:
            ins, outs = _flow(rel)
            hit = [i for i in ins if i in affected or i in lost]
            if not hit:
                continue
            src = hit[0]
            kind = "context" if (rel.type is RelationType.DEPENDS_ON
                                 or affected.get(src, ("value",))[0] == "context") else "value"
            for o in outs:
                if o == src or o in lost:
                    continue
                prior = affected.get(o)
                if prior is None or (prior[0] == "value" and kind == "context"):
                    affected[o] = (kind, f"{rel.relation_id} ({rel.type.value}) from {src}")
                    changed = True

    grown = [r for r in new_explicit if any(e.node_id in added for e in r.endpoints)]

    outcomes: list[RelationOutcome] = []
    for old in old_relations:
        rel = translate(old)
        ids = [e.node_id for e in rel.endpoints]
        reasons: list[str] = []

        missing = [i for i in ids if i in lost]
        if missing:
            reasons += [f"{i} {lost[i]}" for i in missing]
            disposition = Disposition.UNRESOLVED
        else:
            ctx = [i for i in ids if affected.get(i, ("",))[0] == "context"]
            val = [i for i in ids if affected.get(i, ("",))[0] == "value"]
            # A reviewed relation that shares endpoints with a formula which has
            # just gained operands may now describe only part of that scope.
            mirrored = [] if old.extraction_method is ExtractionMethod.EXPLICIT_FORMULA else [
                g for g in grown if set(ids) & ({e.node_id for e in g.endpoints} - added)
            ]
            reasons += [f"meaning of {i} may have changed - {affected[i][1]}" for i in ctx]
            reasons += [
                f"{g.relation_id} now also includes "
                f"{', '.join(e.node_id for e in g.endpoints if e.node_id in added)}; "
                f"this relation may no longer cover the same scope"
                for g in mirrored
            ]
            reasons += [f"{i} value affected - {affected[i][1]}" for i in val]
            if ctx or mirrored:
                disposition = Disposition.REREVIEW
            elif val:
                disposition = Disposition.RECHECK
            else:
                disposition = Disposition.KEEP

        carried = None
        if old.extraction_method is not ExtractionMethod.EXPLICIT_FORMULA:
            if disposition is Disposition.UNRESOLVED:
                carried = old.model_copy(update={"review_state": ReviewState.UNRESOLVED})
            else:
                state = old.review_state
                if disposition is Disposition.REREVIEW and state in (
                        ReviewState.ACCEPTED, ReviewState.AMENDED, ReviewState.PROPOSED):
                    state = ReviewState.STALE
                carried = rel.model_copy(update={
                    "review_state": state,
                    "valid_source_versions": tuple(sorted({new_versions[i] for i in ids})),
                })
        outcomes.append(RelationOutcome(old, disposition, reasons, carried))

    return Closure(outcomes, affected, lost)
