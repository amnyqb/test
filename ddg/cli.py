"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ddg.pipeline import audit, render
from ddg.store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ddg", description="Document Dependency Graphs")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="import a package and run eligible checks")
    a.add_argument("package", type=Path, help="directory holding one document package")
    a.add_argument("--db", type=Path, default=Path("ddg.sqlite"))
    a.add_argument("--export", type=Path, help="write the graph as JSON")

    args = p.parse_args(argv)

    if args.cmd == "audit":
        if not args.package.is_dir():
            print(f"not a directory: {args.package}", file=sys.stderr)
            return 2
        with Store(args.db) as store:
            report = audit(args.package, store)
            print(render(report))
            if args.export:
                args.export.write_text(json.dumps(store.export(), indent=2))
                print(f"\ngraph exported to {args.export}")
        # A failing check is a finding, not a tool error: exit 0.
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
