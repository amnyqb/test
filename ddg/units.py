"""Quantity normalisation and context compatibility.

Comparison order is fixed and never shortcut: entity, period, scenario, then
dimensional compatibility, then scale, then rounding. A mismatch at any earlier
stage is a review item, not a numerical failure - the two numbers were never
comparable, so calling them unequal would be a category error.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ddg.models import QuantityContext

#: Display scales and their multiplier into base units.
SCALES: dict[str, Decimal] = {
    "units": Decimal(1),
    "thousands": Decimal(1_000),
    "millions": Decimal(1_000_000),
    "billions": Decimal(1_000_000_000),
}

#: Context fields that must agree before two quantities may be compared.
IDENTITY_FIELDS = ("entity", "period", "scenario")
DIMENSION_FIELDS = ("unit", "currency")


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
    cleaned = raw.strip().replace(",", "").replace(" ", "")
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
    """Decide whether two quantities may legitimately be compared."""
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
    return Compatibility(True, "entity, period, scenario, unit and currency agree")
