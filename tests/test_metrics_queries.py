"""Gold metrics and named queries on a tiny known dataset."""

from __future__ import annotations

import pytest

from conftest import make_match, write_match
from football_pipeline import queries
from football_pipeline.config import layout_for
from football_pipeline.ingest import run_ingest
from football_pipeline.metrics import compute_gold


@pytest.fixture
def built_warehouse(tmp_path):
    raw = tmp_path / "raw"
    write_match(raw, make_match(1, home_goals=2, away_goals=1))
    write_match(raw, make_match(2, home_goals=0, away_goals=0))
    layout = layout_for(tmp_path / "wh")
    run_ingest([raw], layout)
    compute_gold(layout)
    return layout


def test_player_match_stats_finishing(built_warehouse):
    layout = built_warehouse
    rel = queries.run_sql(
        layout,
        "SELECT goals, shots, xg FROM player_match_stats WHERE match_id = 1 AND player_id = 10",
    )
    goals, shots, xg = rel.fetchone()
    assert goals == 2  # two goal shots
    assert shots == 3  # two goals + one saved
    # xg = 0.5 + 0.5 (goals) + 0.2 (saved) = 1.2
    assert xg == pytest.approx(1.2, abs=1e-6)


def test_pass_completion_metric(built_warehouse):
    rel = queries.run_sql(
        built_warehouse,
        "SELECT passes, passes_completed, pass_completion_pct "
        "FROM player_match_stats WHERE match_id = 1 AND player_id = 11",
    )
    passes, completed, pct = rel.fetchone()
    assert (passes, completed) == (1, 1)
    assert pct == 100.0


def test_top_scorers_query(built_warehouse):
    rel = queries.run_named(built_warehouse, "top_scorers", {"limit": 5})
    rows = rel.fetchall()
    # Home Scorer (2 goals) should rank first.
    assert rows[0][0] == "Home Scorer"
    assert rows[0][3] == 2


def test_data_quality_all_reconcile(built_warehouse):
    rel = queries.run_named(built_warehouse, "data_quality_goals_vs_score", {})
    cols = rel.columns
    idx = cols.index("reconciles")
    assert all(row[idx] for row in rel.fetchall())


def test_match_summary_requires_match_id(built_warehouse):
    with pytest.raises(ValueError, match="requires a 'match_id'"):
        queries.run_named(built_warehouse, "match_summary", {})


def test_goals_minus_xg_sign(built_warehouse):
    # Home Scorer: 2 goals vs 1.2 xG -> overperformance positive.
    rel = queries.run_sql(
        built_warehouse,
        "SELECT goals_minus_xg FROM player_match_stats WHERE match_id=1 AND player_id=10",
    )
    assert rel.fetchone()[0] > 0


def test_top_xg_respects_shot_floor(built_warehouse):
    # An unreachable shot floor leaves the leaderboard empty.
    assert queries.run_named(built_warehouse, "top_xg", {"min_shots": 100}).fetchall() == []
    # A floor of 1 admits players who actually took shots.
    assert queries.run_named(built_warehouse, "top_xg", {"min_shots": 1}).fetchall()
