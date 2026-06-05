"""Independent post-write verification of warehouse invariants."""

from __future__ import annotations

from conftest import make_match, write_match
from football_pipeline.config import layout_for
from football_pipeline.ingest import run_ingest
from football_pipeline.verify import run_verify


def _built(tmp_path):
    raw = tmp_path / "raw"
    write_match(raw, make_match(1, home_goals=2, away_goals=1))
    write_match(raw, make_match(2, home_goals=0, away_goals=0))
    layout = layout_for(tmp_path / "wh")
    run_ingest([raw], layout)
    return layout


def test_verify_passes_on_a_clean_warehouse(tmp_path):
    report = run_verify(_built(tmp_path))
    assert report.ok, report.problems


def test_verify_flags_an_empty_warehouse(tmp_path):
    report = run_verify(layout_for(tmp_path / "empty"))
    assert not report.ok


def test_verify_catches_an_inconsistency(tmp_path):
    layout = _built(tmp_path)
    # Drop match 1's events: its stored score (2-1) no longer reconciles.
    (layout.events_dir / "1.parquet").unlink()
    report = run_verify(layout)
    assert not report.ok
    assert any("reconcile" in p for p in report.problems)
