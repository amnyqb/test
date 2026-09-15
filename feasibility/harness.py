"""Generated correctness harness (feasibility plan, Part 1).

Each use case builds a tiny input from a seed, runs it through the real ddg code
path, and judges the result against an answer known by construction. A run
reports counts by outcome, a Wilson 95% interval on the pass rate, node-level
totals, and the smallest failing seeds - so any failure reproduces exactly with
``python -m feasibility repro <use case> <seed>``.

These tests show whether the mechanics work. They never measure how the system
performs on real documents.
"""

from __future__ import annotations

import math
import random
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable


class FailureKind(str, Enum):
    """What a failure of a use case means (see the feasibility plan)."""

    DEFECT = "a defect to repair"
    TARGET = "a missed target"
    DESIGN = "evidence against the design"


@dataclass
class Outcome:
    ok: bool
    label: str
    critical: bool = False
    #: How large the generated case was, so the smallest failures surface first.
    size: int = 0
    detail: str = ""
    #: Node- or relation-level tallies, summed across a run.
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class UseCase:
    id: str
    question: str
    failure_kind: FailureKind
    critical_means: str
    run_one: Callable[[random.Random], Outcome]
    default_n: int = 1000


@dataclass
class Report:
    use_case: str
    question: str
    failure_kind: str
    critical_means: str
    n: int
    base_seed: int
    passed: int
    failed: int
    critical: int
    pass_rate: float
    ci_low: float
    ci_high: float
    outcomes: dict[str, int]
    totals: dict[str, int]
    smallest_failures: list[dict]
    seconds: float

    def as_dict(self) -> dict:
        return asdict(self)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval; honest at 0% and 100%, unlike the normal approximation."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def seed_for(use_case: str, base_seed: int, index: int) -> str:
    return f"{use_case}:{base_seed}:{index}"


def run_seed(case: UseCase, seed: str) -> Outcome:
    try:
        return case.run_one(random.Random(seed))
    except Exception as exc:  # a crash is a failure, never a skip
        return Outcome(False, f"crash ({type(exc).__name__})",
                       detail=f"{type(exc).__name__}: {exc}")


def run(case: UseCase, n: int | None = None, base_seed: int = 0, keep: int = 5) -> Report:
    n = n or case.default_n
    outcomes: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    failures: list[dict] = []
    passed = critical = 0
    start = time.perf_counter()
    for index in range(n):
        seed = seed_for(case.id, base_seed, index)
        out = run_seed(case, seed)
        outcomes[out.label] += 1
        totals.update(out.counts)
        if out.ok:
            passed += 1
            continue
        critical += out.critical
        failures.append({"seed": seed, "label": out.label, "critical": out.critical,
                         "size": out.size, "detail": out.detail})
    failures.sort(key=lambda f: (not f["critical"], f["size"], f["seed"]))
    low, high = wilson_interval(passed, n)
    return Report(
        use_case=case.id, question=case.question, failure_kind=case.failure_kind.value,
        critical_means=case.critical_means, n=n, base_seed=base_seed,
        passed=passed, failed=n - passed, critical=critical,
        pass_rate=passed / n if n else 0.0, ci_low=low, ci_high=high,
        outcomes=dict(outcomes.most_common()), totals=dict(sorted(totals.items())),
        smallest_failures=failures[:keep], seconds=round(time.perf_counter() - start, 2),
    )


def render(report: Report) -> str:
    lines = [
        f"{report.use_case}  {report.question}",
        f"  cases {report.n} (seeds {report.use_case}:{report.base_seed}:0..{report.n - 1})"
        f"  time {report.seconds}s",
        f"  passed {report.passed}  failed {report.failed}  critical {report.critical}",
        f"  pass rate {report.pass_rate:.2%}  (95% CI {report.ci_low:.2%} to {report.ci_high:.2%})",
        f"  a failure here is {report.failure_kind}; critical means {report.critical_means}",
        "  outcomes:",
    ]
    lines += [f"    {count:>6}  {label}" for label, count in report.outcomes.items()]
    if report.totals:
        lines.append("  totals across all cases:")
        lines += [f"    {count:>6}  {name}" for name, count in report.totals.items()]
    if report.smallest_failures:
        lines.append("  smallest failures (python -m feasibility repro <use case> <seed>):")
        for f in report.smallest_failures:
            lines.append(f"    {'CRITICAL  ' if f['critical'] else ''}{f['seed']}  {f['label']}")
            lines.append(f"      {f['detail'][:500]}")
    return "\n".join(lines)
