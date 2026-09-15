"""F06 after a revision: the re-review queue closes revise -> re-review -> re-check.

All inputs are synthetic fixtures.
"""

from __future__ import annotations

import shutil

import pytest
from pydantic import ValidationError

from ddg.cli import main
from ddg.models import CheckStatus, Endpoint, ReviewState
from ddg.pipeline import audit
from ddg.review.queue import Decision, ReviewError, Verdict, decide, pending
from ddg.revision.engine import revise
from ddg.revision.replay import replay
from ddg.store import Store
from tests.test_pipeline import MANIFEST
from tests.test_revision import package, relabel

CAPEX_TOTAL = "report.docx#p3q0"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "graph.sqlite")
    yield s
    s.close()


@pytest.fixture
def v1(tmp_path, store):
    return audit(package(tmp_path / "v1"), store)


@pytest.fixture
def amended_definition(tmp_path, store, v1):
    """T06: the definition changes, so the CAPEX review is withdrawn."""
    return revise(package(tmp_path / "v2", report={"amounts_in": "thousands"},
                          manifest=False), store)


@pytest.fixture
def renamed_label(tmp_path, store, v1):
    """T07: the revenue row label changes, so the revenue relation is stranded."""
    return revise(relabel(package(tmp_path / "v2", manifest=False), "A8", "Turnover"), store)


def waiting(store) -> dict[str, ReviewState]:
    return {i.relation.relation_id: i.relation.review_state for i in pending(store)}


def verdicts(report) -> dict[str, CheckStatus]:
    return {r.check_id.removeprefix("chk:"): r.status for r in report.results}


def run_count(store) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


# --- the queue -----------------------------------------------------------

def test_nothing_waits_after_a_clean_audit(store, v1):
    assert pending(store) == []


def test_queue_lists_withdrawn_reviews_and_the_questioned_meaning(store, amended_definition):
    assert waiting(store) == {"rev:capex": ReviewState.STALE,
                              "rev:capex-definition": ReviewState.STALE}
    capex = next(i for i in pending(store) if i.relation.relation_id == "rev:capex")
    assert CAPEX_TOTAL in capex.why
    assert all(w.node is not None and not w.note for w in capex.endpoints)
    total = next(w for w in capex.endpoints if w.endpoint.node_id == CAPEX_TOTAL)
    assert "report.docx#p9" in total.question


def test_stranded_endpoint_is_never_shown_as_the_new_content_at_its_old_address(
        store, renamed_label):
    """Model!B8 exists in the new version too - as a different row."""
    revenue = next(i for i in pending(store) if i.relation.relation_id == "rev:revenue")
    workbook = next(w for w in revenue.endpoints if w.endpoint.role == "workbook")
    assert "not on the current version" in workbook.note
    assert workbook.node is None or workbook.node.source_version in \
        revenue.relation.valid_source_versions


# --- T06: re-review after an amended definition ---------------------------

def test_t06_accepting_needs_the_questioned_meaning_answered(store, amended_definition):
    before = run_count(store)
    with pytest.raises(ReviewError, match=f"meaning of {CAPEX_TOTAL} was questioned"):
        decide(store, [Decision(relation_id="rev:capex", verdict=Verdict.ACCEPT,
                                reviewer="analyst", reason="looks fine")])
    assert run_count(store) == before, "a refused batch writes nothing"

    rep = decide(store, [Decision(relation_id="rev:capex", verdict=Verdict.ACCEPT,
                                  reviewer="analyst", minutes=2,
                                  reason="the sentence states millions explicitly",
                                  confirm_context=(CAPEX_TOTAL,))])
    assert verdicts(rep)["rev:capex"] is CheckStatus.PASS
    assert "rev:capex" not in waiting(store)
    decision = store.conn.execute(
        "SELECT actor, state FROM decisions WHERE relation_id = 'rev:capex'"
        " ORDER BY row_id DESC LIMIT 1").fetchone()
    assert (decision["actor"], decision["state"]) == ("analyst", "ACCEPTED")
    answer = store.conn.execute(
        "SELECT actor, state FROM context_reviews WHERE node_id = ?"
        " ORDER BY row_id DESC LIMIT 1", (CAPEX_TOTAL,)).fetchone()
    assert (answer["actor"], answer["state"]) == ("analyst", "confirmed")
    assert rep.costs["human_minutes"] == 2


