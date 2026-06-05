"""Post-write verification: re-read the warehouse and re-assert its invariants.

Ingest validates input at the boundary, but `verify` is an independent second
opinion that reads what actually landed on disk. It re-checks the properties the
pipeline promises: goals reconcile with the stored score, event ids are unique
within a match, required columns are never null, and the manifest agrees with
the Parquet that's really present. Handy as a post-run smoke test or in CI.

It deliberately recomputes from the written Parquet rather than trusting the
in-memory ingest result, so it would catch a bug in the writer itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import warehouse_db
from .config import WarehouseLayout
from .logging_utils import get_logger
from .state import PipelineState

log = get_logger("verify")


@dataclass
class VerifyReport:
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def run_verify(layout: WarehouseLayout) -> VerifyReport:
    """Re-read the warehouse and check every invariant. Empty problems == clean."""
    report = VerifyReport()
    if not warehouse_db.has_silver(layout):
        report.problems.append("no silver data found; run ingest before verify")
        return report

    con = warehouse_db.connect(layout)
    try:
        # 1) Event goals must reconcile with the stored score, per match.
        unreconciled = con.sql(
            """
            WITH g AS (
                SELECT match_id, team_id,
                       COUNT(*) FILTER (WHERE type = 'shot' AND outcome = 'goal') AS goals
                FROM events GROUP BY match_id, team_id
            )
            SELECT m.match_id
            FROM matches m
            LEFT JOIN g gh ON gh.match_id = m.match_id AND gh.team_id = m.home_team_id
            LEFT JOIN g ga ON ga.match_id = m.match_id AND ga.team_id = m.away_team_id
            WHERE m.score_home <> COALESCE(gh.goals, 0)
               OR m.score_away <> COALESCE(ga.goals, 0)
            ORDER BY m.match_id
            """
        ).fetchall()
        if unreconciled:
            ids = [r[0] for r in unreconciled]
            report.problems.append(
                f"{len(ids)} match(es) where event goals don't reconcile with the score: {ids[:10]}"
            )

        # 2) event_id must be unique within a match.
        dupes = con.sql(
            """
            SELECT match_id
            FROM events
            GROUP BY match_id
            HAVING COUNT(*) <> COUNT(DISTINCT event_id)
            ORDER BY match_id
            """
        ).fetchall()
        if dupes:
            ids = [r[0] for r in dupes]
            report.problems.append(f"{len(ids)} match(es) with duplicate event_id: {ids[:10]}")

        # 3) Required columns must never be null.
        null_row = con.sql(
            """
            SELECT
                (SELECT COUNT(*) FROM events
                 WHERE match_id IS NULL OR event_id IS NULL OR type IS NULL),
                (SELECT COUNT(*) FROM players WHERE match_id IS NULL OR player_id IS NULL),
                (SELECT COUNT(*) FROM matches WHERE match_id IS NULL)
            """
        ).fetchone()
        ev_nulls, pl_nulls, m_nulls = null_row if null_row else (0, 0, 0)
        if ev_nulls or pl_nulls or m_nulls:
            report.problems.append(
                "null values in required columns "
                f"(events={ev_nulls}, players={pl_nulls}, matches={m_nulls})"
            )

        materialised = {r[0] for r in con.sql("SELECT match_id FROM matches").fetchall()}
    finally:
        con.close()

    # 4) The manifest's owner set must match what's actually on disk.
    state = PipelineState.load(layout.state_file)
    owners = {int(mid) for mid in state.match_owner}
    if owners != materialised:
        owned_not_on_disk = sorted(owners - materialised)
        on_disk_not_owned = sorted(materialised - owners)
        report.problems.append(
            "manifest/Parquet mismatch: "
            f"owned but missing on disk={owned_not_on_disk[:10]}, "
            f"on disk but not owned={on_disk_not_owned[:10]}"
        )

    if report.ok:
        log.info("verify: all invariants hold (%d matches)", len(materialised))
    else:
        for problem in report.problems:
            log.error("verify: %s", problem)
    return report
