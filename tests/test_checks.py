"""T01-T04 and F07: arithmetic, context, scale, and what may execute at all."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ddg import CHECKER_VERSION
from ddg.check import eligible, run_check
from ddg.models import (
    CheckDefinition, CheckStatus, Endpoint, ExtractionMethod, LocationSelector,
    Node, NodeKind, QuantityContext, Relation, RelationType, ReviewState,
)

VER = "v1"
FULL = dict(entity="ProjectCo", metric="capex", scenario="base", unit="currency", currency="USD")


def node(nid, value, *, scale=None, text="evidence", **ctx):
    return Node(
        node_id=nid, source_version=VER, kind=NodeKind.PARAGRAPH,
        selector=LocationSelector(document_id="d", kind=NodeKind.PARAGRAPH, paragraph_index=0),
        evidence_text=text,
        normalized_value=Decimal(str(value)) if value is not None else None,
        context=QuantityContext(scale=scale, **ctx),
    )


def rel(rtype, endpoints, *, state=ReviewState.ACCEPTED, approved=False, versions=(VER,)):
    return Relation(
        relation_id="r1", type=rtype,
        endpoints=tuple(Endpoint(role=r, node_id=n) for r, n in endpoints),
        extraction_method=ExtractionMethod.HUMAN_AUTHORED,
        review_state=state, rule_approved=approved, valid_source_versions=versions,
    )


def check(tol="0"):
    return CheckDefinition(
        check_id="c1", relation_id="r1", expression="equality",
        operand_node_ids=("a", "b"), tolerance=Decimal(tol),
        checker_version=CHECKER_VERSION,
    )


def run(rtype, endpoints, nodes, tol="0"):
    r = rel(rtype, endpoints)
    return run_check(check(tol), r, {n.node_id: n for n in nodes}, "run1")


# --- T04: scale --------------------------------------------------------

def test_millions_and_base_units_are_equal_after_scaling():
    a = node("a", "12.4", scale="millions", period="FY26", **FULL)
    b = node("b", "12400000", scale="units", period="FY26", **FULL)
    res = run(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")], [a, b])
    assert res.status is CheckStatus.PASS


def test_scale_is_applied_before_comparison_not_after():
    a = node("a", "12.4", scale="millions", period="FY26", **FULL)
    b = node("b", "12.4", scale="units", period="FY26", **FULL)
    res = run(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")], [a, b])
    assert res.status is CheckStatus.FAIL


# --- T01: identity survives differing values ---------------------------

def test_changed_authoritative_value_is_a_detected_inconsistency():
    narrative = node("a", "12.4", scale="millions", period="FY26", **FULL)
    workbook = node("b", "13.6", scale="millions", period="FY26", **FULL)
    res = run(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")], [narrative, workbook])
    assert res.status is CheckStatus.FAIL
    assert "not which source is correct" in res.detail


# --- T03: equal values, different context ------------------------------

def test_same_value_different_period_is_review_not_pass():
    a = node("a", "8.0", scale="millions", period="FY26", **FULL)
    b = node("b", "8.0", scale="millions", period="FY27", **FULL)
    res = run(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")], [a, b])
    assert res.status is CheckStatus.NEEDS_REVIEW
    assert "period differs" in res.detail


def test_missing_context_is_review_not_pass():
    a = node("a", "8.0", scale="millions")
    b = node("b", "8.0", scale="millions")
    res = run(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")], [a, b])
    assert res.status is CheckStatus.NEEDS_REVIEW
    assert "context incomplete" in res.detail


def test_missing_value_is_not_checked_never_pass():
    a = node("a", None, period="FY26", **FULL)
    b = node("b", "8.0", period="FY26", **FULL)
    res = run(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")], [a, b])
    assert res.status is CheckStatus.NOT_CHECKED


# --- sums and ratios ---------------------------------------------------

def test_sum_of_preserves_every_operand():
    total = node("t", "12400000", period="FY26", **FULL)
    ops = [node("o1", "6200000", period="FY26", **FULL),
           node("o2", "3100000", period="FY26", **FULL),
           node("o3", "3100000", period="FY26", **FULL)]
    res = run(RelationType.SUM_OF,
              [("total", "t"), ("operand", "o1"), ("operand", "o2"), ("operand", "o3")],
              [total, *ops])
    assert res.status is CheckStatus.PASS
    assert "o3=3100000" in " ".join(res.computation_trace)


def test_sum_mismatch_fails_with_the_difference_shown():
    total = node("t", "12400000", period="FY26", **FULL)
    ops = [node("o1", "6200000", period="FY26", **FULL),
           node("o2", "3100000", period="FY26", **FULL)]
    res = run(RelationType.SUM_OF, [("total", "t"), ("operand", "o1"), ("operand", "o2")],
              [total, *ops])
    assert res.status is CheckStatus.FAIL and "3100000" in res.detail


def test_zero_denominator_is_review_not_error_or_pass():
    r = node("r", "3.1", period="FY26", **FULL)
    n = node("n", "12400000", period="FY26", **FULL)
    d = node("d", "0", period="FY26", **FULL)
    res = run(RatioType := RelationType.RATIO_OF,
              [("result", "r"), ("numerator", "n"), ("denominator", "d")], [r, n, d])
    assert res.status is CheckStatus.NEEDS_REVIEW


def test_decimal_arithmetic_has_no_float_error():
    """0.1 + 0.2 must equal 0.3 exactly; a float path would fail this."""
    exact = dict(period="FY26", rounding_policy="exact", **FULL)  # no rounding allowance
    total = node("t", "0.3", **exact)
    ops = [node("o1", "0.1", **exact), node("o2", "0.2", **exact)]
    res = run(RelationType.SUM_OF, [("total", "t"), ("operand", "o1"), ("operand", "o2")],
              [total, *ops])
    assert res.status is CheckStatus.PASS


# --- F07: the eligibility gate ----------------------------------------

@pytest.mark.parametrize("state", [ReviewState.PROPOSED, ReviewState.REJECTED,
                                   ReviewState.STALE, ReviewState.UNRESOLVED])
def test_unaccepted_relations_never_execute(state):
    a, b = node("a", "1", period="FY26", **FULL), node("b", "1", period="FY26", **FULL)
    r = rel(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")], state=state)
    verdict = eligible(r, {"a": a, "b": b}, {VER})
    assert not verdict.ok and state.value in verdict.reason


def test_depends_on_is_never_executable():
    a, b = node("a", "1"), node("b", "1")
    r = rel(RelationType.DEPENDS_ON, [("a", "a"), ("b", "b")])
    assert not eligible(r, {"a": a, "b": b}, {VER}).ok


def test_clause_rule_requires_reviewer_approval():
    a, b = node("a", "1"), node("b", "1")
    r = rel(RelationType.REQUIRES, [("condition", "a"), ("consequence", "b")], approved=False)
    verdict = eligible(r, {"a": a, "b": b}, {VER})
    assert not verdict.ok and "approved by a reviewer" in verdict.reason
    ok = rel(RelationType.REQUIRES, [("condition", "a"), ("consequence", "b")], approved=True)
    assert eligible(ok, {"a": a, "b": b}, {VER}).ok


def test_superseded_source_version_blocks_execution():
    a, b = node("a", "1", period="FY26", **FULL), node("b", "1", period="FY26", **FULL)
    r = rel(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")])
    verdict = eligible(r, {"a": a, "b": b}, {"some-other-version"})
    assert not verdict.ok and "superseded" in verdict.reason


def test_missing_endpoint_blocks_execution():
    a = node("a", "1", period="FY26", **FULL)
    r = rel(RelationType.SAME_QUANTITY_AS, [("a", "a"), ("b", "b")])
    assert not eligible(r, {"a": a}, {VER}).ok
