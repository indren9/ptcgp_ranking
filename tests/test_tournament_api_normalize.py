from datetime import UTC
import json
from pathlib import Path

import pandas as pd
import pytest

from acquisition.aggregation import aggregate_matchups, aggregate_meta
from acquisition.contracts import hash_dataframe
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


def test_canonical_bye_is_unchanged():
    raw = {
        "phase": 1,
        "round": 1,
        "table": 1,
        "player1": "p1",
        "player2": None,
        "winner": "p1",
    }
    diagnostics = {}

    df = normalize_pairings(
        "t1",
        [raw],
        participant_ids=["p1"],
        diagnostics=diagnostics,
    )

    row = df.iloc[0]
    assert row["player1"] == "p1"
    assert row["player2"] is None
    assert row["winner"] == "p1"
    assert diagnostics == {}


def test_missing_player1_reversed_slot_bye_is_canonicalized_without_mutating_raw():
    raw = {
        "phase": 1,
        "round": 1,
        "table": 2,
        "player2": "p2",
        "winner": "p2",
    }
    before = json.loads(json.dumps(raw))
    diagnostics = {}

    df = normalize_pairings(
        "t1",
        [raw],
        participant_ids=["p1", "p2"],
        diagnostics=diagnostics,
    )

    row = df.iloc[0]
    assert row["player1"] == "p2"
    assert row["player2"] is None
    assert row["winner"] == "p2"
    assert diagnostics == {"canonicalized_player2_bye_count": 1}
    assert raw == before


def test_missing_player1_with_nonwinning_player2_fails_fast():
    raw = {
        "phase": 1,
        "round": 1,
        "table": 2,
        "player2": "p2",
        "winner": "p1",
    }

    with pytest.raises(ValueError, match="winner must equal player2"):
        normalize_pairings(
            "t1",
            [raw],
            participant_ids=["p1", "p2"],
        )


def test_missing_player1_reversed_slot_bye_requires_known_participant_when_available():
    raw = {
        "phase": 1,
        "round": 1,
        "table": 2,
        "player2": "ghost",
        "winner": "ghost",
    }

    with pytest.raises(ValueError, match="player2 is not a normalized participant"):
        normalize_pairings(
            "t1",
            [raw],
            participant_ids=["p1", "p2"],
        )


def test_pairing_with_no_players_is_excluded_without_mutating_raw():
    raw = {
        "phase": 1,
        "round": 1,
        "table": 3,
        "winner": -1,
    }
    before = json.loads(json.dumps(raw))
    diagnostics = {}

    df = normalize_pairings(
        "t1",
        [raw],
        participant_ids=["p1", "p2"],
        diagnostics=diagnostics,
    )

    assert df.empty
    assert tuple(df.columns) == PAIRING_COLUMNS
    assert diagnostics == {"excluded_pairing_no_players_count": 1}
    assert raw == before


def test_pairing_normalization_anomalies_do_not_increase_comparable_matches():
    participants = normalize_participants(
        "t1",
        [
            _standing(player="p1", placing=1, deck_id="deck-a", deck_name="Deck A"),
            _standing(player="p2", placing=2, deck_id="deck-b", deck_name="Deck B"),
        ],
    )
    raw_pairings = [
        {
            "phase": 1,
            "round": 1,
            "table": 1,
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 2,
            "player2": "p2",
            "winner": "p2",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 3,
            "winner": -1,
        },
    ]
    diagnostics = {}

    pairings = normalize_pairings(
        "t1",
        raw_pairings,
        participant_ids=participants["player_id"].tolist(),
        diagnostics=diagnostics,
    )
    result = aggregate_matchups(participants, pairings)

    assert result.comparable_matches == 1
    assert result.pairing_exclusion_counts["bye"] == 1
    assert diagnostics == {
        "canonicalized_player2_bye_count": 1,
        "excluded_pairing_no_players_count": 1,
    }


