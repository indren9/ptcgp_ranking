import math

import pandas as pd
import pytest

from acquisition.aggregation import AggregationConflictError, aggregate_matchups, aggregate_meta


def participants(rows):
    base = []
    for row in rows:
        base.append(
            {
                "tournament_id": row[0],
                "player_id": row[1],
                "deck_id": row[2],
                "deck_name": row[3],
            }
        )
    return pd.DataFrame(base)


def pairing(tid, key, p1, p2, winner):
    return {
        "tournament_id": tid,
        "phase": 1,
        "round": 1,
        "table": 1,
        "match": None,
        "player1": p1,
        "player2": p2,
        "winner": winner,
        "pairing_key": f"{tid}|{key}",
    }


def test_meta_uses_classified_participants_as_denominator_and_reports_coverage():
    df = participants(
        [
            ("t1", "p1", "a", "Deck A"),
            ("t1", "p2", "a", "Deck A"),
            ("t1", "p3", "b", "Deck B"),
            ("t1", "p4", None, None),
        ]
    )
    result = aggregate_meta(df)

    assert result.total_participants == 4
    assert result.classified_participants == 3
    assert result.unclassified_participants == 1
    assert result.classification_coverage == pytest.approx(0.75)
    assert result.meta.loc[result.meta["Deck"] == "Deck A", "Share_%"].iloc[0] == pytest.approx(200 / 3)
    assert result.meta["Share_%"].sum() == pytest.approx(100.0)


def test_player1_and_player2_wins_produce_symmetric_directional_counts():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", "b", "Deck B")])
    q = pd.DataFrame(
        [
            pairing("t1", "r1", "p1", "p2", "p1"),
            pairing("t1", "r2", "p1", "p2", "p2"),
        ]
    )
    result = aggregate_matchups(p, q)
    ab = result.matchups.set_index(["Deck A", "Deck B"]).loc[("Deck A", "Deck B")]
    ba = result.matchups.set_index(["Deck A", "Deck B"]).loc[("Deck B", "Deck A")]

    assert (int(ab["W"]), int(ab["L"]), int(ab["T"]), int(ab["N"])) == (1, 1, 0, 2)
    assert ab.WR_dir == pytest.approx(50.0)
    assert int(ab["W"]) == int(ba["L"])
    assert int(ab["L"]) == int(ba["W"])
    assert int(ab["T"]) == int(ba["T"])


def test_tie_is_preserved_in_both_directions_and_wr_is_nan():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", "b", "Deck B")])
    result = aggregate_matchups(p, pd.DataFrame([pairing("t1", "r1", "p1", "p2", 0)]))

    assert len(result.matchups) == 2
    assert result.matchups["T"].tolist() == [1, 1]
    assert result.matchups["N"].tolist() == [1, 1]
    assert result.matchups["WR_dir"].isna().all()
    assert result.comparable_matches == 1


def test_bye_is_diagnostic_and_not_a_matchup():
    p = participants([("t1", "p1", "a", "Deck A")])
    result = aggregate_matchups(p, pd.DataFrame([pairing("t1", "r1", "p1", None, "p1")]))
    assert result.matchups.empty
    assert result.pairing_exclusion_counts["bye"] == 1


def test_missing_deck_is_diagnostic_and_not_a_matchup():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", None, None)])
    result = aggregate_matchups(p, pd.DataFrame([pairing("t1", "r1", "p1", "p2", "p1")]))
    assert result.matchups.empty
    assert result.pairing_exclusion_counts["missing_deck"] == 1


def test_double_loss_is_diagnostic_and_not_normal_wl():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", "b", "Deck B")])
    result = aggregate_matchups(p, pd.DataFrame([pairing("t1", "r1", "p1", "p2", -1)]))
    assert result.matchups.empty
    assert result.pairing_exclusion_counts["double_loss"] == 1


def test_unresolved_winner_is_diagnostic():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", "b", "Deck B")])
    result = aggregate_matchups(p, pd.DataFrame([pairing("t1", "r1", "p1", "p2", "someone-else")]))
    assert result.matchups.empty
    assert result.pairing_exclusion_counts["unresolved_result"] == 1


def test_same_archetype_match_is_excluded():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", "a", "Deck A")])
    result = aggregate_matchups(p, pd.DataFrame([pairing("t1", "r1", "p1", "p2", "p1")]))
    assert result.matchups.empty
    assert result.pairing_exclusion_counts["same_archetype"] == 1



