"""DOCX parsing into anchored nodes."""

from __future__ import annotations

import re
from pathlib import Path

import docx

from ddg.anchors import make_quote
from ddg.models import LocationSelector, Node, NodeKind, QuantityContext, SourceSnapshot
from ddg.units import parse_decimal

#: A number as written in prose, with the scale word that governs it. A
#: paragraph may state several quantities, so each becomes its own node with
#: its own anchor - a paragraph-level node cannot be compared against a cell.
_QUANTITY = re.compile(
    r"(?P<currency>USD|EUR|GBP|\$|£|€)?\s*"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?P<scale>million|billion|thousand)s?)?",
    re.I,
)

_SCALE_WORDS = {"thousand": "thousands", "million": "millions", "billion": "billions"}
_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR"}


def document_text(path: Path) -> str:
    """The flat text a quote anchor resolves against."""
    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def parse_docx(path: Path, snap: SourceSnapshot) -> tuple[SourceSnapshot, list[Node]]:
    d = docx.Document(str(path))
    paragraphs = list(d.paragraphs)
    flat = "\n".join(p.text for p in paragraphs)

    nodes: list[Node] = []
    unsupported: list[str] = []
    heading_path: list[str] = []
    offset = 0

    for idx, para in enumerate(paragraphs):
        text = para.text
        style = (para.style.name or "") if para.style is not None else ""
        if text.strip():
            if style.startswith("Heading"):
                level = style.replace("Heading", "").strip()
                depth = int(level) if level.isdigit() else 1
                heading_path = heading_path[: depth - 1] + [text.strip()]
                kind = NodeKind.HEADING
            else:
                kind = NodeKind.PARAGRAPH

            selector = LocationSelector(
                document_id=snap.document_id,
                kind=kind,
                quote=make_quote(flat, offset, offset + len(text)),
                paragraph_index=idx,
                text_start=offset,
                text_end=offset + len(text),
                structural_path=tuple(heading_path),
            )
            nodes.append(Node(
                node_id=f"{snap.document_id}#p{idx}",
                source_version=snap.version_hash,
                kind=kind,
                selector=selector,
                evidence_text=text,
                raw_value=None,
                context=QuantityContext(),
            ))
            nodes.extend(_quantity_mentions(snap, flat, text, idx, offset, heading_path))
        offset += len(text) + 1

    for t_i, table in enumerate(d.tables):
        for r_i, row in enumerate(table.rows):
            for c_i, cell in enumerate(row.cells):
                text = cell.text.strip()
                if not text:
                    continue
                selector = LocationSelector(
                    document_id=snap.document_id,
                    kind=NodeKind.TABLE_CELL,
                    quote=make_quote(text, 0, len(text)),
                    structural_path=tuple(heading_path) + (f"table{t_i}", f"r{r_i}c{c_i}"),
                )
                nodes.append(Node(
                    node_id=f"{snap.document_id}#t{t_i}r{r_i}c{c_i}",
                    source_version=snap.version_hash,
                    kind=NodeKind.TABLE_CELL,
                    selector=selector,
                    evidence_text=text,
                    raw_value=text,
                    normalized_value=parse_decimal(text),
                ))

    if d.inline_shapes:
        unsupported.append(f"{len(d.inline_shapes)} inline shape(s) not extracted")

    return snap.model_copy(update={"unsupported_objects": unsupported}), nodes


def _quantity_mentions(
    snap: SourceSnapshot,
    flat: str,
    text: str,
    para_index: int,
    para_offset: int,
    heading_path: list[str],
) -> list[Node]:
    """Extract each number written in a paragraph as its own anchored node.

    Only what the sentence actually states is recorded. The scale word and
    currency symbol are read from the text; entity, period and scenario are
    left None for a reviewer, because inferring them from prose is precisely
    the judgement that produces confident false alarms.
    """
    out: list[Node] = []
    k = 0
    for m in _QUANTITY.finditer(text):
        value = parse_decimal(m.group("number"))
        if value is None:
            continue
        raw_scale = (m.group("scale") or "").lower()
        raw_currency = (m.group("currency") or "").upper()

        # A bare integer is far more often a section number or a year fragment
        # ("section 9", "FY26") than a reported quantity. Requiring a currency
        # marker, a scale word or a decimal/thousands separator keeps those out
        # of the graph, where they would otherwise invite meaningless checks.
        number_text = m.group("number")
        qualified = bool(raw_currency or raw_scale) or any(c in number_text for c in ".,")
        if not qualified or text[: m.start("number")].rstrip().upper().endswith("FY"):
            continue

        start = para_offset + m.start("number")
        end = para_offset + m.end("number")

        out.append(Node(
            node_id=f"{snap.document_id}#p{para_index}q{k}",
            source_version=snap.version_hash,
            kind=NodeKind.PARAGRAPH,
            selector=LocationSelector(
                document_id=snap.document_id,
                kind=NodeKind.PARAGRAPH,
                quote=make_quote(flat, start, end),
                paragraph_index=para_index,
                text_start=start,
                text_end=end,
                structural_path=tuple(heading_path),
            ),
            evidence_text=text,
            raw_value=m.group(0).strip(),
            normalized_value=value,
            context=QuantityContext(
                scale=_SCALE_WORDS.get(raw_scale),
                currency=_CURRENCY_SYMBOLS.get(raw_currency, raw_currency or None),
                unit="currency" if raw_currency else None,
            ),
        ))
        k += 1
    return out
