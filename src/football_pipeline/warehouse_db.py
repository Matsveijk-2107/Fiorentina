"""DuckDB connection bound to the warehouse Parquet files.

DuckDB is the "lightweight engine that queries open files" from the brief. We
never load data into a server; instead we register SQL views directly over the
Parquet globs and let DuckDB push predicates into the files. Views are created
only for layers that actually have data, so querying before metrics have been
computed degrades gracefully instead of erroring.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .config import WarehouseLayout


def _glob_has_files(glob_dir: Path) -> bool:
    return any(glob_dir.glob("*.parquet"))


def _sql_literal(path: str) -> str:
    """Escape a path for safe embedding as a single-quoted SQL string literal.

    Paths are not user-controlled here, but a warehouse dir containing a quote
    (e.g. a home like ``O'Brien``) would otherwise break the statement.
    """
    return "'" + path.replace("'", "''") + "'"


def _create_view(con, name: str, path: str) -> None:
    # `name` is an internal constant and `path` is quote-escaped via
    # _sql_literal(), so no user-controlled input reaches this statement.
    con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({_sql_literal(path)})")  # nosec B608


def connect(layout: WarehouseLayout) -> duckdb.DuckDBPyConnection:
    """Return an in-memory DuckDB connection with views over the warehouse."""
    con = duckdb.connect(database=":memory:")

    if _glob_has_files(layout.matches_dir):
        _create_view(con, "matches", layout.matches_glob())
    if _glob_has_files(layout.players_dir):
        _create_view(con, "players", layout.players_glob())
    if _glob_has_files(layout.events_dir):
        _create_view(con, "events", layout.events_glob())

    pms = layout.player_match_stats_path()
    if pms.exists():
        _create_view(con, "player_match_stats", pms.as_posix())
    pss = layout.player_season_stats_path()
    if pss.exists():
        _create_view(con, "player_season_stats", pss.as_posix())

    return con


def has_silver(layout: WarehouseLayout) -> bool:
    return _glob_has_files(layout.events_dir)
