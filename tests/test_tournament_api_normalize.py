from datetime import UTC
import json
from pathlib import Path

import pytest

from sources.limitless.tournament_api.normalize import (
    NormalizationConflictError,
    PAIRING_COLUMNS,
    PARTICIPANT_COLUMNS,
    TOURNAMENT_COLUMNS,
    normalize_pairings,
    normalize_participants,
    normalize_snapshot,
    normalize_tournaments,
)

FIXTURES = Path(__file__).parent / "fixtures" / "limitless_api"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_normalize_snapshot_produces_frozen_schemas_and_utc_date():
    tournaments, participants, pairings = normalize_snapshot(
        tournament_id="t1",
        raw_snapshot_id="snap1",
        details=load("details.json"),
        standings=load("standings.json"),
        pairings=load("pairings.json"),
    )

    assert tuple(tournaments.columns) == TOURNAMENT_COLUMNS
    assert tuple(participants.columns) == PARTICIPANT_COLUMNS
    assert tuple(pairings.columns) == PAIRING_COLUMNS
    assert tournaments.iloc[0]["date"].tzinfo is UTC
    assert participants.loc[participants["player_id"] == "p3", "decklist_available"].iloc[0] == False
    assert pairings.iloc[-1]["winner"] == 0


def test_duplicate_identical_player_is_deduplicated():
    rows = load("standings.json")
    df = normalize_participants("t1", [rows[0], rows[0]])
    assert len(df) == 1


def test_duplicate_player_conflict_raises():
    rows = load("standings.json")
    changed = dict(rows[0])
    changed["placing"] = 2
    with pytest.raises(NormalizationConflictError, match="conflicting duplicate player"):
        normalize_participants("t1", [rows[0], changed])


def test_duplicate_identical_pairing_is_deduplicated():
    row = load("pairings.json")[0]
    df = normalize_pairings("t1", [row, row])
    assert len(df) == 1


def test_duplicate_pairing_conflict_raises():
    row = load("pairings.json")[0]
    changed = dict(row)
    changed["winner"] = "p2"
    with pytest.raises(NormalizationConflictError, match="conflicting duplicate pairing"):
        normalize_pairings("t1", [row, changed])


def test_pairing_sentinels_are_normalized_to_numbers():
    rows = [
        {"phase": 1, "round": 1, "table": 1, "player1": "p1", "player2": "p2", "winner": "0"},
        {"phase": 1, "round": 2, "table": 1, "player1": "p1", "player2": "p2", "winner": "-1"},
    ]
    df = normalize_pairings("t1", rows)
    assert df["winner"].tolist() == [0, -1]


def test_duplicate_tournament_conflict_raises():
    details = load("details.json")
    changed = dict(details)
    changed["players"] = 99
    with pytest.raises(NormalizationConflictError, match="conflicting duplicate tournament"):
        normalize_tournaments(((details, "s1"), (changed, "s1")))
