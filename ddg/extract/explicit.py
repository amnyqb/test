"""Deterministic relation extraction from workbook formulas.

These relations need no model and no reviewer: the workbook states them. They
are the floor the semantic layer has to beat, and they are what Milestone 1
executes.
"""

from __future__ import annotations

import re

from openpyxl.utils import range_boundaries, get_column_letter

from ddg.models import (
    Endpoint,
    ExtractionMethod,
    Node,
    Relation,
    RelationType,
    ReviewState,
)

_SUM = re.compile(r"^=\s*SUM\(([^)]+)\)\s*$", re.I)
_RATIO = re.compile(r"^=\s*([A-Z]+\d+)\s*/\s*([A-Z]+\d+)\s*$", re.I)
_ADD = re.compile(r"^=\s*([A-Z]+\d+(?:\s*\+\s*[A-Z]+\d+)+)\s*$", re.I)
_SINGLE = re.compile(r"^=\s*([A-Z]+\d+)\s*$", re.I)
_CELL = re.compile(r"[A-Z]+\d+", re.I)


def _expand(ref: str, sheet: str) -> list[str]:
    """Expand ``B2:B4`` into individual A1 addresses."""
    ref = ref.strip()
    if ":" not in ref:
        return [f"{sheet}!{ref.upper()}"]
    min_c, min_r, max_c, max_r = range_boundaries(ref.upper())
    return [
        f"{sheet}!{get_column_letter(c)}{r}"
        for r in range(min_r, max_r + 1)
        for c in range(min_c, max_c + 1)
    ]


def extract_explicit_relations(nodes: list[Node]) -> list[Relation]:
    """Read SUM / ratio / addition / direct-reference formulas into relations."""
    by_address: dict[str, Node] = {}
    for n in nodes:
        if n.selector.cell_ref and n.selector.sheet_name:
            by_address[f"{n.selector.sheet_name}!{n.selector.cell_ref}"] = n

    relations: list[Relation] = []
    for node in nodes:
        if not node.formula or not node.selector.sheet_name:
            continue
        sheet = node.selector.sheet_name
        f = node.formula.replace(" ", "")
        rel_id = f"rel:{node.node_id}"

        def build(rtype: RelationType, endpoints: list[Endpoint]) -> Relation | None:
            # Only build a relation whose every endpoint actually exists.
            if any(e.node_id not in {n.node_id for n in nodes} for e in endpoints):
                return None
            return Relation(
                relation_id=rel_id,
                type=rtype,
                endpoints=tuple(endpoints),
                extraction_method=ExtractionMethod.EXPLICIT_FORMULA,
                review_state=ReviewState.ACCEPTED,  # the workbook asserts it
                valid_source_versions=(node.source_version,),
                note=f"from formula {node.formula}",
            )

        if m := _SUM.match(f):
            addrs = _expand(m.group(1), sheet)
            ops = [by_address[a] for a in addrs if a in by_address]
            if len(ops) >= 2:
                eps = [Endpoint(role="total", node_id=node.node_id)]
                eps += [Endpoint(role="operand", node_id=o.node_id) for o in ops]
                if r := build(RelationType.SUM_OF, eps):
                    relations.append(r)
            continue

        if m := _RATIO.match(f):
            num = by_address.get(f"{sheet}!{m.group(1).upper()}")
            den = by_address.get(f"{sheet}!{m.group(2).upper()}")
            if num and den:
                eps = [
                    Endpoint(role="result", node_id=node.node_id),
                    Endpoint(role="numerator", node_id=num.node_id),
                    Endpoint(role="denominator", node_id=den.node_id),
                ]
                if r := build(RelationType.RATIO_OF, eps):
                    relations.append(r)
            continue

        if _ADD.match(f):
            ops = [
                by_address[f"{sheet}!{a.upper()}"]
                for a in _CELL.findall(f)
                if f"{sheet}!{a.upper()}" in by_address
            ]
            if len(ops) >= 2:
                eps = [Endpoint(role="total", node_id=node.node_id)]
                eps += [Endpoint(role="operand", node_id=o.node_id) for o in ops]
                if r := build(RelationType.SUM_OF, eps):
                    relations.append(r)
            continue

        if m := _SINGLE.match(f):
            src = by_address.get(f"{sheet}!{m.group(1).upper()}")
            if src:
                eps = [
                    Endpoint(role="reported", node_id=node.node_id),
                    Endpoint(role="source", node_id=src.node_id),
                ]
                if r := build(RelationType.VALUE_FROM, eps):
                    relations.append(r)

    return relations
