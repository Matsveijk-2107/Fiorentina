"""The core guarantees: incremental, idempotent, and correcting re-runs."""

from __future__ import annotations

import duckdb

from conftest import make_match
from football_pipeline.config import layout_for
from football_pipeline.ingest import run_ingest


def _events_for(layout, match_id: int) -> int:
    f = layout.events_dir / f"{match_id}.parquet"
    return duckdb.sql(f"SELECT COUNT(*) FROM read_parquet('{f.as_posix()}')").fetchone()[0]


def _score_for(layout, match_id: int) -> tuple[int, int]:
    f = layout.matches_dir / f"{match_id}.parquet"
    row = duckdb.sql(
        f"SELECT score_home, score_away FROM read_parquet('{f.as_posix()}')"
    ).fetchone()
    return row[0], row[1]


def test_initial_then_idempotent(tmp_path, match_builder, writer):
    raw = tmp_path / "raw"
    writer(raw, make_match(1, home_goals=1))
    writer(raw, make_match(2, home_goals=0, away_goals=2))
    layout = layout_for(tmp_path / "wh")

    r1 = run_ingest([raw], layout)
    assert sorted(r1.processed) == [1, 2]
    assert r1.skipped_unchanged == []

    # Second identical run = zero work.
    r2 = run_ingest([raw], layout)
    assert r2.processed == []
    assert sorted(r2.skipped_unchanged) == [1, 2]
    assert r2.silver_changed is False


def test_only_changed_match_is_reprocessed(tmp_path, match_builder, writer):
    raw = tmp_path / "raw"
    writer(raw, make_match(1, home_goals=1))
    writer(raw, make_match(2, home_goals=1))
    layout = layout_for(tmp_path / "wh")
    run_ingest([raw], layout)

    # Change only match 2 (add a pass -> different bytes).
    writer(raw, make_match(2, home_goals=1, extra_pass=True))
    r = run_ingest([raw], layout)
    assert r.processed == [2]
    assert r.skipped_unchanged == [1]


def test_corrected_match_in_update_batch_overrides_initial(tmp_path, match_builder, writer):
    raw = tmp_path / "raw"
    upd = tmp_path / "raw_update"
    writer(raw, make_match(100, home_goals=2, away_goals=2))  # original
    layout = layout_for(tmp_path / "wh")
    run_ingest([raw], layout)
    assert _score_for(layout, 100) == (2, 2)

    # Corrected re-export with an extra home goal lives in the update batch.
    writer(upd, make_match(100, home_goals=3, away_goals=2, corrected=True))
    r = run_ingest([raw, upd], layout)  # precedence: upd wins

    assert 100 in r.processed
    assert _score_for(layout, 100) == (3, 2)  # correction applied in place
    # The initial raw/100 must be recorded as shadowed (superseded, not written).
    assert any(s.endswith("raw/100.json") for s in r.shadowed)


def test_ownership_is_deterministic_regardless_of_dir_order(tmp_path, writer):
    """raw_update always wins for a shared match, because it is later in the list."""
    raw = tmp_path / "raw"
    upd = tmp_path / "raw_update"
    writer(raw, make_match(7, home_goals=1))
    writer(upd, make_match(7, home_goals=4, corrected=True))
    layout = layout_for(tmp_path / "wh")

    run_ingest([raw, upd], layout)
    assert _score_for(layout, 7) == (4, 0)


def test_full_refresh_rebuilds(tmp_path, writer):
    raw = tmp_path / "raw"
    writer(raw, make_match(1, home_goals=1))
    layout = layout_for(tmp_path / "wh")
    run_ingest([raw], layout)
    r = run_ingest([raw], layout, full_refresh=True)
    assert r.processed == [1]  # everything reprocessed after a wipe


def test_removed_source_drops_match(tmp_path, writer):
    raw = tmp_path / "raw"
    p1 = writer(raw, make_match(1, home_goals=1))
    writer(raw, make_match(2, home_goals=1))
    layout = layout_for(tmp_path / "wh")
    run_ingest([raw], layout)

    p1.unlink()  # match 1 source disappears
    r = run_ingest([raw], layout)
    assert r.removed == [1]
    assert not (layout.events_dir / "1.parquet").exists()


def test_invalid_file_is_reported_and_does_not_corrupt_warehouse(tmp_path, writer):
    raw = tmp_path / "raw"
    writer(raw, make_match(1, home_goals=1))
    bad = make_match(2, home_goals=1)
    bad["events"][0]["x"] = 999.0  # out of range -> ValidationError
    writer(raw, bad)
    layout = layout_for(tmp_path / "wh")

    r = run_ingest([raw], layout)
    assert r.processed == [1]  # the good match still landed
    assert any("events[" in err or "out of range" in err for _, err in r.failed)
    assert not (layout.events_dir / "2.parquet").exists()  # bad match never written


def test_unreadable_owner_does_not_delete_existing_output(tmp_path, writer):
    """A transient read error must not drop a match whose source still exists."""
    raw = tmp_path / "raw"
    p = writer(raw, make_match(5, home_goals=1))
    layout = layout_for(tmp_path / "wh")
    run_ingest([raw], layout)
    assert (layout.events_dir / "5.parquet").exists()

    # Corrupt the file so its match_id can't be peeked (simulated transient error).
    p.write_text("{ this is not valid json", encoding="utf-8")
    r = run_ingest([raw], layout)

    assert r.removed == []  # NOT deleted, because the source path still exists
    assert (layout.events_dir / "5.parquet").exists()  # prior good output preserved