def test_same_table_distinct_participant_pairs_remain_distinct():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 1,
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 1,
            "player1": "p3",
            "player2": "p4",
            "winner": "p3",
        },
    ]

    df = normalize_pairings("t1", rows)

    assert len(df) == 2
    assert df["pairing_key"].nunique() == 2
    assert all("table:" not in key for key in df["pairing_key"])


def test_same_match_label_distinct_participant_pairs_remain_distinct():
    rows = [
        {
            "phase": 2,
            "round": 6,
            "match": "T2-1",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 2,
            "round": 6,
            "match": "T2-1",
            "player1": "p3",
            "player2": "p4",
            "winner": "p4",
        },
    ]

    df = normalize_pairings("t1", rows)

    assert len(df) == 2
    assert df["pairing_key"].nunique() == 2
    assert all("match:" not in key for key in df["pairing_key"])


def test_same_match_and_table_distinct_pairs_remain_distinct():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 4,
            "match": "m-1",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 4,
            "match": "m-1",
            "player1": "p3",
            "player2": "p4",
            "winner": "p3",
        },
    ]

    df = normalize_pairings("t1", rows)

    assert len(df) == 2
    assert set(df["pairing_key"]) == {
        't1|phase:1|round:1|players:["p1","p2"]|occurrence:single',
        't1|phase:1|round:1|players:["p3","p4"]|occurrence:single',
    }


def test_same_pair_same_semantics_same_locators_is_deduplicated():
    row = {
        "phase": 1,
        "round": 1,
        "table": 2,
        "match": "m-1",
        "player1": "p1",
        "player2": "p2",
        "winner": "p1",
    }

    df = normalize_pairings("t1", [row, dict(row)])

    assert len(df) == 1


def test_same_pair_locator_variants_dedupe_to_deterministic_representative():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 9,
            "match": "B",
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 2,
            "match": "A",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
    ]
    before = json.loads(json.dumps(rows))

    first = normalize_pairings("t1", rows)
    second = normalize_pairings("t1", list(reversed(rows)))

    assert len(first) == 1
    assert first.iloc[0]["pairing_key"] == 't1|phase:1|round:1|players:["p1","p2"]|occurrence:single'
    assert first.iloc[0]["match"] == "A"
    assert first.iloc[0]["table"] == 2
    pd.testing.assert_frame_equal(first, second)
    assert hash_dataframe(first) == hash_dataframe(second)
    assert rows == before


def test_same_pair_legitimate_rematch_uses_match_as_local_discriminator():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "match": "F",
            "player1": "p1",
            "player2": "p2",
            "winner": "p2",
        },
        {
            "phase": 1,
            "round": 1,
            "match": "W3-1",
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        },
    ]
    diagnostics = {}

    df = normalize_pairings("t1", rows, diagnostics=diagnostics)

    assert len(df) == 2
    assert set(df["pairing_key"]) == {
        't1|phase:1|round:1|players:["p1","p2"]|match:"F"',
        't1|phase:1|round:1|players:["p1","p2"]|match:"W3-1"',
    }
    assert diagnostics["pairing_base_collision_count"] == 1
    assert diagnostics["pairing_rematch_occurrence_count"] == 1
    assert diagnostics["pairing_match_discriminator_count"] == 2


def test_reversed_player_slots_share_participant_canonical_identity():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 3,
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 4,
            "match": "locator-only",
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        },
    ]

    df = normalize_pairings("t1", rows)

    assert len(df) == 1
    assert df.iloc[0]["pairing_key"] == 't1|phase:1|round:1|players:["p1","p2"]|occurrence:single'


