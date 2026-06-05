"""Parquet read/write for the silver layer (one file per match).

Explicit Arrow schemas keep column types stable across runs (nullable ``xg``
and ``recipient_id`` stay typed even when a given match has no such rows), which
is what lets DuckDB read the whole glob as one consistent table.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .config import WarehouseLayout
from .models import ParsedMatch

MATCH_SCHEMA = pa.schema(
    [
        ("match_id", pa.int64()),
        ("competition", pa.string()),
        ("season", pa.string()),
        ("match_date", pa.string()),
        ("home_team_id", pa.int64()),
        ("home_team_name", pa.string()),
        ("away_team_id", pa.int64()),
        ("away_team_name", pa.string()),
        ("score_home", pa.int64()),
        ("score_away", pa.int64()),
        ("corrected", pa.bool_()),
    ]
)

PLAYER_SCHEMA = pa.schema(
    [
        ("match_id", pa.int64()),
        ("player_id", pa.int64()),
        ("name", pa.string()),
        ("team_id", pa.int64()),
        ("position", pa.string()),
        ("minutes", pa.int64()),
    ]
)

EVENT_SCHEMA = pa.schema(
    [
        ("match_id", pa.int64()),
        ("event_id", pa.int64()),
        ("minute", pa.int64()),
        ("second", pa.int64()),
        ("team_id", pa.int64()),
        ("player_id", pa.int64()),
        ("x", pa.float64()),
        ("y", pa.float64()),
        ("type", pa.string()),
        ("outcome", pa.string()),
        ("recipient_id", pa.int64()),
        ("xg", pa.float64()),
    ]
)


def _write(rows: list[dict], schema: pa.Schema, path: Path) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)  # atomic swap


def write_match(parsed: ParsedMatch, layout: WarehouseLayout) -> None:
    """Materialise one match into its three per-match Parquet files."""
    layout.ensure_dirs()
    mid = parsed.match_id
    _write([asdict(parsed.match)], MATCH_SCHEMA, layout.matches_dir / f"{mid}.parquet")
    _write(
        [asdict(p) for p in parsed.players], PLAYER_SCHEMA, layout.players_dir / f"{mid}.parquet"
    )
    _write([asdict(e) for e in parsed.events], EVENT_SCHEMA, layout.events_dir / f"{mid}.parquet")


def remove_match(match_id: int, layout: WarehouseLayout) -> None:
    """Delete a match's silver files (used when a source disappears)."""
    for d in layout.silver_dirs:
        f = d / f"{match_id}.parquet"
        if f.exists():
            f.unlink()
