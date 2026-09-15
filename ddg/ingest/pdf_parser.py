"""Digital-text PDF parsing into page-region nodes."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from ddg.anchors import make_quote
from ddg.models import LocationSelector, Node, NodeKind, SourceSnapshot


def parse_pdf(path: Path, snap: SourceSnapshot) -> tuple[SourceSnapshot, list[Node]]:
    nodes: list[Node] = []
    unsupported: list[str] = []

    with pdfplumber.open(str(path)) as pdf:
        for p_i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                unsupported.append(
                    f"page {p_i} yielded no digital text; scanned pages are out of scope"
                )
                continue
            offset = 0
            for line in text.split("\n"):
                if line.strip():
                    words = [w for w in page.extract_words() if w["text"] in line][:1]
                    bbox = (
                        (words[0]["x0"], words[0]["top"], words[0]["x1"], words[0]["bottom"])
                        if words else None
                    )
                    nodes.append(Node(
                        node_id=f"{snap.document_id}#pg{p_i}o{offset}",
                        source_version=snap.version_hash,
                        kind=NodeKind.PDF_REGION,
                        selector=LocationSelector(
                            document_id=snap.document_id,
                            kind=NodeKind.PDF_REGION,
                            quote=make_quote(text, offset, offset + len(line)),
                            page_number=p_i,
                            bbox=bbox,
                        ),
                        evidence_text=line,
                    ))
                offset += len(line) + 1

    return snap.model_copy(update={"unsupported_objects": unsupported}), nodes
