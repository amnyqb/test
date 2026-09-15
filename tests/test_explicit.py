"""F03: explicit workbook relations, extracted deterministically."""

from __future__ import annotations

from ddg.extract import extract_explicit_relations
from ddg.ingest import ingest_file
from ddg.models import ExtractionMethod, RelationType


def relations(fixtures, blob_dir, key="workbook_v1"):
    _, nodes = ingest_file(fixtures[key], "pkg1", blob_dir)
    return nodes, extract_explicit_relations(nodes)


def test_sum_formula_becomes_a_hyperedge(fixtures, blob_dir):
    _, rels = relations(fixtures, blob_dir)
    sums = [r for r in rels if r.type is RelationType.SUM_OF]
    assert len(sums) == 1
    r = sums[0]
    assert len(r.role("total")) == 1
    assert len(r.role("operand")) == 3, "every operand is preserved, not flattened to pairs"


def test_ratio_formula_names_numerator_and_denominator(fixtures, blob_dir):
    _, rels = relations(fixtures, blob_dir)
    ratios = [r for r in rels if r.type is RelationType.RATIO_OF]
    assert len(ratios) == 1
    assert ratios[0].role("numerator") and ratios[0].role("denominator")


def test_explicit_relations_record_their_provenance(fixtures, blob_dir):
    _, rels = relations(fixtures, blob_dir)
    assert rels
    for r in rels:
        assert r.extraction_method is ExtractionMethod.EXPLICIT_FORMULA
        assert r.note and r.note.startswith("from formula")
        assert r.valid_source_versions