def test_t06_amended_context_answers_the_question_and_changes_the_verdict(
        store, amended_definition):
    rep = decide(store, [Decision(
        relation_id="rev:capex", verdict=Verdict.AMEND, reviewer="analyst",
        reason="the definition now puts section 4 in thousands",
        contexts={CAPEX_TOTAL: {"scale": "thousands"}})])
    assert verdicts(rep)["rev:capex"] is CheckStatus.FAIL
    assert replay(store, rep.run_id).faithful


def test_an_open_question_survives_a_further_revision(tmp_path, store, amended_definition):
    revise(package(tmp_path / "v3", report={"amounts_in": "thousands", "new_section": True},
                   manifest=False), store)
    with pytest.raises(ReviewError, match="was questioned"):
        decide(store, [Decision(relation_id="rev:capex", verdict=Verdict.ACCEPT,
                                reviewer="analyst", reason="a new version, so surely fine")])


def test_amending_a_shared_node_withdraws_other_approvals_relying_on_it(
        store, amended_definition):
    """Both rev:revenue and rev:hard-negative rely on Model!B8."""
    rep = decide(store, [Decision(
        relation_id="rev:revenue", verdict=Verdict.AMEND, reviewer="analyst",
        reason="the workbook line is FY27",
        contexts={"model.xlsx#Model!B8": {"period": "FY27"}})])
    assert verdicts(rep)["rev:revenue"] is CheckStatus.NEEDS_REVIEW

    assert rep.withdrawn == ["rev:hard-negative"]
    assert "rev:hard-negative" not in verdicts(rep)
    assert dict(rep.refusals)["rev:hard-negative"].startswith("review state is STALE")
    assert waiting(store)["rev:hard-negative"] is ReviewState.STALE

    stale = dict(store.conn.execute(
        "SELECT check_id, stale FROM results WHERE run_id = ?",
        (amended_definition.run_id,)).fetchall())
    assert stale["chk:rev:revenue"] == 1 and stale["chk:rev:hard-negative"] == 1
    earlier = replay(store, amended_definition.run_id)
    assert earlier.faithful, "an amendment must not rewrite what an earlier run read"


# --- T07: re-review after evidence could not be followed -------------------

def test_t07_stranded_relation_cannot_be_accepted_blindly(store, renamed_label):
    before = run_count(store)
    with pytest.raises(ReviewError, match="UNRESOLVED"):
        decide(store, [Decision(relation_id="rev:revenue", verdict=Verdict.ACCEPT,
                                reviewer="analyst", reason="looks fine")])
    assert run_count(store) == before, "a refused batch writes nothing"


def test_t07_amending_with_current_endpoints_restores_the_check(store, renamed_label):
    rep = decide(store, [Decision(
        relation_id="rev:revenue", verdict=Verdict.AMEND, reviewer="analyst",
        reason="Turnover is the renamed revenue line",
        endpoints=(Endpoint(role="narrative", node_id="report.docx#p4q0"),
                   Endpoint(role="workbook", node_id="model.xlsx#Model!B8")),
        contexts={"model.xlsx#Model!B8": MANIFEST["contexts"]["model.xlsx#Model!B8"]})])
    assert verdicts(rep)["rev:revenue"] is CheckStatus.PASS
    assert dict(rep.applied)["rev:revenue"] is ReviewState.AMENDED
    assert "rev:revenue" not in waiting(store)
    assert waiting(store)["rev:hard-negative"] is ReviewState.UNRESOLVED


# --- rejection, validation, persistence ------------------------------------

def test_rejected_relation_is_recorded_and_never_executes(store, v1):
    rep = decide(store, [Decision(relation_id="rev:hard-negative", verdict=Verdict.REJECT,
                                  reviewer="analyst", reason="different metric and period")])
    assert "rev:hard-negative" not in verdicts(rep)
    assert dict(rep.refusals)["rev:hard-negative"].startswith("review state is REJECTED")


