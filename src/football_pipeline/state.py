"""Persistent ingestion state (the manifest).

The manifest is the memory that makes re-runs incremental. For each source file
it records the content hash we last ingested, plus which source currently "owns"
each match_id (so a corrected re-export can take over from the initial batch).
It is a plain JSON file: human-readable, diff-able, and easy to move to object
storage in a real deployment.

Writes are atomic (temp file + ``os.replace``) so an interrupted run never
leaves a half-written manifest.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

STATE_VERSION = 1

log = logging.getLogger("state")


@dataclass
class SourceRecord:
    sha256: str
    match_id: int
    is_owner: bool  # True if this source currently materialises its match_id
    n_events: int
    n_players: int
    ingested_at: str


@dataclass
class PipelineState:
    version: int = STATE_VERSION
    runs: int = 0
    last_run_at: str | None = None
    # repo-relative source path -> SourceRecord
    sources: dict[str, SourceRecord] = field(default_factory=dict)
    # str(match_id) -> repo-relative source path that owns it
    match_owner: dict[str, str] = field(default_factory=dict)

    # ----------------------------------------------------------------- I/O ---
    @classmethod
    def load(cls, path: Path) -> PipelineState:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        # Forward-compatibility: a manifest written by a different schema version
        # is treated as unreadable and we rebuild from scratch rather than crash on
        # restart (the warehouse is fully derivable from raw). Within a known
        # version, unknown fields are tolerated, not a trigger: extra keys are
        # filtered out below.
        version = data.get("version", STATE_VERSION)
        if version != STATE_VERSION:
            log.warning(
                "manifest version %s != %s; ignoring it and rebuilding from scratch",
                version,
                STATE_VERSION,
            )
            return cls()
        known = {f.name for f in fields(SourceRecord)}
        sources = {
            k: SourceRecord(**{fk: fv for fk, fv in v.items() if fk in known})
            for k, v in data.get("sources", {}).items()
        }
        return cls(
            version=version,
            runs=data.get("runs", 0),
            last_run_at=data.get("last_run_at"),
            sources=sources,
            match_owner={str(k): v for k, v in data.get("match_owner", {}).items()},
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "runs": self.runs,
            "last_run_at": self.last_run_at,
            "sources": {k: asdict(v) for k, v in sorted(self.sources.items())},
            "match_owner": dict(sorted(self.match_owner.items())),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # atomic on the same filesystem

    # ------------------------------------------------------------- helpers ---
    def stamp_run(self) -> None:
        self.runs += 1
        self.last_run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
