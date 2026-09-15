"""G04 - Is a quantity's identity judged on everything that defines it?

One micro-test is two figures for a true quantity, each written at its own
scale and precision. Their contexts may agree, differ in one or two fields, or
miss a field; scales may be undeclared or unknown; rounding policies may be
absent, declared, or malformed; the second figure may carry an error. The
expected verdict follows from how the case was built:

* a field that differs or is missing on either side -> NEEDS_REVIEW;
* an unknown scale, a scale declared on one side only, or no scale across
  different documents -> NEEDS_REVIEW;
* a malformed rounding policy -> NEEDS_REVIEW;
* otherwise PASS exactly when one true value could be written as both figures
  under their rounding, and FAIL when none could.

The oracle states scales and rounding in its own code rather than importing the
checker's, so the two can disagree. A PASS between different quantities, or
with incomplete context, is critical.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ddg.check import run_check
from ddg.models import (
    CheckDefinition,
    CheckStatus,
    Endpoint,
    ExtractionMethod,
    LocationSelector,
    Node,
    NodeKind,
    QuantityContext,
    Relation,
    RelationType,
    ReviewState,
)
from ddg import CHECKER_VERSION
from ddg.pipeline import TOLERANCE
from feasibility.harness import FailureKind, Outcome, UseCase

IDENTITY = ("entity", "metric", "period", "scenario", "unit", "currency")
POOLS: dict[str, list[str]] = {
    "entity": ["ProjectCo", "HoldCo", "OpCo"],
    "metric": ["capex", "revenue", "opex", "contingency"],
    "period": ["FY25", "FY26", "FY27"],
    "scenario": ["base", "downside"],
    "unit": ["currency", "tonnes"],
    "currency": ["USD", "EUR", "SAR"],
}
MULTIPLIER: dict[str, Decimal] = {
    "units": Decimal(1),
    "thousands": Decimal(10) ** 3,
    "millions": Decimal(10) ** 6,
    "billions": Decimal(10) ** 9,
}
MALFORMED_POLICIES = ["banker's", "nearest:abc", "nearest:-1", "nearest:0"]
UNKNOWN_SCALES = ["lakhs", "crores"]

RELATION = Relation(
    relation_id="g04", type=RelationType.SAME_QUANTITY_AS,
    endpoints=(Endpoint(role="a", node_id="a"), Endpoint(role="b", node_id="b")),
    extraction_method=ExtractionMethod.HUMAN_AUTHORED, review_state=ReviewState.ACCEPTED,
    valid_source_versions=("v",),
)
CHECK = CheckDefinition(check_id="chk:g04", relation_id="g04", expression="SAME_QUANTITY_AS",
                        operand_node_ids=("a", "b"), tolerance=TOLERANCE,
                        checker_version=CHECKER_VERSION)


@dataclass
class Side:
    written: Decimal
    precision: int
    scale: str | None
    context: dict[str, str | None]
    policy: str | None
    document: str

    def describe(self) -> str:
        fields = " ".join(f"{k}={v}" for k, v in self.context.items())
        return (f"{self.written} scale={self.scale} policy={self.policy!r} "
                f"doc={self.document} {fields}")


def _write(rng: random.Random, value: Decimal, scale: str | None = None) -> tuple[Decimal, int, str]:
    if scale is None:
        fitting = [s for s, m in MULTIPLIER.items() if Decimal("0.1") <= value / m < Decimal(10) ** 7]
        scale = rng.choice(fitting)
    precision = rng.randint(0, 3)
    written = (value / MULTIPLIER[scale]).quantize(Decimal(1).scaleb(-precision),
                                                    rounding=ROUND_HALF_UP)
    return written, precision, scale


def _policy(rng: random.Random) -> str | None:
    roll = rng.random()
    if roll < 0.6:
        return None
    if roll < 0.7:
        return "displayed"
    if roll < 0.8:
        return "exact"
    if roll < 0.95:
        return f"nearest:{Decimal(rng.choice([1, 2, 5])).scaleb(-rng.randint(0, 3))}"
    return rng.choice(MALFORMED_POLICIES)


def _half_step(side: Side) -> Decimal | None:
    """Half the rounding step of a side, in base units; None for a malformed policy."""
    multiplier = MULTIPLIER[side.scale] if side.scale else Decimal(1)
    policy = (side.policy or "").lower()
    if policy == "exact":
        return Decimal(0)
    if policy.startswith("nearest:"):
        try:
            step = Decimal(policy.removeprefix("nearest:"))
        except InvalidOperation:
            return None
        return step / 2 * multiplier if step.is_finite() and step > 0 else None
    if policy in ("", "displayed"):
        return Decimal("0.5").scaleb(-side.precision) * multiplier
    return None


def expected(a: Side, b: Side) -> tuple[CheckStatus, str]:
    for f in IDENTITY:
        if a.context[f] is None or b.context[f] is None:
            return CheckStatus.NEEDS_REVIEW, "incomplete context"
    for f in IDENTITY:
        if a.context[f] != b.context[f]:
            return CheckStatus.NEEDS_REVIEW, "different quantities"
    if any(s.scale is not None and s.scale not in MULTIPLIER for s in (a, b)):
        return CheckStatus.NEEDS_REVIEW, "unknown scale"
    if (a.scale is None) != (b.scale is None):
        return CheckStatus.NEEDS_REVIEW, "scale declared on one side only"
    if a.scale is None and a.document != b.document:
        return CheckStatus.NEEDS_REVIEW, "no scale, different documents"
    half_a, half_b = _half_step(a), _half_step(b)
    if half_a is None or half_b is None:
        return CheckStatus.NEEDS_REVIEW, "malformed rounding policy"
    value_a = a.written * (MULTIPLIER[a.scale] if a.scale else 1)
    value_b = b.written * (MULTIPLIER[b.scale] if b.scale else 1)
    if abs(value_a - value_b) <= half_a + half_b + TOLERANCE:
        return CheckStatus.PASS, "same quantity, consistent under rounding"
    return CheckStatus.FAIL, "same quantity, inconsistent"


def _node(node_id: str, side: Side) -> Node:
    return Node(
        node_id=node_id, source_version="v", kind=NodeKind.PARAGRAPH,
        selector=LocationSelector(document_id=side.document, kind=NodeKind.PARAGRAPH),
        evidence_text="generated", normalized_value=side.written,
        context=QuantityContext(**side.context, scale=side.scale, rounding_policy=side.policy),
    )


def run_one(rng: random.Random) -> Outcome:
    truth = Decimal(rng.randint(1, 10**9)).scaleb(-rng.randint(0, 2))
    base = {f: rng.choice(pool) for f, pool in POOLS.items()}
    base["unit"] = "currency"
    notes: list[str] = []

    other = truth
    if rng.random() < 0.5:
        fraction = Decimal(str(10 ** rng.uniform(-6, -0.3)))
        error = (truth * fraction).quantize(Decimal("0.01")) * rng.choice([1, -1])
        if truth + error > 0:
            other = truth + error
            notes.append(f"error {error}")

    same_document = rng.random() < 0.1
    declares = [rng.random() < 0.85, rng.random() < 0.85]
    a_written, a_precision, a_scale = _write(rng, truth)
    forced = a_scale if same_document and not any(declares) else None
    b_written, b_precision, b_scale = _write(rng, other, forced)

    contexts = [dict(base), dict(base)]
    if rng.random() < 0.35:
        for f in rng.sample(IDENTITY, rng.randint(1, 2)):
            contexts[1][f] = rng.choice([v for v in POOLS[f] if v != base[f]])
            notes.append(f"B {f} differs")
    if rng.random() < 0.15:
        side, f = rng.randrange(2), rng.choice(IDENTITY)
        contexts[side][f] = None
        notes.append(f"{'AB'[side]} {f} missing")

    def declared(scale: str, is_declared: bool) -> str | None:
        if not is_declared:
            return None
        return rng.choice(UNKNOWN_SCALES) if rng.random() < 0.02 else scale

    documents = ("model", "model") if same_document else ("report", "model")
    a = Side(a_written, a_precision, declared(a_scale, declares[0]), contexts[0], _policy(rng),
             documents[0])
    b = Side(b_written, b_precision, declared(b_scale, declares[1]), contexts[1], _policy(rng),
             documents[1])

    want, why = expected(a, b)
    got = run_check(CHECK, RELATION, {"a": _node("a", a), "b": _node("b", b)}, "g04")
    size = len(notes) + a.precision + b.precision
    detail = (f"A {a.describe()} | B {b.describe()} | truth {truth}; "
              f"{'; '.join(notes) or 'no mutation'} | expected {want.value} ({why}); "
              f"got {got.status.value}: {got.detail}")
    if got.status is want:
        return Outcome(True, f"correct {want.value}: {why}", size=size, detail=detail)
    critical = got.status is CheckStatus.PASS and why in ("different quantities",
                                                          "incomplete context")
    return Outcome(False, f"expected {want.value} ({why}), got {got.status.value}",
                   critical=critical, size=size, detail=detail)


USE_CASE = UseCase(
    id="G04",
    question="Is a quantity's identity judged on everything that defines it?",
    failure_kind=FailureKind.DEFECT,
    critical_means="a PASS between different quantities, or with incomplete context",
    run_one=run_one,
    default_n=5000,
)
