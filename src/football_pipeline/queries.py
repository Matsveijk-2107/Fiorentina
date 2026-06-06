"""Named analytical queries: the questions you can ask the data.

Each public runner returns a materialised :class:`QueryResult` (columns + rows)
with the connection already closed, so callers never leak DuckDB handles.
Parameters go through DuckDB's ``$name`` binding rather than string
interpolation, so user input cannot be injected into SQL.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from . import warehouse_db
from .config import WarehouseLayout


@dataclass(frozen=True)
class NamedQuery:
    name: str
    description: str
    runner: Callable[[duckdb.DuckDBPyConnection, dict], duckdb.DuckDBPyRelation]


def _matches_per_competition(con, params):
    return con.sql(
        """
        SELECT competition, season,
               COUNT(*) AS matches,
               SUM(score_home + score_away) AS total_goals
        FROM matches
        GROUP BY competition, season
        ORDER BY competition, season
        """
    )


def _goals_per_competition(con, params):
    return con.sql(
        """
        SELECT m.competition,
               COUNT(*) FILTER (WHERE e.type = 'shot' AND e.outcome = 'goal') AS goals,
               COUNT(*) FILTER (WHERE e.type = 'shot')                        AS shots,
               ROUND(AVG(e.xg) FILTER (WHERE e.type = 'shot'), 3)             AS avg_xg_per_shot
        FROM events e
        JOIN matches m USING (match_id)
        GROUP BY m.competition
        ORDER BY goals DESC
        """
    )


def _top_scorers(con, params):
    con.execute("SET VARIABLE lim = $limit", {"limit": params.get("limit", 10)})
    rel = con.sql(
        """
        SELECT name, competition, season,
               SUM(goals) AS goals,
               ROUND(SUM(xg), 2) AS xg,
               ROUND(SUM(goals) - SUM(xg), 2) AS goals_minus_xg,
               COUNT(DISTINCT match_id) AS matches
        FROM player_match_stats
        GROUP BY player_id, name, competition, season
        HAVING SUM(goals) > 0
        ORDER BY goals DESC, xg DESC
        LIMIT getvariable('lim')
        """
    )
    return rel


def _top_xg(con, params):
    con.execute("SET VARIABLE lim = $limit", {"limit": params.get("limit", 10)})
    con.execute("SET VARIABLE mins = $min_shots", {"min_shots": params.get("min_shots", 3)})
    # Volume-gate the leaderboard: ranking by cumulative xG without a shot floor
    # lets a single high-xG chance outrank a striker with a season of shots.
    return con.sql(
        """
        SELECT name, competition, season,
               ROUND(SUM(xg), 2) AS xg,
               SUM(goals) AS goals,
               SUM(shots) AS shots
        FROM player_match_stats
        GROUP BY player_id, name, competition, season
        HAVING SUM(shots) >= getvariable('mins')
        ORDER BY xg DESC
        LIMIT getvariable('lim')
        """
    )


def _best_pass_completion(con, params):
    con.execute("SET VARIABLE minp = $min_passes", {"min_passes": params.get("min_passes", 30)})
    con.execute("SET VARIABLE lim = $limit", {"limit": params.get("limit", 10)})
    return con.sql(
        """
        SELECT name, competition, season,
               SUM(passes) AS passes,
               SUM(passes_completed) AS completed,
               CASE WHEN SUM(passes) > 0
                    THEN ROUND(100.0 * SUM(passes_completed) / SUM(passes), 1) END
                    AS pass_completion_pct
        FROM player_match_stats
        GROUP BY player_id, name, competition, season
        HAVING SUM(passes) >= getvariable('minp') AND SUM(passes) > 0
        ORDER BY pass_completion_pct DESC
        LIMIT getvariable('lim')
        """
    )


def _top_tacklers(con, params):
    con.execute("SET VARIABLE minp = $min_tackles", {"min_tackles": params.get("min_tackles", 10)})
    con.execute("SET VARIABLE lim = $limit", {"limit": params.get("limit", 10)})
    return con.sql(
        """
        SELECT name, competition, season,
               SUM(tackles) AS tackles,
               SUM(tackles_won) AS tackles_won,
               CASE WHEN SUM(tackles) > 0
                    THEN ROUND(100.0 * SUM(tackles_won) / SUM(tackles), 1) END
                    AS tackle_win_pct
        FROM player_match_stats
        GROUP BY player_id, name, competition, season
        HAVING SUM(tackles) >= getvariable('minp') AND SUM(tackles) > 0
        ORDER BY tackle_win_pct DESC, tackles_won DESC
        LIMIT getvariable('lim')
        """
    )


def _match_summary(con, params):
    if params.get("match_id") is None:
        raise ValueError("match_summary requires a 'match_id' parameter (--match-id)")
    con.execute("SET VARIABLE mid = $match_id", {"match_id": params.get("match_id")})
    return con.sql(
        """
        SELECT m.match_id, m.competition, m.season, m.match_date,
               m.home_team_name || ' ' || m.score_home || '-' || m.score_away
                   || ' ' || m.away_team_name AS result,
               m.corrected,
               (SELECT COUNT(*) FROM events e WHERE e.match_id = m.match_id) AS events
        FROM matches m
        WHERE m.match_id = getvariable('mid')
        """
    )


def _data_quality_goals_vs_score(con, params):
    """Independent re-check: goals in events must reconcile with final score."""
    return con.sql(
        """
        WITH goal_counts AS (
            SELECT match_id, team_id,
                   COUNT(*) FILTER (WHERE type = 'shot' AND outcome = 'goal') AS goals
            FROM events GROUP BY match_id, team_id
        )
        SELECT m.match_id, m.competition,
               m.score_home, COALESCE(gh.goals, 0) AS events_home_goals,
               m.score_away, COALESCE(ga.goals, 0) AS events_away_goals,
               (m.score_home = COALESCE(gh.goals, 0)
                AND m.score_away = COALESCE(ga.goals, 0)) AS reconciles
        FROM matches m
        LEFT JOIN goal_counts gh ON gh.match_id = m.match_id AND gh.team_id = m.home_team_id
        LEFT JOIN goal_counts ga ON ga.match_id = m.match_id AND ga.team_id = m.away_team_id
        ORDER BY reconciles, m.match_id
        """
    )


REGISTRY: dict[str, NamedQuery] = {
    q.name: q
    for q in [
        NamedQuery(
            "matches_per_competition",
            "Match & goal counts per competition/season",
            _matches_per_competition,
        ),
        NamedQuery(
            "goals_per_competition",
            "Goals, shots and avg xG per competition",
            _goals_per_competition,
        ),
        NamedQuery("top_scorers", "Top scorers (params: limit)", _top_scorers),
        NamedQuery("top_xg", "Highest cumulative xG (params: limit, min_shots)", _top_xg),
        NamedQuery(
            "best_pass_completion",
            "Best passers (params: min_passes, limit)",
            _best_pass_completion,
        ),
        NamedQuery(
            "top_tacklers", "Best tacklers by win % (params: min_tackles, limit)", _top_tacklers
        ),
        NamedQuery("match_summary", "One match summary (params: match_id)", _match_summary),
        NamedQuery(
            "data_quality_goals_vs_score",
            "Reconcile event goals vs final score",
            _data_quality_goals_vs_score,
        ),
    ]
}


@dataclass(frozen=True)
class QueryResult:
    """Materialised query result. Owns no connection (already closed)."""

    columns: list[str]
    rows: list[tuple]

    def fetchall(self) -> list[tuple]:
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _materialise(rel: duckdb.DuckDBPyRelation) -> QueryResult:
    return QueryResult(columns=list(rel.columns), rows=rel.fetchall())


def run_named(layout: WarehouseLayout, name: str, params: dict | None = None) -> QueryResult:
    if name not in REGISTRY:
        raise KeyError(f"unknown query '{name}'. Available: {', '.join(sorted(REGISTRY))}")
    con = warehouse_db.connect(layout)
    try:
        return _materialise(REGISTRY[name].runner(con, params or {}))
    finally:
        con.close()


def run_sql(layout: WarehouseLayout, sql: str) -> QueryResult:
    con = warehouse_db.connect(layout)
    try:
        return _materialise(con.sql(sql))
    finally:
        con.close()
