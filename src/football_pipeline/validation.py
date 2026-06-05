"""Boundary validation: raw JSON dict -> typed :class:`ParsedMatch`.

Two classes of problem are treated differently.

Structural errors (a missing or wrong-typed required field, an unknown event
type, out-of-range coordinates, an illegal outcome) raise
:class:`ValidationError`. Malformed data never makes it into the warehouse.

Data-quality warnings, for example goals in the events not reconciling with the
final score, are collected and returned rather than raised. They get reported
but do not block ingestion, which mirrors how a real pipeline quarantines or
logs soft issues instead of dropping the load.

Nothing here mutates the input; typed records are built fresh.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import (
    ALLOWED_OUTCOMES,
    VALID_POSITIONS,
    Event,
    EventType,
    Match,
    ParsedMatch,
    Player,
)

_COORD_MIN, _COORD_MAX = 0.0, 100.0


class ValidationError(ValueError):
    """Raised when a match file violates the schema in a non-recoverable way."""


@dataclass(frozen=True, slots=True)
class DataQualityWarning:
    match_id: int
    code: str
    detail: str


# --------------------------------------------------------------------------- #
# Small typed-accessor helpers. Each fails fast with a precise, located message.
# --------------------------------------------------------------------------- #
def _require(obj: dict, key: str, where: str):
    if key not in obj:
        raise ValidationError(f"{where}: missing required field '{key}'")
    return obj[key]


def _as_int(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{where}: expected int, got {type(value).__name__} ({value!r})")
    return value


def _as_float(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{where}: expected number, got {type(value).__name__} ({value!r})")
    return float(value)


def _as_str(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{where}: expected non-empty string, got {value!r}")
    return value


def _iso_date(value, where: str) -> str:
    """Require an ISO ``YYYY-MM-DD`` date.

    Stored as a string, but format-checked because the season aggregation orders
    by ``match_date`` lexicographically (arg_max), which is only correct for ISO
    dates.
    """
    s = _as_str(value, where)
    try:
        from datetime import date

        date.fromisoformat(s)
    except ValueError:
        raise ValidationError(f"{where}: '{s}' is not an ISO date (YYYY-MM-DD)") from None
    return s


def _coord(value, axis: str, where: str) -> float:
    v = _as_float(value, f"{where}.{axis}")
    if not (_COORD_MIN <= v <= _COORD_MAX):
        raise ValidationError(f"{where}.{axis}: {v} out of range [0, 100]")
    return v


# --------------------------------------------------------------------------- #
# Parsers for each section.
# --------------------------------------------------------------------------- #
def _parse_team(raw: dict, side: str) -> tuple[int, str]:
    where = f"{side}_team"
    team = _require(raw, side + "_team", "match")
    if not isinstance(team, dict):
        raise ValidationError(f"{where}: expected object")
    return _as_int(_require(team, "team_id", where), f"{where}.team_id"), _as_str(
        _require(team, "name", where), f"{where}.name"
    )


def _parse_players(raw_players, match_id: int) -> tuple[Player, ...]:
    if not isinstance(raw_players, list) or not raw_players:
        raise ValidationError(f"match {match_id}: 'players' must be a non-empty list")
    out: list[Player] = []
    seen: set[int] = set()
    for i, p in enumerate(raw_players):
        where = f"match {match_id}.players[{i}]"
        if not isinstance(p, dict):
            raise ValidationError(f"{where}: expected object")
        pid = _as_int(_require(p, "player_id", where), f"{where}.player_id")
        if pid in seen:
            raise ValidationError(f"{where}: duplicate player_id {pid}")
        seen.add(pid)
        position = _as_str(_require(p, "position", where), f"{where}.position")
        if position not in VALID_POSITIONS:
            raise ValidationError(f"{where}.position: unknown position {position!r}")
        minutes = _as_int(_require(p, "minutes", where), f"{where}.minutes")
        if not (0 <= minutes <= 130):  # generous upper bound incl. extra time
            raise ValidationError(f"{where}.minutes: {minutes} out of range")
        out.append(
            Player(
                match_id=match_id,
                player_id=pid,
                name=_as_str(_require(p, "name", where), f"{where}.name"),
                team_id=_as_int(_require(p, "team_id", where), f"{where}.team_id"),
                position=position,
                minutes=minutes,
            )
        )
    return tuple(out)


def _parse_event(raw: dict, match_id: int, idx: int) -> Event:
    where = f"match {match_id}.events[{idx}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"{where}: expected object")

    raw_type = _as_str(_require(raw, "type", where), f"{where}.type")
    try:
        etype = EventType(raw_type)
    except ValueError:
        raise ValidationError(f"{where}.type: unknown event type {raw_type!r}") from None

    outcome = raw.get("outcome")
    if outcome is not None:
        outcome = _as_str(outcome, f"{where}.outcome")
        if outcome not in ALLOWED_OUTCOMES[etype]:
            raise ValidationError(
                f"{where}.outcome: {outcome!r} not allowed for type {etype.value!r} "
                f"(expected one of {sorted(ALLOWED_OUTCOMES[etype])})"
            )

    # Type-specific optional fields.
    recipient_id = None
    xg = None
    if etype is EventType.PASS:
        rid = raw.get("recipient_id")
        recipient_id = None if rid is None else _as_int(rid, f"{where}.recipient_id")
    if etype is EventType.SHOT:
        raw_xg = raw.get("xg")
        if raw_xg is not None:
            xg = _as_float(raw_xg, f"{where}.xg")
            if not (0.0 <= xg <= 1.0):
                raise ValidationError(f"{where}.xg: {xg} out of range [0, 1]")

    minute = _as_int(_require(raw, "minute", where), f"{where}.minute")
    if minute < 0:
        raise ValidationError(f"{where}.minute: {minute} must be >= 0")
    second = _as_int(_require(raw, "second", where), f"{where}.second")
    if not (0 <= second < 60):
        raise ValidationError(f"{where}.second: {second} out of range [0, 59]")

    return Event(
        match_id=match_id,
        event_id=_as_int(_require(raw, "event_id", where), f"{where}.event_id"),
        minute=minute,
        second=second,
        team_id=_as_int(_require(raw, "team_id", where), f"{where}.team_id"),
        player_id=_as_int(_require(raw, "player_id", where), f"{where}.player_id"),
        x=_coord(_require(raw, "x", where), "x", where),
        y=_coord(_require(raw, "y", where), "y", where),
        type=etype.value,
        outcome=outcome,
        recipient_id=recipient_id,
        xg=xg,
    )


def _parse_events(raw_events, match_id: int) -> tuple[Event, ...]:
    if not isinstance(raw_events, list):
        raise ValidationError(f"match {match_id}: 'events' must be a list")
    events = [_parse_event(e, match_id, i) for i, e in enumerate(raw_events)]
    seen_ids: set[int] = set()
    for e in events:
        if e.event_id in seen_ids:
            raise ValidationError(f"match {match_id}: duplicate event_id {e.event_id}")
        seen_ids.add(e.event_id)
    # Persist deterministically ordered by (minute, second, event_id).
    events.sort(key=lambda e: (e.minute, e.second, e.event_id))
    return tuple(events)


# --------------------------------------------------------------------------- #
# Public API.
# --------------------------------------------------------------------------- #
def parse_match(
    raw: dict, source_path: str, content_hash: str
) -> tuple[ParsedMatch, list[DataQualityWarning]]:
    """Validate a raw match dict and return a typed match + soft warnings."""
    if not isinstance(raw, dict):
        raise ValidationError(f"{source_path}: top-level JSON must be an object")

    match_id = _as_int(_require(raw, "match_id", "match"), "match.match_id")
    home_id, home_name = _parse_team(raw, "home")
    away_id, away_name = _parse_team(raw, "away")

    score = _require(raw, "score", f"match {match_id}")
    if not isinstance(score, dict):
        raise ValidationError(f"match {match_id}.score: expected object")
    score_home = _as_int(_require(score, "home", f"match {match_id}.score"), "score.home")
    score_away = _as_int(_require(score, "away", f"match {match_id}.score"), "score.away")
    if score_home < 0 or score_away < 0:
        raise ValidationError(f"match {match_id}.score: scores must be non-negative")

    match = Match(
        match_id=match_id,
        competition=_as_str(_require(raw, "competition", f"match {match_id}"), "competition"),
        season=_as_str(_require(raw, "season", f"match {match_id}"), "season"),
        match_date=_iso_date(
            _require(raw, "match_date", f"match {match_id}"), f"match {match_id}.match_date"
        ),
        home_team_id=home_id,
        home_team_name=home_name,
        away_team_id=away_id,
        away_team_name=away_name,
        score_home=score_home,
        score_away=score_away,
        corrected=bool(raw.get("corrected", False)),
    )

    players = _parse_players(_require(raw, "players", f"match {match_id}"), match_id)
    events = _parse_events(_require(raw, "events", f"match {match_id}"), match_id)

    warnings = _quality_checks(match, players, events)
    return (
        ParsedMatch(
            match=match,
            players=players,
            events=events,
            source_path=source_path,
            content_hash=content_hash,
        ),
        warnings,
    )


def parse_file(
    path: Path, repo_relative: str, content_hash: str
) -> tuple[ParsedMatch, list[DataQualityWarning]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{repo_relative}: invalid JSON ({exc})") from exc
    return parse_match(raw, repo_relative, content_hash)


def _quality_checks(
    match: Match, players: tuple[Player, ...], events: tuple[Event, ...]
) -> list[DataQualityWarning]:
    """Non-fatal reconciliation checks surfaced to the operator."""
    warnings: list[DataQualityWarning] = []

    # 1) Goals in events should reconcile with the final score.
    goals: dict[int, int] = {}
    for e in events:
        if e.type == EventType.SHOT.value and e.outcome == "goal":
            goals[e.team_id] = goals.get(e.team_id, 0) + 1
    gh, ga = goals.get(match.home_team_id, 0), goals.get(match.away_team_id, 0)
    if (gh, ga) != (match.score_home, match.score_away):
        warnings.append(
            DataQualityWarning(
                match.match_id,
                "score_mismatch",
                f"events imply {gh}-{ga} but score says {match.score_home}-{match.score_away}",
            )
        )

    # 2) Every event actor should appear in the match's player list.
    roster = {p.player_id for p in players}
    orphan = sorted({e.player_id for e in events if e.player_id not in roster})
    if orphan:
        warnings.append(
            DataQualityWarning(
                match.match_id, "orphan_event_player", f"player_ids not in roster: {orphan[:10]}"
            )
        )

    return warnings
