"""Validation: typed parsing, range/enum enforcement, soft quality warnings."""

from __future__ import annotations

import pytest

from conftest import make_match
from football_pipeline.validation import ValidationError, parse_match


def test_valid_match_parses_with_no_warnings():
    parsed, warnings = parse_match(make_match(1, home_goals=2, away_goals=1), "x.json", "h")
    assert parsed.match.match_id == 1
    assert parsed.match.score_home == 2
    assert len(parsed.players) == 4
    assert parsed.events  # sorted, typed
    assert warnings == []


def test_events_are_sorted_by_time():
    parsed, _ = parse_match(make_match(1, home_goals=3), "x.json", "h")
    times = [(e.minute, e.second, e.event_id) for e in parsed.events]
    assert times == sorted(times)


def test_corrected_flag_is_read():
    parsed, _ = parse_match(make_match(1, corrected=True), "x.json", "h")
    assert parsed.match.corrected is True


def test_score_mismatch_is_a_warning_not_an_error():
    raw = make_match(1, home_goals=2, away_goals=0)
    raw["score"]["home"] = 5  # events still imply 2 -> mismatch
    parsed, warnings = parse_match(raw, "x.json", "h")
    assert parsed is not None
    assert any(w.code == "score_mismatch" for w in warnings)


def test_illegal_outcome_raises():
    raw = make_match(1)
    raw["events"][0]["outcome"] = "teleport"
    with pytest.raises(ValidationError, match="not allowed"):
        parse_match(raw, "x.json", "h")


def test_out_of_range_coordinate_raises():
    raw = make_match(1)
    raw["events"][0]["x"] = 150.0
    with pytest.raises(ValidationError, match="out of range"):
        parse_match(raw, "x.json", "h")


def test_unknown_event_type_raises():
    raw = make_match(1)
    raw["events"][0]["type"] = "teleport"
    with pytest.raises(ValidationError, match="unknown event type"):
        parse_match(raw, "x.json", "h")


def test_missing_required_field_raises():
    raw = make_match(1)
    del raw["score"]
    with pytest.raises(ValidationError, match="missing required field 'score'"):
        parse_match(raw, "x.json", "h")


def test_unknown_position_is_a_warning_not_an_error():
    # SCHEMA.md doesn't enumerate positions, so an unfamiliar code must not
    # reject an otherwise-valid match; it's surfaced as a soft warning instead.
    raw = make_match(1)
    raw["players"][0]["position"] = "WIZARD"
    parsed, warnings = parse_match(raw, "x.json", "h")
    assert parsed is not None
    assert any(w.code == "unknown_position" for w in warnings)


def test_negative_score_raises():
    raw = make_match(1)
    raw["score"]["home"] = -1
    with pytest.raises(ValidationError, match="non-negative"):
        parse_match(raw, "x.json", "h")


def test_non_iso_match_date_raises():
    raw = make_match(1)
    raw["match_date"] = "01/09/2024"
    with pytest.raises(ValidationError, match="not an ISO date"):
        parse_match(raw, "x.json", "h")


def test_xg_out_of_range_raises():
    raw = make_match(1, home_goals=1)
    # find the goal shot and break its xg
    for e in raw["events"]:
        if e.get("type") == "shot":
            e["xg"] = 1.5
            break
    with pytest.raises(ValidationError, match=r"xg: .* out of range"):
        parse_match(raw, "x.json", "h")
