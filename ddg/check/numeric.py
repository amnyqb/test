"""Deterministic numerical checkers.

All arithmetic is Decimal. No float touches the check path, because a display
rounding artefact reported as an inconsistency costs an analyst the same time
as a real defect and costs the tool its credibility faster.

The verdict taxonomy is load-bearing: a missing value, an incompatible context
or an unsupported formula yields NOT_CHECKED or NEEDS_REVIEW. None of them ever
becomes PASS.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from ddg import CHECKER_VERSION
from ddg.models import (
    CheckDefinition,
    CheckResult,
    CheckStatus,
    Node,
    Relation,
    RelationType,
)
from ddg.units import compatible, to_base_units


def _hash_inputs(nodes: list[Node]) -> tuple[str, ...]:
    return tuple(
        hashlib.sha256(
            f"{n.node_id}|{n.source_version}|{n.normalized_value}".encode()
        ).hexdigest()[:16]
        for n in nodes
    )


def _base(node: Node) -> Decimal | None:
    if node.normalized_value is None:
        return None
    try:
        return to_base_units(node.normalized_value, node.context.scale)
    except ValueError:
        return None


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
    except Exception as exc:  # a checker fault is ERROR, never a pass
        return _result(check, run_id, CheckStatus.ERROR,
                       f"{type(exc).__name__}: {exc}", trace, involved)


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

    # Context compatibility is established BEFORE any comparison of values.
    compat = compatible(a.context, b.context)
    trace.append(f"context: {compat.reason}")
    if not compat.comparable:
        return _result(check, run_id, CheckStatus.NEEDS_REVIEW,
                       f"not comparable - {compat.reason}", trace, involved)

    va, vb = _base(a), _base(b)
    if va is None or vb is None:
        missing = a.node_id if va is None else b.node_id
        return _result(check, run_id, CheckStatus.NOT_CHECKED,
                       f"no normalised value for {missing}", trace, involved)

    trace.append(f"{a.node_id} = {va} base units (scale={a.context.scale})")
    trace.append(f"{b.node_id} = {vb} base units (scale={b.context.scale})")
    diff = abs(va - vb)
    trace.append(f"|difference| = {diff}, tolerance = {check.tolerance}")

    if diff <= check.tolerance:
        return _result(check, run_id, CheckStatus.PASS,
                       f"values agree within tolerance ({va} vs {vb})", trace, involved)
    # An inconsistency is detected; which side is wrong is NOT asserted.
    return _result(check, run_id, CheckStatus.FAIL,
                   f"values disagree: {va} vs {vb} (difference {diff}). "
                   f"This identifies an inconsistency, not which source is correct.",
                   trace, involved)


def _check_sum(check, relation, nodes, run_id, trace, involved) -> CheckResult:
    total_ids, operand_ids = relation.role("total"), relation.role("operand")
    if not total_ids or len(operand_ids) < 2:
        return _result(check, run_id, CheckStatus.ERROR,
                       "SUM_OF needs one total and at least two operands", trace, involved)

    total = nodes[total_ids[0]]
    operands = [nodes[i] for i in operand_ids]
    tv = _base(total)
    ovs = [_base(o) for o in operands]
    if tv is None or any(v is None for v in ovs):
        return _result(check, run_id, CheckStatus.NOT_CHECKED,
                       "one or more operands has no normalised value", trace, involved)

    summed = sum(ovs, Decimal(0))
    trace.append(" + ".join(f"{o.node_id}={v}" for o, v in zip(operands, ovs)))
    trace.append(f"sum = {summed}; total {total.node_id} = {tv}")
    diff = abs(summed - tv)
    if diff <= check.tolerance:
        return _result(check, run_id, CheckStatus.PASS,
                       f"total {tv} equals the sum of {len(operands)} operands", trace, involved)
    return _result(check, run_id, CheckStatus.FAIL,
                   f"total {tv} does not equal operand sum {summed} (difference {diff})",
                   trace, involved)


def _check_ratio(check, relation, nodes, run_id, trace, involved) -> CheckResult:
    r_ids, n_ids, d_ids = (
        relation.role("result"), relation.role("numerator"), relation.role("denominator")
    )
    if not (r_ids and n_ids and d_ids):
        return _result(check, run_id, CheckStatus.ERROR,
                       "RATIO_OF needs result, numerator and denominator", trace, involved)

    result_n, num, den = nodes[r_ids[0]], nodes[n_ids[0]], nodes[d_ids[0]]
    rv, nv, dv = _base(result_n), _base(num), _base(den)
    if rv is None or nv is None or dv is None:
        return _result(check, run_id, CheckStatus.NOT_CHECKED,
                       "ratio operands are not all normalised", trace, involved)
    if dv == 0:
        return _result(check, run_id, CheckStatus.NEEDS_REVIEW,
                       f"denominator {den.node_id} is zero; the ratio is undefined",
                       trace, involved)

    computed = nv / dv
    trace.append(f"{nv} / {dv} = {computed}; reported {rv}")
    diff = abs(computed - rv)
    if diff <= check.tolerance:
        return _result(check, run_id, CheckStatus.PASS,
                       f"ratio {rv} matches {nv}/{dv}", trace, involved)
    return _result(check, run_id, CheckStatus.FAIL,
                   f"ratio {rv} does not match computed {computed} (difference {diff})",
                   trace, involved)