def test_canonical_bye_key_is_participant_based_and_ignores_locators():
    raw = {
        "phase": 1,
        "round": 1,
        "table": 5,
        "match": "T4-1",
        "player2": "p2",
        "winner": "p2",
    }
    before = json.loads(json.dumps(raw))

    first = normalize_pairings("t1", [raw], participant_ids=["p2"])
    second = normalize_pairings("t1", [raw], participant_ids=["p2"])

    assert first.iloc[0]["pairing_key"] == 't1|phase:1|round:1|bye:"p2"|occurrence:single'
    assert first.iloc[0]["table"] == 5
    assert first.iloc[0]["match"] == "T4-1"
    pd.testing.assert_frame_equal(first, second)
    assert raw == before


def test_distinct_byes_same_locator_remain_distinct_by_participant():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 6,
            "match": "m-1",
            "player1": "p1",
            "player2": None,
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 6,
            "match": "m-1",
            "player1": "p2",
            "player2": None,
            "winner": "p2",
        },
    ]

    df = normalize_pairings("t1", rows)

    assert len(df) == 2
    assert set(df["pairing_key"]) == {
        't1|phase:1|round:1|bye:"p1"|occurrence:single',
        't1|phase:1|round:1|bye:"p2"|occurrence:single',
    }


def test_no_player_record_is_excluded_before_pairing_key_generation():
    diagnostics = {}
    df = normalize_pairings(
        "t1",
        [{"phase": 1, "round": 1, "table": 7, "winner": -1}],
        participant_ids=["p1"],
        diagnostics=diagnostics,
    )

    assert df.empty
    assert diagnostics == {"excluded_pairing_no_players_count": 1}


def test_missing_match_and_table_identity_is_deterministic():
    rows = [
        {
            "phase": 2,
            "round": 3,
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        }
    ]

    df = normalize_pairings("t1", rows)

    assert df.iloc[0]["pairing_key"] == 't1|phase:2|round:3|players:["p1","p2"]|occurrence:single'
    assert df.iloc[0]["match"] is None
    assert df.iloc[0]["table"] is None


def test_pairing_normalized_hash_is_deterministic_across_payload_order():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 8,
            "match": "Z",
            "player1": "p3",
            "player2": "p4",
            "winner": "p4",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 2,
            "match": "A",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 9,
            "match": "B",
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        },
    ]

    first = normalize_pairings("t1", rows)
    second = normalize_pairings("t1", list(reversed(rows)))

    pd.testing.assert_frame_equal(first, second)
    assert hash_dataframe(first) == hash_dataframe(second)


def test_locator_variants_do_not_inflate_comparable_matches():
    participants = normalize_participants(
        "t1",
        [
            _standing(player="p1", placing=1, deck_id="deck-a", deck_name="Deck A"),
            _standing(player="p2", placing=2, deck_id="deck-b", deck_name="Deck B"),
        ],
    )
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 1,
            "match": "A",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 7,
            "match": "B",
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        },
    ]

    pairings = normalize_pairings("t1", rows)
    result = aggregate_matchups(participants, pairings)

    assert len(pairings) == 1
    assert result.comparable_matches == 1


def test_same_pair_same_match_incompatible_outcome_fails_fast():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "match": "F",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "match": "F",
            "player1": "p2",
            "player2": "p1",
            "winner": "p2",
        },
    ]
    diagnostics = {}

    with pytest.raises(NormalizationConflictError, match="conflicting duplicate pairing"):
        normalize_pairings("t1", rows, diagnostics=diagnostics)

    assert diagnostics["pairing_unresolved_conflict_count"] == 1


def test_same_pair_no_match_distinct_tables_preserve_legitimate_occurrences():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 3,
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 7,
            "player1": "p2",
            "player2": "p1",
            "winner": "p2",
        },
    ]
    diagnostics = {}

    df = normalize_pairings("t1", rows, diagnostics=diagnostics)

    assert len(df) == 2
    assert set(df["pairing_key"]) == {
        't1|phase:1|round:1|players:["p1","p2"]|table:3',
        't1|phase:1|round:1|players:["p1","p2"]|table:7',
    }
    assert diagnostics["pairing_table_fallback_count"] == 2
    assert diagnostics["pairing_rematch_occurrence_count"] == 1


