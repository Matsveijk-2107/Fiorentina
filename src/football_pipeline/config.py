"""Central configuration: paths and layout of the warehouse.

Everything derived (Parquet tables, manifest) lives under a single warehouse
directory so the whole pipeline output is one disposable, reproducible folder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Repo root = two levels up from this file (src/football_pipeline/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Default raw source batches, in PRECEDENCE ORDER (later wins on conflicts).
# The "update" batch re-exports match 1003 with a correction, so it must
# override the initial batch for that match_id.
DEFAULT_SOURCES: tuple[Path, ...] = (
    REPO_ROOT / "candidate_dataset" / "data" / "raw",
    REPO_ROOT / "candidate_dataset" / "data" / "raw_update",
)

DEFAULT_WAREHOUSE = REPO_ROOT / "warehouse"


@dataclass(frozen=True)
class WarehouseLayout:
    """Resolved paths for one warehouse root.

    Silver tables hold one Parquet file per match (``<match_id>.parquet``) so a
    single changed match rewrites exactly one small file. Gold tables are
    derived analytics recomputed when silver changes.
    """

    root: Path
    matches_dir: Path = field(init=False)
    players_dir: Path = field(init=False)
    events_dir: Path = field(init=False)
    gold_dir: Path = field(init=False)
    state_file: Path = field(init=False)

    def __post_init__(self) -> None:
        # frozen dataclass: use object.__setattr__ for derived fields.
        object.__setattr__(self, "matches_dir", self.root / "matches")
        object.__setattr__(self, "players_dir", self.root / "players")
        object.__setattr__(self, "events_dir", self.root / "events")
        object.__setattr__(self, "gold_dir", self.root / "gold")
        object.__setattr__(self, "state_file", self.root / "_state" / "manifest.json")

    @property
    def silver_dirs(self) -> tuple[Path, ...]:
        return (self.matches_dir, self.players_dir, self.events_dir)

    def ensure_dirs(self) -> None:
        for d in (*self.silver_dirs, self.gold_dir, self.state_file.parent):
            d.mkdir(parents=True, exist_ok=True)

    # --- Parquet glob helpers (forward slashes: DuckDB-friendly on Windows) ---
    def matches_glob(self) -> str:
        return (self.matches_dir / "*.parquet").as_posix()

    def players_glob(self) -> str:
        return (self.players_dir / "*.parquet").as_posix()

    def events_glob(self) -> str:
        return (self.events_dir / "*.parquet").as_posix()

    def player_match_stats_path(self) -> Path:
        return self.gold_dir / "player_match_stats.parquet"

    def player_season_stats_path(self) -> Path:
        return self.gold_dir / "player_season_stats.parquet"


def layout_for(warehouse: Path | None = None) -> WarehouseLayout:
    return WarehouseLayout(root=Path(warehouse) if warehouse else DEFAULT_WAREHOUSE)
