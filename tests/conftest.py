"""Shared test fixtures: a builder for synthetic-but-valid match JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def make_match(
    match_id: int,
    *,
    competition: str = "Serie A",
    season: str = "2024-2025",
    home_goals: int = 1,
    away_goals: int = 0,
    corrected: bool = False,
    extra_pass: bool = False,
) -> dict:
    """Build a schema-valid match where event goals reconcile with the score.

    Two players per side (one scorer, one passer). ``extra_pass`` adds a pass so
    two otherwise-identical matches hash differently when needed.
    """
    home_id, away_id = 1, 2
    players = [
        {
            "player_id": 10,
            "name": "Home Scorer",
            "team_id": home_id,
            "position": "FW",
            "minutes": 90,
        },
        {
            "player_id": 11,
            "name": "Home Passer",
            "team_id": home_id,
            "position": "MF",
            "minutes": 90,
        },
        {
            "player_id": 20,
            "name": "Away Scorer",
            "team_id": away_id,
            "position": "FW",
            "minutes": 90,
        },
        {
            "player_id": 21,
            "name": "Away Passer",
            "team_id": away_id,
            "position": "MF",
            "minutes": 90,
        },
    ]
    events: list[dict] = []
    eid = 1

    def add(ev: dict) -> None:
        nonlocal eid
        events.append({"event_id": eid, "second": 0, "x": 50.0, "y": 50.0, **ev})
        eid += 1

    # Passes (one completed, one incomplete) for the passers.
    add(
        {
            "minute": 1,
            "team_id": home_id,
            "player_id": 11,
            "type": "pass",
            "outcome": "complete",
            "recipient_id": 10,
        }
    )
    add(
        {
            "minute": 2,
            "team_id": away_id,
            "player_id": 21,
            "type": "pass",
            "outcome": "incomplete",
            "recipient_id": None,
        }
    )
    if extra_pass:
        add(
            {
                "minute": 3,
                "team_id": home_id,
                "player_id": 11,
                "type": "pass",
                "outcome": "complete",
                "recipient_id": 10,
            }
        )

    # Goals matching the requested score.
    for g in range(home_goals):
        add(
            {
                "minute": 10 + g,
                "team_id": home_id,
                "player_id": 10,
                "type": "shot",
                "outcome": "goal",
                "xg": 0.5,
            }
        )
    for g in range(away_goals):
        add(
            {
                "minute": 20 + g,
                "team_id": away_id,
                "player_id": 20,
                "type": "shot",
                "outcome": "goal",
                "xg": 0.4,
            }
        )
    # A non-goal shot + a tackle so other metrics are exercised.
    add(
        {
            "minute": 30,
            "team_id": home_id,
            "player_id": 10,
            "type": "shot",
            "outcome": "saved",
            "xg": 0.2,
        }
    )
    add({"minute": 40, "team_id": away_id, "player_id": 21, "type": "tackle", "outcome": "won"})

    match = {
        "match_id": match_id,
        "competition": competition,
        "season": season,
        "match_date": "2024-09-01",
        "home_team": {"team_id": home_id, "name": "Home FC"},
        "away_team": {"team_id": away_id, "name": "Away FC"},
        "score": {"home": home_goals, "away": away_goals},
        "players": players,
        "events": events,
    }
    if corrected:
        match["corrected"] = True
    return match


def write_match(directory: Path, match: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{match['match_id']}.json"
    path.write_text(json.dumps(match, indent=1), encoding="utf-8")
    return path


@pytest.fixture
def match_builder():
    return make_match


@pytest.fixture
def writer():
    return write_match
