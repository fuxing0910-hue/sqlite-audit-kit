"""Command-line entry point, available without installation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .compare import compare_snapshots
from .demo import create_demo
from .io import load_snapshot, write_json, write_text
from .report import render_report
from .scan import AuditError, KeySpec, scan_database


def _protect_inputs(inputs: Sequence[str], outputs: Sequence[str | None]) -> None:
    source_paths = {Path(path).expanduser().resolve() for path in inputs}
    destinations = [Path(path).expanduser().resolve() for path in outputs if path]
    if source_paths.intersection(destinations):
        raise AuditError("An output path must not overwrite an input database or snapshot")
    if len(set(destinations)) != len(destinations):
        raise AuditError("HTML and JSON outputs must use different paths")
    for destination in destinations:
        if destination.exists() and any(destination.samefile(source) for source in source_paths if source.exists()):
            raise AuditError("An output path must not alias an input database or snapshot")
    for index, destination in enumerate(destinations):
        if destination.exists() and any(destination.samefile(other) for other in destinations[:index] if other.exists()):
            raise AuditError("HTML and JSON outputs must not alias the same file")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sqlite-audit", description="Read-only SQLite snapshots and deterministic audit comparisons."
    )
    parser.add_argument("--version", action="version", version=f"sqlite-audit-kit {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Fully scan an existing database in a read-only transaction")
    scan.add_argument("database", help="Existing local SQLite database")
    scan.add_argument("--output", required=True, help="Destination snapshot JSON")
    scan.add_argument("--key", action="append", default=[], metavar="TABLE:COLUMN[,COLUMN]",
                      help="Repeat for selected candidate keys; any-NULL rows are excluded")
    scan.add_argument("--fk-detail-limit", type=int, default=100, help="Bounded FK details (0–1000; default 100); counts stay exact")
    compare = commands.add_parser("compare", help="Compare v1 snapshots and write an offline report")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.add_argument("--html", help="Destination standalone HTML report")
    compare.add_argument("--json", help="Destination comparison JSON")
    compare.add_argument("--fail-on-regression", action="store_true",
                         help="Exit 1 for regressions, 2 for incomplete coverage; reports are still written")
    demo = commands.add_parser("demo", help="Generate synthetic SQLite fixtures, snapshots and reports")
    demo.add_argument("--output-dir", default="demo", help="Fresh directory for generated demo files")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            _protect_inputs([args.database], [args.output])
            rules = [KeySpec.parse(value) for value in args.key]
            snapshot = scan_database(args.database, rules, fk_detail_limit=args.fk_detail_limit)
            write_json(args.output, snapshot)
            fk = snapshot["foreign_keys"]
            count = fk["violation_count"] if fk["checked"] else "unavailable"
            print(f"Snapshot: {args.output} | {len(snapshot['tables'])} tables | full scan | FK violations: {count}")
            if snapshot["unsupported_tables"]:
                print(f"Excluded {len(snapshot['unsupported_tables'])} virtual/shadow tables; see snapshot scope.")
            if not fk["checked"]:
                print(f"Foreign-key check incomplete: {fk['error']}", file=sys.stderr)
            return 0
        if args.command == "compare":
            if not args.html and not args.json:
                raise AuditError("Select at least one output: --html report.html and/or --json report.json")
            _protect_inputs([args.before, args.after], [args.html, args.json])
            comparison = compare_snapshots(load_snapshot(args.before), load_snapshot(args.after))
            if args.json:
                write_json(args.json, comparison)
            if args.html:
                write_text(args.html, render_report(comparison))
            print(f"Gate: {comparison['gate']['status']} | {len(comparison['regressions'])} regression findings")
            for finding in comparison["regressions"]:
                print(f"  - {finding['message']}")
            for reason in comparison["gate"]["incomplete_reasons"]:
                print(f"  - Incomplete: {reason}")
            return comparison["gate"]["exit_code"] if args.fail_on_regression else 0
        paths = create_demo(args.output_dir)
        print("Synthetic demo generated:")
        for name, path in paths.items():
            print(f"  {name}: {path}")
        print("Expected findings: one FK violation and one selected-key duplicate regression.")
        return 0
    except (AuditError, OSError) as error:
        print(f"sqlite-audit: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
