"""Manifest persistence: round-trip and forward-compatibility."""

from __future__ import annotations

import json

from football_pipeline.state import PipelineState, SourceRecord


def test_round_trip(tmp_path):
    path = tmp_path / "manifest.json"
    state = PipelineState()
    state.sources["raw/1.json"] = SourceRecord(
        sha256="abc", match_id=1, is_owner=True, n_events=10, n_players=4, ingested_at="t"
    )
    state.match_owner["1"] = "raw/1.json"
    state.stamp_run()
    state.save(path)

    loaded = PipelineState.load(path)
    assert loaded.match_owner == {"1": "raw/1.json"}
    assert loaded.sources["raw/1.json"].sha256 == "abc"
    assert loaded.runs == 1


def test_newer_version_rebuilds_instead_of_crashing(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 999, "runs": 7, "sources": {}}), encoding="utf-8")
    loaded = PipelineState.load(path)
    # Falls back to a clean state rather than trusting an incompatible manifest.
    assert loaded.runs == 0
    assert loaded.sources == {}


def test_unknown_source_fields_are_ignored(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {
                    "raw/1.json": {
                        "sha256": "h",
                        "match_id": 1,
                        "is_owner": True,
                        "n_events": 1,
                        "n_players": 1,
                        "ingested_at": "t",
                        "field_from_the_future": "x",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = PipelineState.load(path)  # must not raise on the unknown field
    assert loaded.sources["raw/1.json"].match_id == 1