def test_same_pair_no_match_same_table_equivalent_rows_dedupe():
    row = {
        "phase": 1,
        "round": 1,
        "table": 3,
        "player1": "p1",
        "player2": "p2",
        "winner": "p1",
    }
    diagnostics = {}

    df = normalize_pairings("t1", [row, dict(row)], diagnostics=diagnostics)

    assert len(df) == 1
    assert df.iloc[0]["pairing_key"].endswith("|occurrence:single")
    assert diagnostics["pairing_deduplicated_count"] == 1


def test_same_pair_no_match_same_table_incompatible_outcome_fails_fast():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "table": 3,
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 3,
            "player1": "p2",
            "player2": "p1",
            "winner": "p2",
        },
    ]

    with pytest.raises(NormalizationConflictError, match="conflicting duplicate pairing"):
        normalize_pairings("t1", rows)


def test_same_pair_no_match_no_table_equivalent_rows_dedupe():
    row = {
        "phase": 1,
        "round": 1,
        "player1": "p1",
        "player2": "p2",
        "winner": "p1",
    }

    df = normalize_pairings("t1", [row, dict(row)])

    assert len(df) == 1
    assert df.iloc[0]["pairing_key"].endswith("|occurrence:single")


def test_same_pair_no_match_no_table_incompatible_outcome_fails_fast():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "player1": "p2",
            "player2": "p1",
            "winner": "p2",
        },
    ]

    with pytest.raises(NormalizationConflictError, match="conflicting duplicate pairing"):
        normalize_pairings("t1", rows)


def test_mixed_match_present_and_missing_new_outcome_fails_fast():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "match": "F",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 9,
            "player1": "p2",
            "player2": "p1",
            "winner": "p2",
        },
    ]

    with pytest.raises(NormalizationConflictError, match="conflicting duplicate pairing"):
        normalize_pairings("t1", rows)


def test_mixed_match_present_and_missing_unique_equivalent_row_dedupes():
    rows = [
        {
            "phase": 1,
            "round": 1,
            "match": "F",
            "player1": "p1",
            "player2": "p2",
            "winner": "p1",
        },
        {
            "phase": 1,
            "round": 1,
            "table": 9,
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        },
    ]

    df = normalize_pairings("t1", rows)

    assert len(df) == 1
    assert df.iloc[0]["pairing_key"].endswith("|occurrence:single")


def test_swiss_style_single_occurrence_ignores_locator_for_identity():
    row = {
        "phase": 1,
        "round": 4,
        "table": 12,
        "match": None,
        "player1": "p1",
        "player2": "p2",
        "winner": "p1",
    }

    df = normalize_pairings("t1", [row])

    assert len(df) == 1
    assert df.iloc[0]["pairing_key"] == (
        't1|phase:1|round:4|players:["p1","p2"]|occurrence:single'
    )


def test_legitimate_same_pair_rematch_increases_comparable_matches_exactly_twice():
    participants = normalize_participants(
        "t1",
        [
            _standing(player="p1", placing=1, deck_id="deck-a", deck_name="Deck A"),
            _standing(player="p2", placing=2, deck_id="deck-b", deck_name="Deck B"),
        ],
    )
    rows = [
        {
            "phase": 1,
            "round": 1,
            "match": "F",
            "player1": "p1",
            "player2": "p2",
            "winner": "p2",
        },
        {
            "phase": 1,
            "round": 1,
            "match": "W3-1",
            "player1": "p2",
            "player2": "p1",
            "winner": "p1",
        },
    ]

    pairings = normalize_pairings("t1", rows)
    result = aggregate_matchups(participants, pairings)

    assert len(pairings) == 2
    assert result.comparable_matches == 2
    ab = result.matchups.set_index(["Deck A", "Deck B"]).loc[("Deck A", "Deck B")]
    assert int(ab["W"]) == 1
    assert int(ab["L"]) == 1
