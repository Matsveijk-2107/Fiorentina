"""End-to-end CLI tests driving `main()` exactly as a user would.

These exercise the full surface against a temp warehouse: argument parsing,
ingest, gold, every named query, output formats, status, and the error paths.
"""

from __future__ import annotations

import json

import pytest

from conftest import make_match, write_match
from football_pipeline import queries
from football_pipeline.cli import main


@pytest.fixture
def populated(tmp_path):
    """A raw dir + warehouse path, ingested via the CLI."""
    raw = tmp_path / "raw"
    write_match(raw, make_match(1, home_goals=2, away_goals=1))
    write_match(raw, make_match(2, home_goals=0, away_goals=3))
    wh = tmp_path / "wh"
    rc = main(["--warehouse", str(wh), "run", "--source", str(raw)])
    assert rc == 0
    return raw, wh


def test_run_then_idempotent_rerun(tmp_path, capsys):
    raw = tmp_path / "raw"
    write_match(raw, make_match(1, home_goals=1))
    wh = tmp_path / "wh"

    assert main(["--warehouse", str(wh), "run", "--source", str(raw)]) == 0
    assert "processed 1" in capsys.readouterr().out

    assert main(["--warehouse", str(wh), "run", "--source", str(raw)]) == 0
    assert "processed 0" in capsys.readouterr().out


def test_query_before_ingest_returns_2(tmp_path, capsys):
    wh = tmp_path / "empty"
    assert main(["--warehouse", str(wh), "query", "top_scorers"]) == 2
    assert "Run" in capsys.readouterr().err


def test_all_named_queries_run(populated, capsys):
    _, wh = populated
    for name in queries.REGISTRY:
        params = ["--match-id", "1"] if name == "match_summary" else []
        rc = main(["--warehouse", str(wh), "query", name, *params])
        assert rc == 0, name
        assert capsys.readouterr().out.strip()  # produced some output


def test_query_json_format_is_valid(populated, capsys):
    _, wh = populated
    rc = main(["--warehouse", str(wh), "query", "matches_per_competition", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and payload


def test_query_csv_format(populated, capsys):
    _, wh = populated
    rc = main(["--warehouse", str(wh), "query", "top_scorers", "--format", "csv"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("name,competition")


def test_raw_sql_query(populated, capsys):
    _, wh = populated
    rc = main(["--warehouse", str(wh), "query", "--sql", "SELECT COUNT(*) AS n FROM matches"])
    assert rc == 0
    assert "2" in capsys.readouterr().out


def test_unknown_named_query_returns_2(populated, capsys):
    _, wh = populated
    assert main(["--warehouse", str(wh), "query", "does_not_exist"]) == 2
    assert "unknown query" in capsys.readouterr().err


def test_match_summary_without_id_returns_2(populated, capsys):
    _, wh = populated
    assert main(["--warehouse", str(wh), "query", "match_summary"]) == 2
    assert "match_id" in capsys.readouterr().err


def test_status_and_queries_commands(populated, capsys):
    _, wh = populated
    assert main(["--warehouse", str(wh), "status"]) == 0
    assert "Matches:" in capsys.readouterr().out
    assert main(["--warehouse", str(wh), "queries"]) == 0
    assert "top_scorers" in capsys.readouterr().out


def test_full_refresh_and_no_metrics(populated, capsys):
    raw, wh = populated
    rc = main(
        ["--warehouse", str(wh), "run", "--source", str(raw), "--full-refresh", "--no-metrics"]
    )
    assert rc == 0
    assert "processed 2" in capsys.readouterr().out


def test_metrics_subcommand(populated, capsys):
    _, wh = populated
    assert main(["--warehouse", str(wh), "metrics"]) == 0
    assert "Gold recomputed" in capsys.readouterr().out
