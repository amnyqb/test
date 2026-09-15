"""M3 revision handling: F08 re-anchoring, F09 invalidation, F11 replay.

Scenario labels follow PROJECT_PLAN.md (section 5, section 8 and milestone M3):
T05 inserted rows and renumbered clauses, T06 an amended definition whose
dependents' text did not change, T07 evidence that cannot be followed. The
source brief's own wording of T05-T07 was not available when these were written.
All inputs are synthetic fixtures.
"""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from ddg.cli import main
from ddg.models import CheckStatus, ReviewState
from ddg.pipeline import audit
from ddg.revision.closure import Disposition
from ddg.revision.engine import RevisionReport, revise
from ddg.revision.remap import ChangeStatus, NodeChange
from ddg.revision.replay import replay
from ddg.store import SchemaError, Store
from tests.fixtures.make_fixtures import make_report, make_workbook
from tests.test_pipeline import MANIFEST

#: The CAPEX figure is only meaningful in the unit the definition paragraph sets.
DEFINITION = {
    "relation_id": "rev:capex-definition", "type": "DEPENDS_ON",
    "endpoints": [{"role": "dependent", "node_id": "report.docx#p3q0"},
                  {"role": "definition", "node_id": "report.docx#p9"}],
    "reviewer": "test", "reason": "CAPEX is stated in the defined unit",
    "review_minutes": 1.0,
}


def package(root: Path, *, report: dict | None = None, workbook: dict | None = None,
            manifest: bool = True) -> Path:
    root.mkdir(parents=True)
    make_report(root / "report.docx", **(report or {}))
    make_workbook(root / "model.xlsx", **(workbook or {}))
    if manifest:
        m = copy.deepcopy(MANIFEST)
        m["relations"].append(DEFINITION)
        (root / "review.json").write_text(json.dumps(m))
    return root


def relabel(pkg: Path, cell: str, text: str) -> Path:
    wb = openpyxl.load_workbook(pkg / "model.xlsx")
    wb["Model"][cell] = text
    wb.save(pkg / "model.xlsx")
    return pkg


def outcome(rep: RevisionReport, relation_id: str):
    return next(o for o in rep.closure.outcomes if o.old.relation_id == relation_id)


def change(rep: RevisionReport, old_node_id: str) -> NodeChange:
    return next(c for c in rep.changes if c.old and c.old.node_id == old_node_id)


def verdicts(rep) -> dict[str, CheckStatus]:
    return {r.check_id.removeprefix("chk:"): r.status for r in rep.results}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "graph.sqlite")
    yield s
    s.close()


@pytest.fixture
def v1(tmp_path, store):
    rep = audit(package(tmp_path / "v1"), store)
    assert not rep.review_problems, rep.review_problems
    return rep


@pytest.fixture
def t05(tmp_path, store, v1) -> RevisionReport:
    """A section inserted in the report and a row inserted in the workbook."""
    return revise(package(tmp_path / "v2", report={"new_section": True},
                          workbook={"design_fees": 900_000}, manifest=False), store)


# --- nothing changed ---------------------------------------------------

def test_identical_revision_keeps_every_decision_and_verdict(tmp_path, store, v1):
    v2 = tmp_path / "v2"
    v2.mkdir()
    for name in ("report.docx", "model.xlsx", "review.json"):
        shutil.copy(tmp_path / "v1" / name, v2 / name)
    rep = revise(v2, store)

    assert {c.status for c in rep.changes} == {ChangeStatus.UNCHANGED}
    assert {o.disposition for o in rep.closure.outcomes} == {Disposition.KEEP}
    assert rep.stale_marked == 0 and not rep.closure_misses
    assert verdicts(rep) == verdicts(v1), "reviewed context must survive an unchanged version"
    assert any("not applied" in n for n in rep.notices), \
        "a manifest copied forward must never be re-applied by coordinate"


# --- T05: moved rows, renumbered clauses --------------------------------

