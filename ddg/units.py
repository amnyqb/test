"""Quantity normalisation and context compatibility.

Comparison order is fixed and never shortcut: entity, metric, period, scenario,
then dimensional compatibility, then scale, then rounding. A mismatch at any
earlier stage is a review item, not a numerical failure - the two numbers were
never comparable, so calling them unequal would be a category error.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ddg.models import QuantityContext

#: Display scales and their multiplier into base units.
SCALES: dict[str, Decimal] = {
    "units": Decimal(1),
    "percent": Decimal("0.01"),
    "thousands": Decimal(1_000),
    "millions": Decimal(1_000_000),
    "billions": Decimal(1_000_000_000),
    "trillions": Decimal(1_000_000_000_000),
}

#: Fields that must agree before two values may be called the same quantity.
#: Equal numbers are not enough: a revenue line and a contingency line can both
#: read 8.0 million.
IDENTITY_FIELDS = ("entity", "metric", "period", "scenario")
DIMENSION_FIELDS = ("unit", "currency")

#: Fields the participants of a sum or ratio must not contradict. Metric is
#: deliberately absent: a total and its components measure different things.
SHARED_FIELDS = ("entity", "period", "scenario")


@dataclass(frozen=True)
class Compatibility:
    comparable: bool
    reason: str
    missing: tuple[str, ...] = ()


def to_base_units(value: Decimal, scale: str | None) -> Decimal:
    """Convert a displayed value into base units using its declared scale."""
    if scale is None:
        return value
    key = scale.strip().lower()
    if key not in SCALES:
        raise ValueError(f"unknown scale {scale!r}")
    return value * SCALES[key]


def parse_decimal(raw: str) -> Decimal | None:
    """Parse a human-written number without ever falling back to float."""
    cleaned = raw.strip().replace(",", "").replace(" ", "")
    for token in ("$", "£", "€", "USD", "EUR", "GBP"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def compatible(a: QuantityContext, b: QuantityContext) -> Compatibility:
    """Decide whether two values may legitimately be called the same quantity."""
    missing: list[str] = []
    for f in IDENTITY_FIELDS:
        av, bv = getattr(a, f), getattr(b, f)
        if av is None or bv is None:
            missing.append(f)
            continue
        if av != bv:
            return Compatibility(False, f"{f} differs ({av!r} vs {bv!r})")

    for f in DIMENSION_FIELDS:
        av, bv = getattr(a, f), getattr(b, f)
        if av is None or bv is None:
            missing.append(f)
            continue
        if av != bv:
            return Compatibility(
                False, f"{f} differs ({av!r} vs {bv!r}); no conversion is assumed"
            )

    if missing:
        return Compatibility(
            False,
            "context incomplete: " + ", ".join(sorted(set(missing))),
            tuple(sorted(set(missing))),
        )
    return Compatibility(True, "entity, metric, period, scenario, unit and currency agree")


def conflicts(contexts: list[QuantityContext], fields: tuple[str, ...]) -> Compatibility:
    """Whether the declared values of ``fields`` contradict each other.

    Used for sums and ratios, whose participants are different quantities: a
    missing field does not block them, but two declared values that disagree do.
    """
    for f in fields:
        declared = {getattr(c, f) for c in contexts if getattr(c, f) is not None}
        if len(declared) > 1:
            values = ", ".join(sorted(repr(v) for v in declared))
            return Compatibility(False, f"{f} differs among participants ({values})")
    return Compatibility(True, f"no declared {', '.join(fields)} contradict each other")
