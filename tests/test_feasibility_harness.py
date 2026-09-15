"""Smoke tests for the feasibility harness: it runs, it reproduces, and it reports honestly.

The large runs happen with ``python -m feasibility``; these keep the harness and
its first use cases from rotting between them.
"""

from __future__ import annotations

from feasibility.cases import USE_CASES
from feasibility.harness import FailureKind, UseCase, run, run_seed, wilson_interval


def test_wilson_interval_stays_honest_at_the_extremes():
    low, high = wilson_interval(400, 400)
    assert high == 1.0 and 0.99 < low < 1.0, "400 of 400 does not prove 100%"
    low, high = wilson_interval(0, 400)
    assert low == 0.0 and 0.0 < high < 0.01


def test_a_crashing_case_is_a_failure_never_a_skip():
    def broken(rng):
        raise ValueError("generator bug")

    report = run(UseCase("X", "?", FailureKind.DEFECT, "-", broken), n=3)
    assert report.failed == 3
    assert report.smallest_failures[0]["label"] == "crash (ValueError)"


def test_a_seed_reproduces_the_same_case_exactly():
    case = USE_CASES["G04"]
    assert run_seed(case, "G04:7:3").detail == run_seed(case, "G04:7:3").detail


def test_identity_use_case_passes_a_small_run():
    report = run(USE_CASES["G04"], n=300, base_seed=11)
    assert report.failed == 0, report.smallest_failures


def test_structural_use_case_passes_a_small_run():
    report = run(USE_CASES["G06"], n=8, base_seed=11)
    assert report.failed == 0, report.smallest_failures
