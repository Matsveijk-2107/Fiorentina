"""Gold layer: per-player metrics derived from silver via DuckDB SQL.

Two tables are produced. ``player_match_stats`` has one row per (match, player)
and holds the raw building blocks. ``player_season_stats`` aggregates those per
(player, competition, season) and adds per-90 rates, so players with different
minutes can be compared.

The three headline metrics from the theory write-up all live here: finishing
(xG, goals, goals minus xG), passing (completion percentage and volume), and
defensive contribution (tackle win percentage, tackles per 90).

Gold is recomputed only when silver changes, which the caller decides, so a
no-op ingest run does no analytical work either.
"""

from __future__ import annotations

import pyarrow.parquet as pq

from . import warehouse_db
from .config import WarehouseLayout
from .logging_utils import get_logger

log = get_logger("metrics")

# Minutes guard for per-90 rates: players with almost no pitch time produce
# meaningless rates, so per-90 columns are NULL below this threshold.
_MIN_MINUTES_FOR_RATE = 30

_PLAYER_MATCH_SQL = """
WITH ev AS (
    SELECT
        match_id,
        player_id,
        COUNT(*) FILTER (WHERE type = 'shot' AND outcome = 'goal')          AS goals,
        COUNT(*) FILTER (WHERE type = 'shot')                                AS shots,
        COUNT(*) FILTER (WHERE type = 'shot'
                         AND outcome IN ('goal', 'saved'))                   AS shots_on_target,
        COALESCE(SUM(xg) FILTER (WHERE type = 'shot'), 0.0)                  AS xg,
        COUNT(*) FILTER (WHERE type = 'pass')                                AS passes,
        COUNT(*) FILTER (WHERE type = 'pass' AND outcome = 'complete')       AS passes_completed,
        COUNT(*) FILTER (WHERE type = 'tackle')                              AS tackles,
        COUNT(*) FILTER (WHERE type = 'tackle' AND outcome = 'won')          AS tackles_won,
        COUNT(*) FILTER (WHERE type = 'dribble')                             AS dribbles,
        COUNT(*) FILTER (WHERE type = 'dribble' AND outcome = 'complete')    AS dribbles_completed,
        COUNT(*) FILTER (WHERE type = 'foul' AND outcome = 'committed')      AS fouls_committed,
        COUNT(*) FILTER (WHERE type = 'foul' AND outcome = 'won')            AS fouls_won
    FROM events
    GROUP BY match_id, player_id
)
SELECT
    p.match_id,
    p.player_id,
    p.name,
    p.team_id,
    p.position,
    p.minutes,
    m.competition,
    m.season,
    m.match_date,
    COALESCE(ev.goals, 0)              AS goals,
    COALESCE(ev.shots, 0)              AS shots,
    COALESCE(ev.shots_on_target, 0)    AS shots_on_target,
    ROUND(COALESCE(ev.xg, 0.0), 4)     AS xg,
    ROUND(COALESCE(ev.goals, 0) - COALESCE(ev.xg, 0.0), 4) AS goals_minus_xg,
    COALESCE(ev.passes, 0)             AS passes,
    COALESCE(ev.passes_completed, 0)   AS passes_completed,
    CASE WHEN COALESCE(ev.passes, 0) > 0
         THEN ROUND(100.0 * ev.passes_completed / ev.passes, 1) END AS pass_completion_pct,
    COALESCE(ev.tackles, 0)            AS tackles,
    COALESCE(ev.tackles_won, 0)        AS tackles_won,
    CASE WHEN COALESCE(ev.tackles, 0) > 0
         THEN ROUND(100.0 * ev.tackles_won / ev.tackles, 1) END AS tackle_win_pct,
    COALESCE(ev.dribbles, 0)           AS dribbles,
    COALESCE(ev.dribbles_completed, 0) AS dribbles_completed,
    COALESCE(ev.fouls_committed, 0)    AS fouls_committed,
    COALESCE(ev.fouls_won, 0)          AS fouls_won
FROM players p
JOIN matches m USING (match_id)
LEFT JOIN ev ON ev.match_id = p.match_id AND ev.player_id = p.player_id
ORDER BY p.match_id, p.player_id
"""

_PLAYER_SEASON_SQL = """
WITH base AS (
    SELECT * FROM player_match_stats
)
SELECT
    player_id,
    competition,
    season,
    -- a player can change clubs; report the most recent name/team seen.
    -- tie-break same-date matches by match_id so the pick is deterministic.
    arg_max(name, (match_date, match_id))    AS name,
    arg_max(team_id, (match_date, match_id)) AS team_id,
    COUNT(DISTINCT match_id)     AS matches_played,
    SUM(minutes)                 AS minutes,
    SUM(goals)                   AS goals,
    ROUND(SUM(xg), 3)            AS xg,
    ROUND(SUM(goals) - SUM(xg), 3) AS goals_minus_xg,
    SUM(shots)                   AS shots,
    CASE WHEN SUM(shots) > 0
         THEN ROUND(100.0 * SUM(goals) / SUM(shots), 1) END AS shot_conversion_pct,
    SUM(passes)                  AS passes,
    SUM(passes_completed)        AS passes_completed,
    CASE WHEN SUM(passes) > 0
         THEN ROUND(100.0 * SUM(passes_completed) / SUM(passes), 1) END AS pass_completion_pct,
    SUM(tackles)                 AS tackles,
    SUM(tackles_won)             AS tackles_won,
    CASE WHEN SUM(tackles) > 0
         THEN ROUND(100.0 * SUM(tackles_won) / SUM(tackles), 1) END AS tackle_win_pct,
    -- per-90 rates (NULL when sample of minutes is too small to be meaningful)
    CASE WHEN SUM(minutes) >= getvariable('min_minutes')
         THEN ROUND(90.0 * SUM(goals) / SUM(minutes), 3) END AS goals_per90,
    CASE WHEN SUM(minutes) >= getvariable('min_minutes')
         THEN ROUND(90.0 * SUM(xg) / SUM(minutes), 3) END AS xg_per90,
    CASE WHEN SUM(minutes) >= getvariable('min_minutes')
         THEN ROUND(90.0 * SUM(tackles_won) / SUM(minutes), 3) END AS tackles_won_per90
FROM base
GROUP BY player_id, competition, season
ORDER BY goals DESC, xg DESC
"""


def compute_gold(layout: WarehouseLayout) -> dict[str, int]:
    """(Re)compute gold tables from silver. Returns row counts per table."""
    if not warehouse_db.has_silver(layout):
        log.warning("no silver data found; skipping gold computation")
        return {}

    layout.gold_dir.mkdir(parents=True, exist_ok=True)
    con = warehouse_db.connect(layout)
    try:
        pms = con.execute(_PLAYER_MATCH_SQL).to_arrow_table()
        _atomic_write(pms, layout.player_match_stats_path())
        # Register the freshly computed table so the season query can read it.
        con.register("player_match_stats_tbl", pms)
        con.execute(
            "CREATE OR REPLACE VIEW player_match_stats AS SELECT * FROM player_match_stats_tbl"
        )
        # Bind the per-90 minutes threshold as a variable (no SQL string-building).
        con.execute("SET VARIABLE min_minutes = $m", {"m": _MIN_MINUTES_FOR_RATE})
        pss = con.execute(_PLAYER_SEASON_SQL).to_arrow_table()
        _atomic_write(pss, layout.player_season_stats_path())
    finally:
        con.close()

    counts = {"player_match_stats": pms.num_rows, "player_season_stats": pss.num_rows}
    log.info("gold computed: %s", counts)
    return counts


def _atomic_write(table, path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
