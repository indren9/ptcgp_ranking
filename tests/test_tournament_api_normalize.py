from datetime import UTC
import json
from pathlib import Path

import pandas as pd
import pytest

from acquisition.aggregation import aggregate_matchups, aggregate_meta
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


def _standing(*, player, placing, deck_id, deck_name):
    return {
        "player": player,
        "placing": placing,
        "record": {"wins": 1, "losses": 0, "ties": 0},
        "decklist": {"cards": []},
        "deck": {"id": deck_id, "name": deck_name},
        "drop": 1 if placing is None else None,
    }


def test_nullable_placing_is_preserved_and_participant_remains_normalized():
    df = normalize_participants(
        "t1",
        [
            _standing(player="p1", placing=None, deck_id="deck-a", deck_name="Deck A"),
            _standing(player="p2", placing=2, deck_id="deck-b", deck_name="Deck B"),
        ],
    )

    row = df.loc[df["player_id"] == "p1"].iloc[0]
    assert pd.isna(row["placing"])
    assert row["player_id"] == "p1"
    assert row["deck_id"] == "deck-a"
    assert row["deck_name"] == "Deck A"
    assert len(df) == 2


def test_valid_placing_is_unchanged():
    df = normalize_participants(
        "t1",
        [_standing(player="p1", placing=7, deck_id="deck-a", deck_name="Deck A")],
    )
    assert int(df.iloc[0]["placing"]) == 7


def test_negative_placing_still_fails():
    with pytest.raises(ValueError, match="placing must be a non-negative integer or null"):
        normalize_participants(
            "t1",
            [_standing(player="p1", placing=-1, deck_id="deck-a", deck_name="Deck A")],
        )


def test_non_numeric_placing_still_fails():
    with pytest.raises(ValueError, match="placing must be an integer or null"):
        normalize_participants(
            "t1",
            [_standing(player="p1", placing="not-a-number", deck_id="deck-a", deck_name="Deck A")],
        )


def test_null_placing_participant_contributes_to_meta_and_matchup_join():
    participants = normalize_participants(
        "t1",
        [
            _standing(player="p1", placing=None, deck_id="deck-a", deck_name="Deck A"),
            _standing(player="p2", placing=1, deck_id="deck-b", deck_name="Deck B"),
        ],
    )
    pairings = normalize_pairings(
        "t1",
        [
            {
                "phase": 1,
                "round": 1,
                "table": 1,
                "player1": "p1",
                "player2": "p2",
                "winner": "p1",
            }
        ],
    )

    meta = aggregate_meta(participants)
    matchups = aggregate_matchups(participants, pairings)

    assert meta.total_participants == 2
    assert meta.classified_participants == 2
    assert set(meta.meta["Deck"]) == {"Deck A", "Deck B"}

    ab = matchups.matchups.set_index(["Deck A", "Deck B"]).loc[("Deck A", "Deck B")]
    assert int(ab["W"]) == 1
    assert int(ab["L"]) == 0
    assert matchups.comparable_matches == 1
