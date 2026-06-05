"""The incremental ingestion engine.

Re-running is the normal case, so the whole thing is built around doing as
little work as possible:

1. Discover every ``*.json`` under the given source dirs, in precedence order.
2. Resolve, per ``match_id``, which source "owns" it. When the same match shows
   up in more than one batch (the corrected ``1003`` lives in both ``raw/`` and
   ``raw_update/``), the later batch wins. That decision is deterministic and
   does not depend on filesystem ordering.
3. For each owning source, compare its content hash against the manifest. The
   match is parsed and rewritten only if it is new, changed, has switched owner,
   or its output file is missing. Everything else is skipped.
4. Drop matches whose sources have disappeared, which handles deletions.

That gives three properties worth stating plainly. It is incremental (only new
or changed matches touch disk), idempotent (a second identical run does no
work), and correcting (a re-exported match overwrites just its own partition,
and gold downstream is recomputed only when silver actually changed).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import hashing, parquet_io
from .config import REPO_ROOT, WarehouseLayout
from .logging_utils import get_logger
from .state import PipelineState, SourceRecord
from .validation import DataQualityWarning, ValidationError, parse_file

log = get_logger("ingest")


@dataclass
class IngestReport:
    processed: list[int] = field(default_factory=list)  # match_ids (re)written
    skipped_unchanged: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)  # match_ids deleted
    shadowed: list[str] = field(default_factory=list)  # non-owning source paths
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, error)
    warnings: list[DataQualityWarning] = field(default_factory=list)

    @property
    def silver_changed(self) -> bool:
        return bool(self.processed or self.removed)

    def summary(self) -> str:
        return (
            f"processed={len(self.processed)} "
            f"unchanged={len(self.skipped_unchanged)} "
            f"removed={len(self.removed)} "
            f"failed={len(self.failed)} "
            f"warnings={len(self.warnings)}"
        )


def _repo_rel(path: Path) -> str:
    """Stable, OS-independent key for a source file."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def discover_sources(source_dirs: list[Path]) -> list[Path]:
    """All match JSON files, ordered by (dir precedence, then path)."""
    found: list[Path] = []
    for d in source_dirs:
        if not d.exists():
            log.warning("source dir does not exist: %s", d)
            continue
        found.extend(sorted(d.rglob("*.json"), key=lambda p: p.as_posix()))
    return found


def _peek_match_id(path: Path) -> int:
    """Read just the match_id without a full parse (cheap ownership resolution)."""
    return int(json.loads(path.read_text(encoding="utf-8"))["match_id"])


def resolve_ownership(sources: list[Path]) -> tuple[dict[int, Path], dict[Path, int]]:
    """Resolve match ownership in a single pass.

    Returns ``(owner_by_match, mid_by_path)`` where, on conflict, later entries
    in ``sources`` win (precedence order). ``mid_by_path`` is reused downstream
    so each file's ``match_id`` is read exactly once.
    """
    owner: dict[int, Path] = {}
    mid_by_path: dict[Path, int] = {}
    for path in sources:  # already in precedence order
        try:
            mid = _peek_match_id(path)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            log.error("cannot read match_id from %s: %s", path, exc)
            continue
        mid_by_path[path] = mid
        owner[mid] = path  # last writer wins
    return owner, mid_by_path


def run_ingest(
    source_dirs: list[Path],
    layout: WarehouseLayout,
    *,
    full_refresh: bool = False,
) -> IngestReport:
    layout.ensure_dirs()
    report = IngestReport()

    if full_refresh:
        log.info("full refresh: clearing warehouse silver + gold + state")
        for d in (*layout.silver_dirs, layout.gold_dir):
            for f in d.glob("*.parquet"):
                f.unlink()
        state = PipelineState()
    else:
        state = PipelineState.load(layout.state_file)

    sources = discover_sources(source_dirs)
    owner_by_match, mid_by_path = resolve_ownership(sources)

    new_sources: dict[str, SourceRecord] = {}

    for path in sources:
        key = _repo_rel(path)
        mid = mid_by_path.get(path)
        if mid is None:  # unreadable match_id; already logged in resolve_ownership
            report.failed.append((key, "unreadable match_id"))
            continue
        is_owner = owner_by_match.get(mid) == path

        try:
            digest = hashing.file_sha256(path)
        except OSError as exc:
            report.failed.append((key, f"hash failed: {exc}"))
            continue

        if not is_owner:
            # A shadowed duplicate (e.g. raw/1003 superseded by raw_update/1003).
            # Track its hash so we notice if it ever changes, but write nothing.
            report.shadowed.append(key)
            prev = state.sources.get(key)
            new_sources[key] = SourceRecord(
                sha256=digest,
                match_id=mid,
                is_owner=False,
                n_events=prev.n_events if prev else 0,
                n_players=prev.n_players if prev else 0,
                ingested_at=prev.ingested_at if prev else "",
            )
            continue

        prev = state.sources.get(key)
        output_present = (layout.events_dir / f"{mid}.parquet").exists()
        owner_changed = state.match_owner.get(str(mid)) != key
        unchanged = (
            prev is not None
            and prev.is_owner
            and prev.sha256 == digest
            and output_present
            and not owner_changed
        )

        if unchanged and prev is not None:  # second clause is implied; narrows type
            report.skipped_unchanged.append(mid)
            new_sources[key] = prev
            continue

        # New / changed / owner-switched -> parse and (over)write the partition.
        try:
            parsed, warnings = parse_file(path, key, digest)
        except ValidationError as exc:
            log.error("validation failed for %s: %s", key, exc)
            report.failed.append((key, str(exc)))
            # Keep previous good output if any; do not corrupt the warehouse.
            if prev is not None:
                new_sources[key] = prev
            continue

        parquet_io.write_match(parsed, layout)
        report.processed.append(mid)
        report.warnings.extend(warnings)
        for w in warnings:
            log.warning("data-quality [%s] match %s: %s", w.code, w.match_id, w.detail)
        new_sources[key] = SourceRecord(
            sha256=digest,
            match_id=mid,
            is_owner=True,
            n_events=len(parsed.events),
            n_players=len(parsed.players),
            ingested_at="",  # stamped below at run level
        )
        log.info(
            "%s match %s (%s) -> %d events",
            "corrected" if parsed.match.corrected else "ingested",
            mid,
            key,
            len(parsed.events),
        )

    # Handle deletions: matches that previously existed but have no source now.
    # Guard against transient read errors: only prune when the previous owning
    # source file is genuinely gone from disk, not merely unreadable this run.
    live_match_ids = set(owner_by_match.keys())
    for mid_str, prev_owner_path in list(state.match_owner.items()):
        mid = int(mid_str)
        if mid in live_match_ids:
            continue
        if (REPO_ROOT / prev_owner_path).exists():
            log.warning(
                "match %s has no resolvable source this run but %s still exists "
                "(transient read error?); keeping existing output",
                mid,
                prev_owner_path,
            )
            continue
        parquet_io.remove_match(mid, layout)
        report.removed.append(mid)
        log.info("removed match %s (source disappeared)", mid)

    # Persist new state.
    state.sources = new_sources
    state.match_owner = {str(mid): _repo_rel(p) for mid, p in owner_by_match.items()}
    state.stamp_run()
    for rec in state.sources.values():
        if rec.is_owner and not rec.ingested_at:
            rec.ingested_at = state.last_run_at or ""
    state.save(layout.state_file)

    log.info("ingest complete: %s", report.summary())
    return report
