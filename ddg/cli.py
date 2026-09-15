"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ddg.models import Endpoint
from ddg.pipeline import audit, render
from ddg.store import SchemaError, Store

_CONTEXT = re.compile(r"^(?P<node>.+):(?P<field>[a-z_]+)=(?P<value>.*)$")


def _endpoint(text: str) -> Endpoint:
    role, sep, node_id = text.partition("=")
    if not (sep and role and node_id):
        raise argparse.ArgumentTypeError(f"expected ROLE=NODE_ID, got {text!r}")
    return Endpoint(role=role, node_id=node_id)


def _context(text: str) -> tuple[str, str, str | None]:
    m = _CONTEXT.match(text)
    if not m:
        raise argparse.ArgumentTypeError(f"expected NODE_ID:FIELD=VALUE, got {text!r}")
    return m["node"], m["field"], m["value"] or None


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

    v = sub.add_parser("review", help="list relations waiting for a person, or decide one")
    vsub = v.add_subparsers(dest="review_cmd", required=True)
    vl = vsub.add_parser("list", help="relations a revision withdrew or stranded")
    vl.add_argument("--db", type=Path, default=Path("ddg.sqlite"))
    vd = vsub.add_parser("decide", help="accept, reject or amend a relation and republish")
    vd.add_argument("relation_id")
    vd.add_argument("verdict", choices=["accept", "reject", "amend"])
    vd.add_argument("--reviewer", required=True)
    vd.add_argument("--reason", required=True)
    vd.add_argument("--minutes", type=float, default=0.0,
                    help="active review time, recorded in the cost ledger")
    vd.add_argument("--endpoint", type=_endpoint, action="append", metavar="ROLE=NODE_ID",
                    help="amend: a replacement endpoint (give every endpoint)")
    vd.add_argument("--context", type=_context, action="append",
                    metavar="NODE_ID:FIELD=VALUE",
                    help="amend: set a context field on an endpoint (empty VALUE clears it)")
    vd.add_argument("--confirm-context", action="append", metavar="NODE_ID",
                    help="accept or amend: confirm, as it stands, the meaning of an endpoint "
                         "a revision put in question")
    vd.add_argument("--db", type=Path, default=Path("ddg.sqlite"))

    args = p.parse_args(argv)

    if args.cmd in ("audit", "revise") and not args.package.is_dir():
        print(f"not a directory: {args.package}", file=sys.stderr)
        return 2
    if args.cmd in ("revise", "replay", "review") and not args.db.is_file():
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
            elif args.cmd == "review":
                from ddg.review.queue import (
                    Decision, decide, pending, render_queue, render_review)
                if args.review_cmd == "list":
                    print(render_queue(pending(store), store.latest_run()))
                    return 0
                contexts: dict[str, dict[str, str | None]] = {}
                for node_id, name, value in args.context or []:
                    contexts.setdefault(node_id, {})[name] = value
                report = decide(store, [Decision(
                    relation_id=args.relation_id, verdict=args.verdict,
                    reviewer=args.reviewer, reason=args.reason, minutes=args.minutes,
                    endpoints=tuple(args.endpoint) if args.endpoint else None,
                    contexts=contexts,
                    confirm_context=tuple(args.confirm_context or ()),
                )])
                print(render_review(report))
                return 0
            else:
                from ddg.revision.replay import render_replay, replay
                result = replay(store, args.run_id)
                print(render_replay(result))
                # Replay is a verification: an unfaithful replay is a failure.
                return 0 if result.faithful else 1

            if args.export:
                args.export.write_text(json.dumps(store.export(run_id), indent=2))
                print(f"\ngraph exported to {args.export}")
    except (SchemaError, ValueError) as exc:  # includes refused review batches
        print(str(exc), file=sys.stderr)
        return 2
    # A failing check is a finding, not a tool error: exit 0.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