def test_t05_inserted_row_never_inherits_its_neighbours_identity(t05, store):
    followed = change(t05, "model.xlsx#Model!B5")
    assert followed.mapped and followed.new.node_id == "model.xlsx#Model!B6"

    # The trap: the old address now holds Equipment, with the very same value.
    ver = followed.new.source_version
    squatter = store.node("model.xlsx#Model!B5", ver)
    assert squatter.normalized_value == followed.old.normalized_value
    assert squatter.context.metric == "equipment"
    assert store.node("model.xlsx#Model!B6", ver).context.metric == "contingency"
    assert change(t05, "model.xlsx#Model!B4").new.node_id == "model.xlsx#Model!B5"


def test_t05_unaffected_relation_is_carried_to_moved_cells_and_still_passes(t05):
    revenue = outcome(t05, "rev:revenue")
    assert revenue.disposition is Disposition.KEEP
    assert [e.node_id for e in revenue.carried.endpoints] == \
        ["report.docx#p4q0", "model.xlsx#Model!B9"]
    assert verdicts(t05)["rev:revenue"] is CheckStatus.PASS
    assert not t05.closure_misses


def test_t05_renumbered_clause_follows_its_text_not_its_index(t05):
    clause = change(t05, "report.docx#p7")
    assert clause.status is ChangeStatus.CHANGED
    assert clause.new.node_id == "report.docx#p9"
    assert "section 10" in clause.new.evidence_text


def test_t05_formula_that_gains_an_operand_withdraws_the_review_mirroring_it(t05):
    capex = outcome(t05, "rev:capex")
    assert capex.disposition is Disposition.REREVIEW
    assert capex.carried.review_state is ReviewState.STALE
    assert [e.node_id for e in capex.carried.endpoints] == [
        "report.docx#p3q0", "model.xlsx#Model!B3", "model.xlsx#Model!B5", "model.xlsx#Model!B6"]
    assert any("Model!B4" in r for r in capex.reasons)
    assert "rev:capex" in dict(t05.refusals) and "rev:capex" not in verdicts(t05)


# --- T06: amended definition, unchanged dependent text ------------------

def test_t06_amended_definition_invalidates_a_dependent_whose_text_did_not_change(
        tmp_path, store, v1):
    rep = revise(package(tmp_path / "v2", report={"amounts_in": "thousands"},
                         manifest=False), store)
    assert change(rep, "report.docx#p3q0").status is ChangeStatus.UNCHANGED
    assert change(rep, "report.docx#p9").status is ChangeStatus.CHANGED

    capex = outcome(rep, "rev:capex")
    assert capex.disposition is Disposition.REREVIEW
    assert any("report.docx#p3q0" in r for r in capex.reasons)
    assert outcome(rep, "rev:revenue").disposition is Disposition.KEEP
    assert verdicts(rep)["rev:revenue"] is CheckStatus.PASS

    row = store.conn.execute(
        "SELECT stale, stale_reason FROM results WHERE check_id = 'chk:rev:capex'"
        " AND run_id = ?", (v1.run_id,)).fetchone()
    assert row["stale"] == 1 and "REREVIEW" in row["stale_reason"]


def test_changed_scale_word_beside_an_unchanged_number_needs_review(tmp_path, store, v1):
    rep = revise(package(tmp_path / "v2", report={"capex_scale": "billion"},
                         manifest=False), store)
    assert change(rep, "report.docx#p3q0").context_suspect
    assert outcome(rep, "rev:capex").disposition is Disposition.REREVIEW


# --- changed values keep their review and are re-checked ----------------

def test_changed_narrative_value_is_rechecked_and_the_inconsistency_found(
        tmp_path, store, v1):
    rep = revise(package(tmp_path / "v2", report={"capex_text": "13.6"},
                         manifest=False), store)
    capex = outcome(rep, "rev:capex")
    assert capex.disposition is Disposition.RECHECK
    assert capex.carried.review_state is ReviewState.ACCEPTED
    assert verdicts(rep)["rev:capex"] is CheckStatus.FAIL


