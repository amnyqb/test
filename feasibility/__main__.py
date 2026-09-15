"""Command line: python -m feasibility list | run <ID|all> | repro <ID> <seed>."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from feasibility.cases import USE_CASES
from feasibility.harness import render, run, run_seed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m feasibility",
        description="Generated correctness tests for Alfa Document (feasibility plan, Part 1)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show the implemented use cases")
    r = sub.add_parser("run", help="run a use case, or all of them")
    r.add_argument("use_case", help="an id such as G04, or 'all'")
    r.add_argument("--n", type=int, help="number of generated cases (default per use case)")
    r.add_argument("--seed", type=int, default=0, help="base seed; runs are reproducible")
    r.add_argument("--json", type=Path, help="also write the reports as JSON")
    x = sub.add_parser("repro", help="re-run one generated case exactly")
    x.add_argument("use_case")
    x.add_argument("seed", help="a seed as printed in a report, e.g. G06:0:17")
    args = p.parse_args(argv)

    if args.cmd == "list":
        for case in USE_CASES.values():
            print(f"{case.id}  {case.question}  (default n={case.default_n})")
        return 0

    if args.cmd == "repro":
        case = USE_CASES.get(args.use_case.upper())
        if case is None:
            print(f"unknown use case {args.use_case}", file=sys.stderr)
            return 2
        out = run_seed(case, args.seed)
        print(f"{args.seed}: {'ok' if out.ok else 'FAILED'} - {out.label}")
        print(out.detail)
        if out.counts:
            print(out.counts)
        return 0 if out.ok else 1

    ids = list(USE_CASES) if args.use_case.lower() == "all" else [args.use_case.upper()]
    unknown = [i for i in ids if i not in USE_CASES]
    if unknown:
        print(f"unknown use case(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    reports = [run(USE_CASES[i], args.n, args.seed) for i in ids]
    print("\n\n".join(render(report) for report in reports))
    if args.json:
        args.json.write_text(json.dumps([report.as_dict() for report in reports], indent=2))
    # Any failure in a generated correctness test is something to look at.
    return 0 if all(report.failed == 0 for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
