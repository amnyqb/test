"""Carry nodes from one source version onto the next (F08).

Every prior node ends in exactly one state:

* ``UNCHANGED`` - re-anchored and its evidence is identical (it may have moved);
* ``CHANGED``   - re-anchored to an edited successor (a new value, a rewritten
  sentence, a shifted formula);
* ``UNRESOLVED`` / ``AMBIGUOUS`` - the evidence does not settle where it went.

New nodes nobody maps onto are ``ADDED``. There is no positional fallback: a
node that cannot be followed by its quote or its row/column labels is reported,
never re-attached to whatever now occupies its old coordinate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from ddg.anchors import resolve_by_context, resolve_quote
from ddg.models import Node, NodeKind

#: Context fields a parser reads straight from the text (scale word, currency).
PARSED_CONTEXT_FIELDS = ("scale", "currency", "unit")


class ChangeStatus(str, Enum):
    UNCHANGED = "UNCHANGED"
    CHANGED = "CHANGED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    ADDED = "ADDED"


@dataclass(frozen=True)
class NodeChange:
    status: ChangeStatus
    reason: str
    old: Node | None = None
    new: Node | None = None
    value_changed: bool = False
    #: The quantity survived but what it means may not have: its sentence was
    #: rewritten, or the text now states a different scale or currency.
    context_suspect: bool = False
    moved: bool = False

    @property
    def mapped(self) -> bool:
        return self.status in (ChangeStatus.UNCHANGED, ChangeStatus.CHANGED)


def _is_mention(n: Node) -> bool:
    """A quantity extracted from prose, as opposed to the paragraph holding it."""
    return n.kind is NodeKind.PARAGRAPH and n.raw_value is not None


def _compare(old: Node, new: Node, how: str) -> NodeChange:
    value_changed = (
        old.raw_value != new.raw_value
        or old.normalized_value != new.normalized_value
        or old.formula != new.formula
    )
    text_changed = old.evidence_text != new.evidence_text
    moved = (
        old.selector.cell_ref != new.selector.cell_ref
        or old.selector.paragraph_index != new.selector.paragraph_index
        or old.selector.page_number != new.selector.page_number
    )
    conflicts = [
        f for f in PARSED_CONTEXT_FIELDS
        if getattr(new.context, f) is not None
        and getattr(old.context, f) is not None
        and getattr(new.context, f) != getattr(old.context, f)
    ]
    context_suspect = bool(conflicts) or (_is_mention(old) and text_changed and not value_changed)

    notes = [how]
    if value_changed:
        before = old.formula or old.raw_value
        after = new.formula or new.raw_value
        notes.append(f"value {before!r} -> {after!r}")
    elif text_changed:
        notes.append("surrounding text edited")
    if conflicts:
        notes.append("text now states a different " + ", ".join(conflicts))
    if moved:
        notes.append(f"moved {old.selector.describe()} -> {new.selector.describe()}")

    status = ChangeStatus.CHANGED if (value_changed or text_changed or conflicts) else \
        ChangeStatus.UNCHANGED
    return NodeChange(status, "; ".join(notes), old, new, value_changed, context_suspect, moved)


# -- text documents (DOCX, PDF) -------------------------------------------

def _span_index(nodes: list[Node], text: str) -> dict[tuple[int, int, bool], Node]:
    """Which new node owns which span of the new flat text."""
    index: dict[tuple[int, int, bool], Node] = {}
    for n in nodes:
        s, e = n.selector.text_start, n.selector.text_end
        if (s is None or e is None) and n.selector.quote is not None:
            res = resolve_quote(text, n.selector.quote)
            if res.resolved and res.offset is not None:
                s, e = res.offset, res.offset + len(n.selector.quote.exact)
        if s is not None and e is not None:
            index[(s, e, _is_mention(n))] = n
    return index


def _map_text_node(old: Node, new_text: str, index: dict, new_nodes: list[Node]) -> NodeChange:
    quote = old.selector.quote
    if quote is None:
        return NodeChange(ChangeStatus.UNRESOLVED,
                          "no quote anchor; a coordinate alone is never followed", old)
    mention = _is_mention(old)

    res = resolve_quote(new_text, quote)
    if res.resolved and res.offset is not None:
        target = index.get((res.offset, res.offset + len(quote.exact), mention))
        if target is None:
            return NodeChange(ChangeStatus.UNRESOLVED,
                              "quote found but no node owns that text in the new version", old)
        return _compare(old, target, res.reason)

    if res.status.value == "ambiguous":
        owners = [index.get((c, c + len(quote.exact), mention)) for c in res.candidates]
        same_path = [o for o in owners
                     if o is not None and o.selector.structural_path == old.selector.structural_path]
        if len(same_path) == 1:
            return _compare(old, same_path[0], "identical quotes; tie broken by heading path")
        return NodeChange(ChangeStatus.AMBIGUOUS, res.reason, old)

    edited = resolve_by_context(new_text, quote)
    if edited.resolved:
        s, e = edited.candidates[0], edited.candidates[1]
        target = index.get((s, e, mention))
        if target is None:
            return NodeChange(
                ChangeStatus.UNRESOLVED,
                f"{edited.reason}, but {new_text[s:e]!r} is no longer a recognised "
                f"{'quantity' if mention else 'node'}", old)
        return _compare(old, target, edited.reason)
    status = ChangeStatus.AMBIGUOUS if edited.status.value == "ambiguous" else \
        ChangeStatus.UNRESOLVED
    return NodeChange(status, edited.reason, old)


# -- workbooks --------------------------------------------------------------

def _map_cell(old: Node, new_cells: list[Node]) -> NodeChange:
    path = old.selector.structural_path
    same_sheet = [n for n in new_cells if n.selector.sheet_name == old.selector.sheet_name]

    if len(path) > 1:  # anchored by row label and/or column header
        hits = [n for n in same_sheet if n.selector.structural_path == path]
        labels = " / ".join(path[1:])
        if len(hits) == 1:
            return _compare(old, hits[0], f"followed by {labels}")
        if hits:
            return NodeChange(ChangeStatus.AMBIGUOUS,
                              f"{len(hits)} cells now share {labels}", old)
        return NodeChange(ChangeStatus.UNRESOLVED, f"no cell is labelled {labels} any more", old)

    hits = [n for n in same_sheet
            if len(n.selector.structural_path) <= 1 and n.raw_value == old.raw_value]
    if len(hits) > 1:
        at_same = [n for n in hits if n.selector.cell_ref == old.selector.cell_ref]
        if len(at_same) == 1:
            return _compare(old, at_same[0], "identical text; address agrees")
        return NodeChange(ChangeStatus.AMBIGUOUS,
                          f"{len(hits)} unlabelled cells hold {old.raw_value!r}", old)
    if hits:
        return _compare(old, hits[0], "unique identical text")
    return NodeChange(ChangeStatus.UNRESOLVED,
                      "unlabelled cell whose content changed; nothing to follow it by", old)


# -- one document -----------------------------------------------------------

def map_document(
    old_nodes: list[Node], new_nodes: list[Node], new_text: str | None = None
) -> list[NodeChange]:
    """Map every node of one prior document version onto its successor."""
    changes: list[NodeChange] = []
    text_new = [n for n in new_nodes if n.kind is not NodeKind.SHEET_CELL]
    cells_new = [n for n in new_nodes if n.kind is NodeKind.SHEET_CELL]
    index = _span_index(text_new, new_text) if new_text is not None else {}

    for old in old_nodes:
        if old.kind is NodeKind.SHEET_CELL:
            changes.append(_map_cell(old, cells_new))
        elif old.kind is NodeKind.TABLE_CELL or new_text is None:
            hits = [n for n in text_new if n.kind is old.kind
                    and n.evidence_text == old.evidence_text
                    and n.selector.structural_path == old.selector.structural_path]
            changes.append(_compare(old, hits[0], "identical table cell") if len(hits) == 1
                           else NodeChange(ChangeStatus.UNRESOLVED if not hits
                                           else ChangeStatus.AMBIGUOUS,
                                           f"{len(hits)} matching table cells", old))
        else:
            changes.append(_map_text_node(old, new_text, index, text_new))

    # Injectivity: two prior nodes landing on one new node is a misattachment
    # waiting to happen, so neither is trusted.
    claims: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(changes):
        if c.mapped and c.new is not None:
            claims[c.new.node_id].append(i)
    for new_id, idxs in claims.items():
        if len(idxs) > 1:
            for i in idxs:
                changes[i] = NodeChange(
                    ChangeStatus.AMBIGUOUS,
                    f"{len(idxs)} prior nodes re-anchor onto {new_id}", changes[i].old)

    claimed = {c.new.node_id for c in changes if c.mapped and c.new is not None}
    changes.extend(
        NodeChange(ChangeStatus.ADDED, "new in this version", None, n)
        for n in new_nodes if n.node_id not in claimed
    )
    return changes