@pytest.mark.parametrize("decision, problem", [
    (dict(relation_id="rev:nope", verdict="accept"), "no such relation"),
    (dict(relation_id="rev:capex", verdict="amend",
          endpoints=[{"role": "total", "node_id": "report.docx#p99"},
                     {"role": "operand", "node_id": "model.xlsx#Model!B3"}]),
     "not a node of the current version"),
    (dict(relation_id="rev:capex", verdict="amend",
          contexts={CAPEX_TOTAL: {"colour": "red"}}), "unknown context field"),
    (dict(relation_id="rev:capex", verdict="accept",
          contexts={CAPEX_TOTAL: {"scale": "units"}}), "only be changed by amend"),
    (dict(relation_id="rev:hard-negative", verdict="accept",
          confirm_context=[CAPEX_TOTAL]), "not an endpoint"),
])
def test_one_invalid_decision_refuses_the_whole_batch(store, v1, decision, problem):
    before = run_count(store)
    valid = Decision(relation_id="rev:revenue", verdict=Verdict.REJECT,
                     reviewer="analyst", reason="valid on its own")
    with pytest.raises(ReviewError, match=problem):
        decide(store, [valid, Decision(reviewer="analyst", reason="because", **decision)])
    assert run_count(store) == before


def test_a_decision_needs_a_reviewer_and_a_reason():
    with pytest.raises(ValidationError):
        Decision(relation_id="rev:capex", verdict="accept", reviewer="analyst", reason="")


def test_decisions_carry_into_the_next_revision(tmp_path, store, amended_definition):
    decide(store, [
        Decision(relation_id="rev:capex", verdict=Verdict.ACCEPT, reviewer="analyst",
                 reason="the sentence states millions explicitly",
                 confirm_context=(CAPEX_TOTAL,)),
        Decision(relation_id="rev:hard-negative", verdict=Verdict.REJECT, reviewer="analyst",
                 reason="different metric and period"),
    ])
    v3 = tmp_path / "v3"
    v3.mkdir()
    for name in ("report.docx", "model.xlsx"):
        shutil.copy(tmp_path / "v2" / name, v3 / name)
    rep = revise(v3, store)

    states = {o.old.relation_id: o.carried.review_state
              for o in rep.closure.outcomes if o.carried is not None}
    assert states["rev:capex"] is ReviewState.ACCEPTED
    assert states["rev:hard-negative"] is ReviewState.REJECTED
    assert verdicts(rep)["rev:capex"] is CheckStatus.PASS
    assert not rep.closure_misses


def test_cli_review_list_then_decide(tmp_path, capsys):
    db = tmp_path / "cli.sqlite"
    assert main(["audit", str(package(tmp_path / "v1")), "--db", str(db)]) == 0
    v2 = package(tmp_path / "v2", report={"amounts_in": "thousands"}, manifest=False)
    assert main(["revise", str(v2), "--db", str(db)]) == 0
    capsys.readouterr()

    assert main(["review", "list", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "[STALE] rev:capex" in out and "meaning questioned" in out

    decide_capex = ["review", "decide", "rev:capex", "accept", "--reviewer", "analyst",
                    "--reason", "the sentence states millions", "--db", str(db)]
    assert main(decide_capex) == 2
    assert "was questioned" in capsys.readouterr().err
    assert main(decide_capex + ["--confirm-context", CAPEX_TOTAL]) == 0
    assert "[PASS] chk:rev:capex" in capsys.readouterr().out

    assert main(["review", "decide", "rev:capex", "amend", "--reviewer", "analyst",
                 "--reason", "section 4 is now in thousands",
                 "--context", f"{CAPEX_TOTAL}:scale=thousands", "--db", str(db)]) == 0
    assert "[FAIL] chk:rev:capex" in capsys.readouterr().out

    assert main(["review", "decide", "rev:revenue", "accept", "--reviewer", "analyst",
                 "--reason", "x", "--endpoint", "total=nowhere", "--db", str(db)]) == 2
    assert "nothing was written" in capsys.readouterr().err
