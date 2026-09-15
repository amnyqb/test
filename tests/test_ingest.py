"""F01/F02: import without modification, and resolve every node to its source."""

from __future__ import annotations

from ddg.ingest import ingest_file
from ddg.ingest.snapshot import file_hash, verify
from ddg.models import NodeKind


def test_sources_are_not_modified(fixtures, blob_dir):
    src = fixtures["workbook_v1"]
    before = file_hash(src)
    snap, nodes = ingest_file(src, "pkg1", blob_dir)
    assert file_hash(src) == before, "ingest must never write to a source file"
    assert snap.version_hash == before
    assert verify(snap), "snapshot hash must re-verify against the blob"
    assert nodes


def test_every_cell_node_carries_an_address(fixtures, blob_dir):
    _, nodes = ingest_file(fixtures["workbook_v1"], "pkg1", blob_dir)
    cells = [n for n in nodes if n.kind is NodeKind.SHEET_CELL]
    assert cells
    for n in cells:
        assert n.selector.sheet_name and n.selector.cell_ref
        assert n.selector.describe() == f"{n.selector.sheet_name}!{n.selector.cell_ref}"


def test_unsupported_content_is_reported_not_dropped(fixtures, blob_dir):
    """T08: an unsupported function must surface, never yield a clean audit."""
    snap, _ = ingest_file(fixtures["workbook_v1"], "pkg1", blob_dir)
    assert any("INDIRECT" in u for u in snap.unsupported_objects)


def test_cached_formula_values_are_flagged(fixtures, blob_dir):
    """openpyxl does not evaluate formulas; a cached result is never 'fresh'."""
    _, nodes = ingest_file(fixtures["workbook_v1"], "pkg1", blob_dir)
    formula_nodes = [n for n in nodes if n.formula]
    assert formula_nodes
    assert all(n.is_cached_value for n in formula_nodes)


def test_docx_paragraphs_anchor_and_keep_heading_path(fixtures, blob_dir):
    _, nodes = ingest_file(fixtures["report_v1"], "pkg1", blob_dir)
    # The paragraph itself, not the quantity mention extracted from within it.
    capex = [n for n in nodes
             if "Base-case CAPEX" in n.evidence_text and n.normalized_value is None]
    assert len(capex) == 1
    node = capex[0]
    assert node.selector.quote is not None
    assert node.selector.paragraph_index is not None
    assert any("Capital expenditure" in p for p in node.selector.structural_path)


def test_narrative_quantities_become_their_own_anchored_nodes(fixtures, blob_dir):
    """A paragraph may state several quantities; each needs its own anchor."""
    _, nodes = ingest_file(fixtures["report_v1"], "pkg1", blob_dir)
    mentions = [n for n in nodes if n.normalized_value is not None]
    assert {str(n.normalized_value) for n in mentions} == {"12.4", "8.0"}
    for n in mentions:
        assert n.context.scale == "millions" and n.context.currency == "USD"
        assert n.selector.quote is not None


def test_section_numbers_and_year_fragments_are_not_quantities(fixtures, blob_dir):
    """'section 9' and the 26 inside 'FY26' must never enter the graph."""
    _, nodes = ingest_file(fixtures["report_v1"], "pkg1", blob_dir)
    values = {str(n.normalized_value) for n in nodes if n.normalized_value is not None}
    assert not ({"4", "5", "9", "26", "27"} & values)
