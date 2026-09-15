"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ddg.pipeline import audit, render
from ddg.store import SchemaError, Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ddg", description="Document Dependency Graphs")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="import a package and run eligible checks")
    a.add_argument("package", type=Path, help="directory holding one document package")
    a.add_argument("--db", type=Path, default=Path("ddg.sqlite"))
    a.add_argument("--export", type=Path, help="write the graph as JSON")

    r = sub.add_parser("revise", help="carry the graph onto a new version of the package")
    r.add_argument("package", type=Path, help="directory holding the revised package")
    r.add_argument("--db", type=Path, default=Path("ddg.sqlite"))
    r.add_argument("--export", type=Path, help="write the revised graph as JSON")

    y = sub.add_parser("replay", help="re-execute a recorded run and compare verdicts")
    y.add_argument("run_id")
    y.add_argument("--db", type=Path, default=Path("ddg.sqlite"))

    args = p.parse_args(argv)

    if args.cmd in ("audit", "revise") and not args.package.is_dir():
        print(f"not a directory: {args.package}", file=sys.stderr)
        return 2
    if args.cmd in ("revise", "replay") and not args.db.is_file():
        print(f"no graph store at {args.db}; run `audit` first", file=sys.stderr)
        return 2

    try:
        with Store(args.db) as store:
            if args.cmd == "audit":
                report = audit(args.package, store)
                print(render(report))
                run_id = report.run_id
            elif args.cmd == "revise":
                from ddg.revision.engine import render_revision, revise
                report = revise(args.package, store)
                print(render_revision(report))
                run_id = report.run_id
            else:
                from ddg.revision.replay import render_replay, replay
                result = replay(store, args.run_id)
                print(render_replay(result))
                # Replay is a verification: an unfaithful replay is a failure.
                return 0 if result.faithful else 1

            if args.export:
                args.export.write_text(json.dumps(store.export(run_id), indent=2))
                print(f"\ngraph exported to {args.export}")
    except (SchemaError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # A failing check is a finding, not a tool error: exit 0.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
