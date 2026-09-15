"""Deterministic numerical checkers.

All arithmetic is Decimal. No float touches the check path, because a display
rounding artefact reported as an inconsistency costs an analyst the same time
as a real defect and costs the tool its credibility faster.

Every check runs the same stages, in this order, and stops at the first that
fails:

1. every value is present - otherwise NOT_CHECKED;
2. the contexts allow the comparison: the same quantity for a pair, no
   contradiction among the participants of a sum or ratio - otherwise
   NEEDS_REVIEW;
3. the scales can be reconciled; a missing scale is never assumed to be units -
   otherwise NEEDS_REVIEW;
4. every value's rounding is known: a declared policy, or the precision the
   figure was written to, stated in the trace - otherwise NEEDS_REVIEW;
5. only then are the values compared.

None of the early exits ever becomes PASS.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

from ddg import CHECKER_VERSION
from ddg.models import (
    CheckDefinition,
    CheckResult,
    CheckStatus,
    Node,
    Relation,
    RelationType,
)
from ddg.units import (
    DIMENSION_FIELDS,
    SCALES,
    SHARED_FIELDS,
    compatible,
    conflicts,
    to_base_units,
)


class _Stop(Exception):
    """Ends a check early with a verdict that is never PASS."""

    def __init__(self, status: CheckStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def _hash_inputs(nodes: list[Node]) -> tuple[str, ...]:
    return tuple(
        hashlib.sha256(
            f"{n.node_id}|{n.source_version}|{n.normalized_value}".encode()
        ).hexdigest()[:16]
        for n in nodes
    )


def _result(
    check: CheckDefinition, run_id: str, status: CheckStatus, detail: str,
    trace: list[str], nodes: list[Node],
) -> CheckResult:
    return CheckResult(
        check_id=check.check_id, run_id=run_id, status=status, detail=detail,
        input_hashes=_hash_inputs(nodes), computation_trace=tuple(trace),
    )


def run_check(
    check: CheckDefinition, relation: Relation, nodes: dict[str, Node], run_id: str
) -> CheckResult:
    """Execute one approved relation. The caller must have cleared the gate."""
    involved = [nodes[e.node_id] for e in relation.endpoints if e.node_id in nodes]
    trace: list[str] = [f"checker={CHECKER_VERSION}", f"relation={relation.type.value}"]

    for n in involved:
        if n.is_cached_value:
            trace.append(
                f"{n.node_id} uses Excel's cached result, not a fresh recalculation"
            )

    try:
        if relation.type in (RelationType.SAME_QUANTITY_AS, RelationType.VALUE_FROM):
            return _compare_pair(check, relation, nodes, run_id, trace, involved)
        if relation.type is RelationType.SUM_OF:
            return _check_sum(check, relation, nodes, run_id, trace, involved)
        if relation.type is RelationType.RATIO_OF:
            return _check_ratio(check, relation, nodes, run_id, trace, involved)
        return _result(check, run_id, CheckStatus.NOT_CHECKED,
                       f"no numerical checker for {relation.type.value}", trace, involved)
    except _Stop as stop:
        return _result(check, run_id, stop.status, str(stop), trace, involved)
    except Exception as exc:  # a checker fault is ERROR, never a pass
        return _result(check, run_id, CheckStatus.ERROR,
                       f"{type(exc).__name__}: {exc}", trace, involved)


# -- shared stages ------------------------------------------------------------

def _values_present(nodes: list[Node]) -> None:
    missing = [n.node_id for n in nodes if n.normalized_value is None]
    if missing:
        raise _Stop(CheckStatus.NOT_CHECKED, f"no normalised value for {', '.join(missing)}")


def _scales_reconcilable(nodes: list[Node], trace: list[str]) -> None:
    """A missing scale is never assumed to be units.

    Values with no declared scale are compared as written only when none of the
    participants declares one and they all come from the same document - the
    case of cells inside one workbook formula. Anything else is a review item.
    """
    unknown = [n.node_id for n in nodes
               if n.context.scale is not None and n.context.scale.strip().lower() not in SCALES]
    if unknown:
        raise _Stop(CheckStatus.NEEDS_REVIEW,
                    f"not comparable - unknown scale on {', '.join(unknown)}")
    undeclared = [n.node_id for n in nodes if n.context.scale is None]
    if undeclared and len(undeclared) < len(nodes):
        raise _Stop(CheckStatus.NEEDS_REVIEW,
                    f"not comparable - scale is declared for some values but not for "
                    f"{', '.join(undeclared)}; a missing scale is never assumed")
    if undeclared and len({n.selector.document_id for n in nodes}) > 1:
        raise _Stop(CheckStatus.NEEDS_REVIEW,
                    "not comparable - no scale is declared and the values come from "
                    "different documents")
    trace.append("scale: all declared" if not undeclared
                 else "scale: none declared; compared as written within one document")


def _half_step(node: Node) -> tuple[Decimal, str]:
    """Half the rounding step behind a value, in base units, and how it was decided.

    A declared ``rounding_policy`` wins: ``exact``, ``displayed``, or
    ``nearest:<step>`` with the step in the value's own scale. Without one, the
    precision the figure was written to is used - "12.4 million" stands for
    anything from 12.35 to 12.45 million - and the trace says so, because that is
    a stated rule rather than something the document declared.
    """
    assert node.normalized_value is not None
    declared = (node.context.rounding_policy or "").strip()
    policy = declared.lower()
    unit = to_base_units(Decimal(1), node.context.scale)

    if policy == "exact":
        return Decimal(0), f"{node.node_id}: exact (declared)"
    if policy.startswith("nearest:"):
        try:
            step = Decimal(policy.removeprefix("nearest:"))
        except InvalidOperation:
            step = Decimal(0)
        if not step.is_finite() or step <= 0:
            raise _Stop(CheckStatus.NEEDS_REVIEW,
                        f"rounding policy {declared!r} on {node.node_id} is not understood")
        half = step / 2 * unit
        return half, f"{node.node_id}: rounded to the nearest {step} (declared), ±{half} base units"
    if policy not in ("", "displayed"):
        raise _Stop(CheckStatus.NEEDS_REVIEW,
                    f"rounding policy {declared!r} on {node.node_id} is not supported")

    exponent = node.normalized_value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise _Stop(CheckStatus.NEEDS_REVIEW, f"{node.node_id} has no finite precision")
    half = Decimal(5).scaleb(exponent - 1) * unit
    origin = "declared" if policy else "no rounding policy declared"
    return half, (f"{node.node_id}: written to {Decimal(1).scaleb(exponent)} "
                  f"{node.context.scale or 'as written'}, ±{half} base units "
                  f"(precision of the figure; {origin})")


def _rounding(nodes: list[Node], trace: list[str]) -> list[Decimal]:
    halves = []
    for n in nodes:
        half, why = _half_step(n)
        trace.append(f"rounding: {why}")
        halves.append(half)
    return halves


def _base(node: Node) -> Decimal:
    assert node.normalized_value is not None
    return to_base_units(node.normalized_value, node.context.scale)


# -- checks ------------------------------------------------------------------

def _pair_roles(relation: Relation) -> tuple[str, str]:
    if relation.type is RelationType.VALUE_FROM:
        return "reported", "source"
    roles = [e.role for e in relation.endpoints]
    return roles[0], roles[1]


def _compare_pair(check, relation, nodes, run_id, trace, involved) -> CheckResult:
    role_a, role_b = _pair_roles(relation)
    ids_a, ids_b = relation.role(role_a), relation.role(role_b)
    if not ids_a or not ids_b:
        return _result(check, run_id, CheckStatus.ERROR,
                       "relation does not carry the expected two roles", trace, involved)
    a, b = nodes[ids_a[0]], nodes[ids_b[0]]
    _values_present([a, b])

    # Identity is established BEFORE any comparison of values.
    compat = compatible(a.context, b.context)
    trace.append(f"context: {compat.reason}")
    if not compat.comparable:
        raise _Stop(CheckStatus.NEEDS_REVIEW, f"not comparable - {compat.reason}")
    _scales_reconcilable([a, b], trace)
    allowed = check.tolerance + sum(_rounding([a, b], trace), Decimal(0))

    va, vb = _base(a), _base(b)
    trace.append(f"{a.node_id} = {va} base units (scale={a.context.scale})")
    trace.append(f"{b.node_id} = {vb} base units (scale={b.context.scale})")
    diff = abs(va - vb)
    trace.append(f"|difference| = {diff}, allowed = {allowed}")

    if diff <= allowed:
        return _result(check, run_id, CheckStatus.PASS,
                       f"values agree within tolerance ({va} vs {vb}, allowed ±{allowed})",
                       trace, involved)
    # An inconsistency is detected; which side is wrong is NOT asserted.
    return _result(check, run_id, CheckStatus.FAIL,
                   f"values disagree: {va} vs {vb} (difference {diff}, allowed ±{allowed}). "
                   f"This identifies an inconsistency, not which source is correct.",
                   trace, involved)


def _check_sum(check, relation, nodes, run_id, trace, involved) -> CheckResult:
    total_ids, operand_ids = relation.role("total"), relation.role("operand")
    if not total_ids or len(operand_ids) < 2:
        return _result(check, run_id, CheckStatus.ERROR,
                       "SUM_OF needs one total and at least two operands", trace, involved)

    total = nodes[total_ids[0]]
    operands = [nodes[i] for i in operand_ids]
    participants = [total, *operands]
    _values_present(participants)

    clash = conflicts([n.context for n in participants], SHARED_FIELDS + DIMENSION_FIELDS)
    trace.append(f"context: {clash.reason}")
    if not clash.comparable:
        raise _Stop(CheckStatus.NEEDS_REVIEW, f"not comparable - {clash.reason}")
    _scales_reconcilable(participants, trace)
    allowed = check.tolerance + sum(_rounding(participants, trace), Decimal(0))

    tv = _base(total)
    ovs = [_base(o) for o in operands]
    summed = sum(ovs, Decimal(0))
    trace.append(" + ".join(f"{o.node_id}={v}" for o, v in zip(operands, ovs)))
    trace.append(f"sum = {summed}; total {total.node_id} = {tv}; allowed = {allowed}")
    diff = abs(summed - tv)
    if diff <= allowed:
        return _result(check, run_id, CheckStatus.PASS,
                       f"total {tv} equals the sum of {len(operands)} operands "
                       f"(allowed ±{allowed})", trace, involved)
    return _result(check, run_id, CheckStatus.FAIL,
                   f"total {tv} does not equal operand sum {summed} "
                   f"(difference {diff}, allowed ±{allowed})", trace, involved)


def _check_ratio(check, relation, nodes, run_id, trace, involved) -> CheckResult:
    r_ids, n_ids, d_ids = (
        relation.role("result"), relation.role("numerator"), relation.role("denominator")
    )
    if not (r_ids and n_ids and d_ids):
        return _result(check, run_id, CheckStatus.ERROR,
                       "RATIO_OF needs result, numerator and denominator", trace, involved)

    result_n, num, den = nodes[r_ids[0]], nodes[n_ids[0]], nodes[d_ids[0]]
    participants = [result_n, num, den]
    _values_present(participants)

    clash = conflicts([n.context for n in participants], SHARED_FIELDS)
    trace.append(f"context: {clash.reason}")
    if not clash.comparable:
        raise _Stop(CheckStatus.NEEDS_REVIEW, f"not comparable - {clash.reason}")
    _scales_reconcilable(participants, trace)
    half_r, half_n, half_d = _rounding(participants, trace)

    rv, nv, dv = _base(result_n), _base(num), _base(den)
    if dv == 0 or abs(dv) <= half_d:
        return _result(check, run_id, CheckStatus.NEEDS_REVIEW,
                       f"denominator {den.node_id} is zero or within its rounding of zero; "
                       f"the ratio is undefined", trace, involved)

    computed = nv / dv
    # First-order propagation of the operands' rounding into the ratio.
    propagated = (half_n + abs(computed) * half_d) / abs(dv)
    allowed = check.tolerance + half_r + propagated
    trace.append(f"{nv} / {dv} = {computed}; reported {rv}; allowed = {allowed}")
    diff = abs(computed - rv)
    if diff <= allowed:
        return _result(check, run_id, CheckStatus.PASS,
                       f"ratio {rv} matches {nv}/{dv} (allowed ±{allowed})", trace, involved)
    return _result(check, run_id, CheckStatus.FAIL,
                   f"ratio {rv} does not match computed {computed} "
                   f"(difference {diff}, allowed ±{allowed})", trace, involved)
