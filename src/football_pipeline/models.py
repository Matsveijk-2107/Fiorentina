"""Typed domain model for a match and its parts.

These dataclasses are the cleaned, typed representation produced by the
validation layer. They are deliberately flat (one row per record) so they map
straight onto columnar Parquet tables. All construction goes through
:mod:`validation`; nothing here trusts raw input.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EventType(str, Enum):
    # "pass" is a football event type, not a credential (avoids a B105 false positive)
    PASS = "pass"  # nosec B105
    SHOT = "shot"
    TACKLE = "tackle"
    DRIBBLE = "dribble"
    FOUL = "foul"


# Allowed outcomes per event type, straight from SCHEMA.md. Used by validation
# to reject anything the schema does not permit.
ALLOWED_OUTCOMES: dict[EventType, frozenset[str]] = {
    EventType.PASS: frozenset({"complete", "incomplete"}),
    EventType.SHOT: frozenset({"goal", "saved", "off_target", "blocked"}),
    EventType.TACKLE: frozenset({"won", "lost"}),
    EventType.DRIBBLE: frozenset({"complete", "incomplete"}),
    EventType.FOUL: frozenset({"committed", "won"}),
}

VALID_POSITIONS = frozenset({"GK", "DF", "MF", "FW", "SUB"})


@dataclass(frozen=True, slots=True)
class Player:
    match_id: int
    player_id: int
    name: str
    team_id: int
    position: str
    minutes: int


@dataclass(frozen=True, slots=True)
class Event:
    match_id: int
    event_id: int  # unique within a match, NOT globally -> key is (match_id, event_id)
    minute: int
    second: int
    team_id: int
    player_id: int
    x: float
    y: float
    type: str
    outcome: str | None
    recipient_id: int | None  # passes only
    xg: float | None  # shots only


@dataclass(frozen=True, slots=True)
class Match:
    match_id: int
    competition: str
    season: str
    match_date: str  # ISO YYYY-MM-DD (kept as string; DuckDB casts when needed)
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    score_home: int
    score_away: int
    corrected: bool


@dataclass(frozen=True, slots=True)
class ParsedMatch:
    """A fully validated match plus provenance for incremental processing."""

    match: Match
    players: tuple[Player, ...]
    events: tuple[Event, ...]
    source_path: str  # repo-relative source file
    content_hash: str  # sha256 of the source bytes

    @property
    def match_id(self) -> int:
        return self.match.match_id
