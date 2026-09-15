"""DOCX parsing into anchored nodes."""

from __future__ import annotations

import re
from pathlib import Path

import docx

from ddg.anchors import make_quote
from ddg.models import LocationSelector, Node, NodeKind, QuantityContext, SourceSnapshot
from ddg.units import parse_decimal

_CURRENCY = r"(?<![A-Za-z])(?:US\$|USD|EUR|GBP|SAR|AED)(?![A-Za-z])|\$|£|€"

#: A number as written in prose, with the sign, currency, scale and percent
#: markers that govern it. A paragraph may state several quantities, so each
#: becomes its own node with its own anchor - a paragraph-level node cannot be
#: compared against a cell.
_QUANTITY = re.compile(
    rf"(?P<currency>{_CURRENCY})?\s*"
    r"(?P<open>\()?"
    r"(?P<sign>(?<![\w.,])[-−])?"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?P<close>\))?"
    r"(?:\s*(?P<scale>thousands?|millions?|billions?|trillions?|mln|mn|bn|tn|m|k)(?![A-Za-z]))?"
    r"(?:\s*(?P<percent>%|per\s?cent(?![A-Za-z])))?",
    re.I,
)

_SCALE_WORDS = {
    "thousand": "thousands", "thousands": "thousands", "k": "thousands",
    "million": "millions", "millions": "millions", "m": "millions", "mn": "millions",
    "mln": "millions",
    "billion": "billions", "billions": "billions", "bn": "billions",
    "trillion": "trillions", "trillions": "trillions", "tn": "trillions",
}

#: One-letter abbreviations count as a scale only beside a currency: "USD 12.4m"
#: is money, but "12.4m" on its own may just as well be metres. Left unscaled,
#: such a figure makes every check it enters abstain rather than guess.
_NEEDS_CURRENCY = {"m", "k"}

_CURRENCY_CODES = {"$": "USD", "US$": "USD", "£": "GBP", "€": "EUR"}

_HEADER_CURRENCY = re.compile(_CURRENCY, re.I)
_HEADER_SCALE = re.compile(
    r"(?P<thousands>'000s?|\b000s\b)"
    r"|\b(?P<word>thousands?|millions?|billions?|trillions?|mln|mn|bn|tn|m|k)\b"
    r"|(?P<percent>%)",
    re.I,
)


def _currency(raw: str | None) -> str | None:
    if not raw:
        return None
    return _CURRENCY_CODES.get(raw.upper(), raw.upper())


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
        rows = list(table.rows)
        header = [c.text.strip() for c in rows[0].cells] if rows else []
        for r_i, row in enumerate(rows):
            cells = list(row.cells)
            row_label = cells[0].text.strip() if cells else ""
            for c_i, cell in enumerate(cells):
                text = cell.text.strip()
                if not text:
                    continue
                # The first row and first column are read as headers. A value
                # cell is anchored by them, so it survives rows being inserted.
                col_label = header[c_i] if r_i > 0 and c_i < len(header) else ""
                own_row_label = row_label if c_i > 0 else ""
                labels = tuple(f"{k}={v}" for k, v in
                               (("row", own_row_label), ("col", col_label)) if v)
                value = parse_decimal(text)
                selector = LocationSelector(
                    document_id=snap.document_id,
                    kind=NodeKind.TABLE_CELL,
                    quote=make_quote(text, 0, len(text)),
                    structural_path=tuple(heading_path) + (f"table{t_i}",)
                    + (labels or (f"r{r_i}c{c_i}",)),
                )
                nodes.append(Node(
                    node_id=f"{snap.document_id}#t{t_i}r{r_i}c{c_i}",
                    source_version=snap.version_hash,
                    kind=NodeKind.TABLE_CELL,
                    selector=selector,
                    evidence_text=text,
                    raw_value=text,
                    normalized_value=value,
                    context=_header_context(col_label, own_row_label)
                    if value is not None else QuantityContext(),
                ))

    if d.inline_shapes:
        unsupported.append(f"{len(d.inline_shapes)} inline shape(s) not extracted")

    return snap.model_copy(update={"unsupported_objects": unsupported}), nodes


def _header_context(*labels: str) -> QuantityContext:
    """Currency and scale stated in a table's headers - and nothing else.

    A column headed "FY26" is a label here, not an inferred period. Headers that
    disagree (millions in one, thousands in the other) leave the field empty.
    """
    currencies: set[str] = set()
    scales: set[str] = set()
    percent = False
    for label in labels:
        if not label:
            continue
        found = _HEADER_CURRENCY.search(label)
        currency = _currency(found.group(0)) if found else None
        if currency:
            currencies.add(currency)
        for m in _HEADER_SCALE.finditer(label):
            if m.group("percent"):
                percent = True
            elif m.group("thousands"):
                scales.add("thousands")
            else:
                word = m.group("word").lower()
                if word in _NEEDS_CURRENCY and not currency:
                    continue
                scales.add(_SCALE_WORDS[word])
    if percent:
        return QuantityContext(scale="percent", unit="percent")
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    return QuantityContext(
        currency=currency,
        scale=next(iter(scales)) if len(scales) == 1 else None,
        unit="currency" if currency else None,
    )


def _quantity_mentions(
    snap: SourceSnapshot,
    flat: str,
    text: str,
    para_index: int,
    para_offset: int,
    heading_path: list[str],
) -> list[Node]:
    """Extract each number written in a paragraph as its own anchored node.

    Only what the sentence actually states is recorded: sign, currency, scale
    and percent markers are read from the text; entity, period and scenario are
    left None for a reviewer, because inferring them from prose is precisely
    the judgement that produces confident false alarms.
    """
    out: list[Node] = []
    k = 0
    for m in _QUANTITY.finditer(text):
        number_text = m.group("number")
        value = parse_decimal(number_text)
        if value is None:
            continue
        currency = _currency(m.group("currency"))
        scale_word = (m.group("scale") or "").lower()
        if scale_word in _NEEDS_CURRENCY and not currency:
            scale_word = ""
        percent = bool(m.group("percent"))

        # A bare integer is far more often a section number, a list item or a
        # year fragment ("section 9", "(3)", "FY26") than a reported quantity.
        # Requiring a currency, scale or percent marker, or a decimal/thousands
        # separator, keeps those out of the graph.
        qualified = bool(currency or scale_word or percent) or any(c in number_text for c in ".,")
        if not qualified or text[: m.start("number")].rstrip().upper().endswith("FY"):
            continue

        # Accounting brackets hug the number itself: "(3.1) million" is negative,
        # "(USD 12.4 million)" is an aside.
        if (m.group("open") and m.group("close")) or m.group("sign"):
            value = -value

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
                scale="percent" if percent else _SCALE_WORDS.get(scale_word),
                currency=None if percent else currency,
                unit="percent" if percent else ("currency" if currency else None),
            ),
        ))
        k += 1
    return out