# --- T07: evidence that cannot be followed ------------------------------

def test_t07_removed_label_leaves_the_relation_unresolved_not_reattached(
        tmp_path, store, v1):
    pkg = relabel(package(tmp_path / "v2", manifest=False), "A8", "Turnover")
    rep = revise(pkg, store)

    lost = change(rep, "model.xlsx#Model!B8")
    assert lost.status is ChangeStatus.UNRESOLVED and lost.new is None
    revenue = outcome(rep, "rev:revenue")
    assert revenue.disposition is Disposition.UNRESOLVED
    assert revenue.carried.review_state is ReviewState.UNRESOLVED
    assert "rev:revenue" in dict(rep.refusals) and "rev:revenue" not in verdicts(rep)


def test_t07_duplicated_label_is_ambiguous_not_a_coin_flip(tmp_path, store, v1):
    pkg = relabel(package(tmp_path / "v2", manifest=False), "A9", "Revenue FY26")
    rep = revise(pkg, store)
    assert change(rep, "model.xlsx#Model!B8").status is ChangeStatus.AMBIGUOUS
    assert outcome(rep, "rev:revenue").disposition is Disposition.UNRESOLVED


# --- stale before publish -----------------------------------------------

def test_invalidated_verdicts_are_committed_stale_before_any_new_verdict(
        tmp_path, store, v1, monkeypatch):
    pkg = package(tmp_path / "v2", report={"capex_text": "13.6"}, manifest=False)
    seen: list[int] = []
    write = store.add_result

    def spy(result):
        if not seen:  # observe through a separate connection: only committed state counts
            other = sqlite3.connect(store.path)
            seen.append(other.execute(
                "SELECT stale FROM results WHERE check_id = 'chk:rev:capex' AND run_id = ?",
                (v1.run_id,)).fetchone()[0])
            other.close()
        write(result)

    monkeypatch.setattr(store, "add_result", spy)
    revise(pkg, store)
    assert seen == [1]


# --- F11: replay --------------------------------------------------------

def test_replay_reproduces_the_original_audit_and_the_revision(t05, store, v1):
    for run_id in (v1.run_id, t05.run_id):
        result = replay(store, run_id)
        assert result.faithful, result.divergences
        assert result.reproduced > 0


def test_replay_reports_divergence_instead_of_rewriting_history(store, v1):
    version = store.document_versions(v1.run_id)["model.xlsx"]
    node = store.node("model.xlsx#Model!B8", version)
    store.add_nodes([node.model_copy(update={"normalized_value": Decimal("9000000")})])
    result = replay(store, v1.run_id)
    assert not result.faithful
    assert any("rev:revenue" in d for d in result.divergences)


# --- guards -------------------------------------------------------------

def test_revise_without_a_prior_audit_is_refused(tmp_path):
    with Store() as s, pytest.raises(ValueError, match="no published audit"):
        revise(package(tmp_path / "p", manifest=False), s)


def test_pre_revision_schema_is_refused_rather_than_misread(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE nodes (node_id TEXT PRIMARY KEY, source_version TEXT,"
                 " payload TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaError):
        Store(db)


def test_cli_audit_then_revise_then_replay(tmp_path, capsys):
    db = tmp_path / "cli.sqlite"
    v1 = package(tmp_path / "v1")
    v2 = package(tmp_path / "v2", workbook={"design_fees": 900_000}, manifest=False)
    assert main(["audit", str(v1), "--db", str(db)]) == 0
    assert main(["revise", str(v2), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "[REREVIEW] rev:capex" in out and "STALE before publishing" in out
    with Store(db) as s:
        run_id = s.latest_run()
    assert main(["replay", run_id, "--db", str(db)]) == 0
    assert "FAITHFUL" in capsys.readouterr().out
