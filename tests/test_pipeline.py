"""End-to-end audit behaviour, including the T01 revision scenario."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddg.models import CheckStatus
from ddg.pipeline import audit, render
from ddg.store import Store
from tests.fixtures.make_fixtures import make_report, make_workbook

MANIFEST = {
    "contexts": {
        "report.docx#p3q0": {
            "entity": "ProjectCo", "metric": "CAPEX", "period": "FY26",
            "scenario": "base", "unit": "currency", "currency": "USD",
            "scale": "millions",
        },
        "report.docx#p4q0": {
            "entity": "ProjectCo", "metric": "revenue", "period": "FY26",
            "scenario": "base", "unit": "currency", "currency": "USD",
            "scale": "millions",
        },
        "report.docx#p5q0": {
            "entity": "ProjectCo", "metric": "contingency", "period": "FY27",
            "scenario": "base", "unit": "currency", "currency": "USD",
            "scale": "millions",
        },
        "model.xlsx#Model!B3": {"entity": "ProjectCo", "metric": "civil", "period": "FY26",
                                "scenario": "base", "unit": "currency", "currency": "USD",
                                "scale": "units"},
        "model.xlsx#Model!B4": {"entity": "ProjectCo", "metric": "equipment", "period": "FY26",
                                "scenario": "base", "unit": "currency", "currency": "USD",
                                "scale": "units"},
        "model.xlsx#Model!B5": {"entity": "ProjectCo", "metric": "contingency", "period": "FY26",
                                "scenario": "base", "unit": "currency", "currency": "USD",
                                "scale": "units"},
        "model.xlsx#Model!B8": {"entity": "ProjectCo", "metric": "revenue", "period": "FY26",
                                "scenario": "base", "unit": "currency", "currency": "USD",
                                "scale": "units"},
    },
    "relations": [
        {"relation_id": "rev:revenue", "type": "SAME_QUANTITY_AS",
         "endpoints": [{"role": "narrative", "node_id": "report.docx#p4q0"},
                       {"role": "workbook", "node_id": "model.xlsx#Model!B8"}],
         "reviewer": "test", "reason": "paraphrased revenue line", "review_minutes": 2.0},
        {"relation_id": "rev:capex", "type": "SUM_OF",
         "endpoints": [{"role": "total", "node_id": "report.docx#p3q0"},
                       {"role": "operand", "node_id": "model.xlsx#Model!B3"},
                       {"role": "operand", "node_id": "model.xlsx#Model!B4"},
                       {"role": "operand", "node_id": "model.xlsx#Model!B5"}],
         "reviewer": "test", "reason": "narrative total over workbook components",
         "review_minutes": 3.0},
        {"relation_id": "rev:hard-negative", "type": "SAME_QUANTITY_AS",
         "endpoints": [{"role": "a", "node_id": "report.docx#p5q0"},
                       {"role": "b", "node_id": "model.xlsx#Model!B8"}],
         "reviewer": "test", "reason": "same value, different metric and period",
         "review_minutes": 1.0},
    ],
}


def build_package(root: Path, capex_base: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    make_report(root / "report.docx", "12.4")
    make_workbook(root / "model.xlsx", capex_base)
    (root / "review.json").write_text(json.dumps(MANIFEST))
    return root


@pytest.fixture
def v1(tmp_path) -> Path:
    return build_package(tmp_path / "v1", 12_400_000)


@pytest.fixture
def v2(tmp_path) -> Path:
    """Revision: the authoritative workbook moves; the narrative does not."""
    return build_package(tmp_path / "v2", 13_600_000)


def statuses(pkg: Path) -> dict[str, CheckStatus]:
    with Store() as store:
        rep = audit(pkg, store)
        assert not rep.review_problems, rep.review_problems
        return {r.check_id.removeprefix("chk:"): r.status for r in rep.results}


def test_baseline_package_is_consistent(v1):
    s = statuses(v1)
    assert s["rev:revenue"] is CheckStatus.PASS
    assert s["rev:capex"] is CheckStatus.PASS


def test_t01_changed_workbook_against_unchanged_narrative_fails(v2):
    """The defect the graph exists to catch."""
    s = statuses(v2)
    assert s["rev:capex"] is CheckStatus.FAIL
    assert s["rev:revenue"] is CheckStatus.PASS, "unrelated checks must stay unaffected"


def test_t03_hard_negative_is_review_not_a_false_inconsistency(v1):
    assert statuses(v1)["rev:hard-negative"] is CheckStatus.NEEDS_REVIEW


def test_t08_uncomputable_formula_never_reports_clean(v1):
    with Store() as store:
        rep = audit(v1, store)
        assert any("INDIRECT" in u for u in rep.unsupported)
        assert any(r.status is CheckStatus.NOT_CHECKED for r in rep.results)


def test_manifest_is_not_treated_as_a_source_document(v1):
    with Store() as store:
        rep = audit(v1, store)
        assert not any("review.json" in u for u in rep.unsupported)
        assert len(rep.documents) == 2


def test_review_decisions_and_costs_are_recorded(v1):
    with Store() as store:
        rep = audit(v1, store)
        rows = store.conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
        assert rows == 3, "every review decision is recorded with actor and reason"
        assert rep.costs["human_minutes"] == 6.0
        assert rep.costs["seconds"] > 0


def test_export_round_trips_and_findings_render(v1):
    with Store() as store:
        rep = audit(v1, store)
        blob = store.export()
        assert blob["nodes"] and blob["relations"] and blob["results"]
        text = render(rep)
        assert "NOT_CHECKED" in text and "Findings:" in text