def test_duplicate_display_name_is_preserved_as_distinct_meta_identities():
    p = participants(
        [
            ("t1", "p1", "dragon-1", "Dragonair Altaria"),
            ("t1", "p2", "dragon-2", "Dragonair Altaria"),
            ("t1", "p3", "dragon-1", "Dragonair Altaria"),
        ]
    )
    result = aggregate_meta(p)
    rows = result.meta[result.meta["Deck"] == "Dragonair Altaria"].set_index("Deck ID")
    assert set(rows.index) == {"dragon-1", "dragon-2"}
    assert int(rows.loc["dragon-1", "Count"]) == 2
    assert int(rows.loc["dragon-2", "Count"]) == 1
    assert rows.loc["dragon-1", "Share_%"] == pytest.approx(200 / 3)
    assert rows.loc["dragon-2", "Share_%"] == pytest.approx(100 / 3)
    assert result.duplicate_display_names == {
        "Dragonair Altaria": ("dragon-1", "dragon-2")
    }


def test_same_display_name_different_ids_is_not_same_archetype():
    p = participants(
        [
            ("t1", "p1", "dragon-1", "Dragonair Altaria"),
            ("t1", "p2", "dragon-2", "Dragonair Altaria"),
        ]
    )
    result = aggregate_matchups(p, pd.DataFrame([pairing("t1", "r1", "p1", "p2", "p1")]))
    assert result.pairing_exclusion_counts["same_archetype"] == 0
    assert len(result.matchups) == 2
    forward = result.matchups.set_index(["Deck A ID", "Deck B ID"]).loc[("dragon-1", "dragon-2")]
    assert forward["Deck A"] == "Dragonair Altaria"
    assert forward["Deck B"] == "Dragonair Altaria"
    assert int(forward["W"]) == 1


def test_duplicate_display_names_across_tournaments_aggregate_by_id_not_name():
    p = participants(
        [
            ("t1", "p1", "dragon-1", "Dragonair Altaria"),
            ("t1", "p2", "other", "Other"),
            ("t2", "p3", "dragon-2", "Dragonair Altaria"),
            ("t2", "p4", "other", "Other"),
        ]
    )
    q = pd.DataFrame(
        [
            pairing("t1", "r1", "p1", "p2", "p1"),
            pairing("t2", "r1", "p3", "p4", "p3"),
        ]
    )
    result = aggregate_matchups(p, q)
    lookup = result.matchups.set_index(["Deck A ID", "Deck B ID"])
    assert int(lookup.loc[("dragon-1", "other"), "W"]) == 1
    assert int(lookup.loc[("dragon-2", "other"), "W"]) == 1
    assert ("dragon-1", "dragon-2") not in lookup.index


def test_same_deck_id_multiple_names_fails_fast():
    p = participants(
        [
            ("t1", "p1", "dragon-1", "Dragonair Altaria"),
            ("t2", "p2", "dragon-1", "Different Label"),
        ]
    )
    with pytest.raises(AggregationConflictError, match="deck_id maps to multiple deck names"):
        aggregate_meta(p)

def test_cross_tournament_results_are_summed_never_maxed():
    p = participants(
        [
            ("t1", "p1", "a", "Deck A"),
            ("t1", "p2", "b", "Deck B"),
            ("t2", "p3", "a", "Deck A"),
            ("t2", "p4", "b", "Deck B"),
        ]
    )
    q = pd.DataFrame(
        [
            pairing("t1", "r1", "p1", "p2", "p1"),
            pairing("t2", "r1", "p3", "p4", "p3"),
        ]
    )
    result = aggregate_matchups(p, q)
    ab = result.matchups.set_index(["Deck A", "Deck B"]).loc[("Deck A", "Deck B")]
    assert (int(ab["W"]), int(ab["L"]), int(ab["N"])) == (2, 0, 2)
    assert ab.WR_dir == pytest.approx(100.0)


def test_duplicate_pairing_is_diagnosed_once_and_not_double_counted():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", "b", "Deck B")])
    row = pairing("t1", "r1", "p1", "p2", "p1")
    result = aggregate_matchups(p, pd.DataFrame([row, row]))
    ab = result.matchups.set_index(["Deck A", "Deck B"]).loc[("Deck A", "Deck B")]
    assert int(ab["W"]) == 1
    assert result.pairing_exclusion_counts["duplicate_pairing"] == 1


def test_conflicting_duplicate_pairing_raises():
    p = participants([("t1", "p1", "a", "Deck A"), ("t1", "p2", "b", "Deck B")])
    row1 = pairing("t1", "r1", "p1", "p2", "p1")
    row2 = dict(row1)
    row2["winner"] = "p2"
    with pytest.raises(AggregationConflictError, match="conflicting duplicate pairing"):
        aggregate_matchups(p, pd.DataFrame([row1, row2]))
