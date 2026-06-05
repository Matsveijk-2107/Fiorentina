"""Command-line interface.

Subcommands:
    run      ingest sources incrementally, then refresh gold (unless --no-metrics)
    metrics  force-recompute gold from current silver
    query    run a named query or raw --sql; render as table/csv/json
    status   show manifest summary (what is materialised, last run)
    queries  list available named queries
    verify   re-read the written Parquet and re-assert invariants

Designed so the whole exercise runs from one command:
    python -m football_pipeline run
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from . import __version__, metrics, queries, verify, warehouse_db
from .config import DEFAULT_SOURCES, layout_for
from .ingest import run_ingest
from .logging_utils import configure, get_logger
from .state import PipelineState

log = get_logger("cli")


# --------------------------------------------------------------------------- #
# Rendering helpers.
# --------------------------------------------------------------------------- #
def _render(relation, fmt: str) -> str:
    columns = relation.columns
    rows = relation.fetchall()
    if fmt == "json":
        return json.dumps(
            [dict(zip(columns, r, strict=True)) for r in rows],
            indent=2,
            default=str,
            ensure_ascii=False,
        )
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(columns)
        w.writerows(rows)
        return buf.getvalue().rstrip("\n")
    return _ascii_table(columns, rows)


def _ascii_table(columns, rows) -> str:
    str_rows = [["" if v is None else str(v) for v in r] for r in rows]
    widths = [len(c) for c in columns]
    for r in str_rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells):
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    sep = "-+-".join("-" * w for w in widths)
    out = [fmt_row(columns), sep, *(fmt_row(r) for r in str_rows)]
    if not rows:
        out.append("(no rows)")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Subcommand handlers.
# --------------------------------------------------------------------------- #
def _cmd_run(args) -> int:
    layout = layout_for(args.warehouse)
    sources = [Path(s) for s in (args.source or [str(p) for p in DEFAULT_SOURCES])]
    report = run_ingest(sources, layout, full_refresh=args.full_refresh)

    if report.failed:
        for path, err in report.failed:
            log.error("FAILED %s: %s", path, err)

    if args.no_metrics:
        log.info("skipping gold (metrics) recomputation (--no-metrics)")
    elif (
        report.silver_changed or args.full_refresh or not layout.player_match_stats_path().exists()
    ):
        metrics.compute_gold(layout)
    else:
        log.info("silver unchanged; gold is already up to date (no recompute)")

    print(
        f"\nIngest: processed {len(report.processed)}, "
        f"unchanged {len(report.skipped_unchanged)}, "
        f"removed {len(report.removed)}, failed {len(report.failed)}."
    )
    if report.processed:
        print(f"  (re)written match_ids: {sorted(report.processed)}")
    if report.warnings:
        print(f"  data-quality warnings: {len(report.warnings)} (see log above)")
    return 1 if report.failed else 0


def _cmd_metrics(args) -> int:
    layout = layout_for(args.warehouse)
    counts = metrics.compute_gold(layout)
    print(f"Gold recomputed: {counts}")
    return 0


def _cmd_query(args) -> int:
    layout = layout_for(args.warehouse)
    if not warehouse_db.has_silver(layout):
        print("No data in warehouse. Run `python -m football_pipeline run` first.", file=sys.stderr)
        return 2

    if args.sql:
        rel = queries.run_sql(layout, args.sql)
    else:
        params = {
            "limit": args.limit,
            "min_passes": args.min_passes,
            "min_tackles": args.min_tackles,
            "min_shots": args.min_shots,
            "match_id": args.match_id,
        }
        try:
            rel = queries.run_named(layout, args.name, params)
        except (KeyError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    print(_render(rel, args.format))
    return 0


def _cmd_queries(args) -> int:
    for q in queries.REGISTRY.values():
        print(f"  {q.name:<28} {q.description}")
    return 0


def _cmd_status(args) -> int:
    layout = layout_for(args.warehouse)
    state = PipelineState.load(layout.state_file)
    owners = [r for r in state.sources.values() if r.is_owner]
    shadowed = [r for r in state.sources.values() if not r.is_owner]
    print(f"Warehouse:   {layout.root}")
    print(f"Runs:        {state.runs}")
    print(f"Last run:    {state.last_run_at}")
    print(f"Matches:     {len(owners)} materialised, {len(shadowed)} shadowed duplicates")
    print(f"Total events: {sum(r.n_events for r in owners)}")
    if warehouse_db.has_silver(layout):
        con = warehouse_db.connect(layout)
        try:
            corrected = [
                r[0]
                for r in con.sql(
                    "SELECT match_id FROM matches WHERE corrected ORDER BY 1"
                ).fetchall()
            ]
        finally:
            con.close()
        if corrected:
            print(f"Corrected re-exports: {corrected}")
    return 0


def _cmd_verify(args) -> int:
    layout = layout_for(args.warehouse)
    report = verify.run_verify(layout)
    if report.ok:
        print("verify: OK, all invariants hold.")
        return 0
    print("verify: FAILED", file=sys.stderr)
    for problem in report.problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# Parser.
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="football-pipeline", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--warehouse", default=None, help="warehouse root (default: ./warehouse)")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="incrementally ingest sources, then refresh gold")
    run.add_argument(
        "--source", action="append", help="source dir (repeatable; default: raw + raw_update)"
    )
    run.add_argument("--full-refresh", action="store_true", help="wipe and rebuild from scratch")
    run.add_argument("--no-metrics", action="store_true", help="skip gold recomputation")
    run.set_defaults(func=_cmd_run)

    met = sub.add_parser("metrics", help="force-recompute gold tables")
    met.set_defaults(func=_cmd_metrics)

    q = sub.add_parser("query", help="run a named query or raw SQL")
    q.add_argument("name", nargs="?", default="matches_per_competition", help="named query")
    q.add_argument("--sql", help="run raw SQL instead of a named query")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--min-passes", type=int, default=30, dest="min_passes")
    q.add_argument("--min-tackles", type=int, default=10, dest="min_tackles")
    q.add_argument("--min-shots", type=int, default=3, dest="min_shots")
    q.add_argument("--match-id", type=int, default=None, dest="match_id")
    q.add_argument("--format", choices=["table", "csv", "json"], default="table")
    q.set_defaults(func=_cmd_query)

    sub.add_parser("queries", help="list named queries").set_defaults(func=_cmd_queries)
    sub.add_parser("status", help="show pipeline status").set_defaults(func=_cmd_status)
    sub.add_parser("verify", help="re-read the warehouse and re-assert invariants").set_defaults(
        func=_cmd_verify
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure(verbose=getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
