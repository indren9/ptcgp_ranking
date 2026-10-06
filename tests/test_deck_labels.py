import json

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from acquisition.aggregation import aggregate_meta, aggregate_matchups
from acquisition.contracts import AcquisitionFrames, adapt_top_meta_decklist, materialize_dense_score
from acquisition.deck_labels import DeckLabelResolutionError, LABEL_POLICY_VERSION, resolve_deck_labels
from acquisition.production_bridge import bridge_tournament_api_frames
from scripts.latest_completed_meta_producer import _load_public_deck_labels


def players(rows):
    return pd.DataFrame(rows, columns=["tournament_id", "player_id", "deck_id", "deck_name"])


def dates(*rows):
    return pd.DataFrame(rows, columns=["tournament_id", "date"])


@pytest.mark.parametrize("names,expected", [
    (["Only", "Only"], "Only"),
    ([None, "", "  "], "a"),
    ([None, " Only ", ""], "Only"),
])
def test_zero_or_one_name_needs_no_dates_and_preserves_observations(names, expected):
    p = players([("t", str(i), "a", name) for i, name in enumerate(names)])
    before = p.copy(deep=True)
    result = resolve_deck_labels(p)
    assert result.labels == {"a": expected}
    assert result.diagnostics["fallback_ids"] == (["a"] if expected == "a" else [])
    assert_frame_equal(p, before)


def test_latest_date_not_frequency_and_deterministic_evidence():
    p = players([("old", str(i), "a", "Old") for i in range(3)] +
                [("new", "x", "a", "New"), ("unknown", "y", "a", None)])
    t = dates(("old", "2020-01-01T00:00:00Z"), ("new", "2020-02-01T00:00:00Z"))
    before = p.copy(deep=True)
    result = resolve_deck_labels(p, t)
    assert result.labels == {"a": "New"}
    row = result.diagnostics["drift"][0]
    assert row["selected_label"] == "New"
    assert row["policy_version"] == LABEL_POLICY_VERSION
    assert row["unnamed_count"] == 1
    assert row["tie_break_used"] is False
    assert row["fallback_used"] is False
    assert row["observed_labels"] == [
        {"label": "New", "count": 1, "min_date_utc": "2020-02-01T00:00:00Z",
         "max_date_utc": "2020-02-01T00:00:00Z", "tournament_ids": ["new"],
         "latest_tournament_ids": ["new"]},
        {"label": "Old", "count": 3, "min_date_utc": "2020-01-01T00:00:00Z",
         "max_date_utc": "2020-01-01T00:00:00Z", "tournament_ids": ["old"],
         "latest_tournament_ids": ["old"]},
    ]
    assert_frame_equal(p, before)


@pytest.mark.parametrize("second_date", ["2020-01-01T00:00:00Z", "2020-01-01T02:00:00+02:00"])
def test_latest_utc_tie_uses_case_sensitive_unicode_order(second_date):
    p = players([("x", "1", "a", "alpha"), ("y", "2", "a", "Zulu"),
                 ("old", "3", "a", "Aardvark")])
    t = dates(("x", "2020-01-01T00:00:00Z"), ("y", second_date),
              ("old", "2019-01-01T00:00:00Z"))
    result = resolve_deck_labels(p, t)
    assert result.labels == {"a": "Zulu"}
    assert result.diagnostics["drift"][0]["tie_break_labels"] == ["Zulu", "alpha"]
    assert result.diagnostics["drift"][0]["tie_break_used"] is True


@pytest.mark.parametrize("bad", [None, "", "invalid", "2020-01-01", pd.NaT])
def test_invalid_or_naive_required_dates_fail_closed(bad):
    p = players([("x", "1", "a", "Old"), ("y", "2", "a", "New")])
    with pytest.raises(DeckLabelResolutionError, match="date UTC"):
        resolve_deck_labels(p, dates(("x", bad), ("y", "2020-01-01T00:00:00Z")))


def test_missing_and_conflicting_tournament_relation_fail_closed():
    p = players([("x", "1", "a", "Old"), ("y", "2", "a", "New")])
    for t in [None, dates(("x", "2020-01-01T00:00:00Z")),
              dates(("x", "2020-01-01T00:00:00Z"), ("x", "2020-02-01T00:00:00Z"),
                    ("y", "2020-03-01T00:00:00Z"))]:
        with pytest.raises(DeckLabelResolutionError, match="date UTC"):
            resolve_deck_labels(p, t)


def test_row_and_tournament_permutations_preserve_mapping_and_serialization():
    p = players([("x", "1", "a", "Z"), ("y", "2", "a", "A"),
                 ("x", "3", "b", "A"), ("x", "4", "c", None)])
    t = dates(("x", "2020-01-01T00:00:00Z"), ("y", "2020-02-01T00:00:00Z"))
    expected = resolve_deck_labels(p, t)
    for seed in range(5):
        actual = resolve_deck_labels(p.sample(frac=1, random_state=seed),
                                     t.sample(frac=1, random_state=seed))
        assert actual.labels == expected.labels
        assert json.dumps(actual.diagnostics) == json.dumps(expected.diagnostics)


def test_only_label_drift_preserves_counts_exclusions_dense_and_core_keys():
    p = players([("x", "1", "a", "Old"), ("x", "2", "b", "New"),
                 ("x", "3", "a", "Old"), ("y", "1", "a", "New"),
                 ("y", "2", "b", "New")])
    t = dates(("x", "2020-01-01T00:00:00Z"), ("y", "2020-02-01T00:00:00Z"))
    q = pd.DataFrame([
        ("x", "w", "1", "2", "1"), ("x", "l", "1", "2", "2"),
        ("y", "t", "1", "2", 0), ("x", "mirror", "1", "3", "1"),
        ("x", "bye", "1", None, "1"), ("x", "missing", "1", "absent", "1"),
    ], columns=["tournament_id", "pairing_key", "player1", "player2", "winner"])
    stable = p.copy()
    stable.loc[stable.deck_id == "a", "deck_name"] = "New"
    results = []
    for data in (stable, p):
        meta = aggregate_meta(data, t)
        matches = aggregate_matchups(data, q, t)
        top = adapt_top_meta_decklist(meta.meta)
        dense = materialize_dense_score(matches.matchups, tuple(zip(top["Deck ID"], top["Deck"])))
        bridge = bridge_tournament_api_frames(AcquisitionFrames(top, matches.matchups, dense))
        results.append((meta, matches, bridge))
    for attr in ("comparable_matches", "pairing_exclusion_counts"):
        assert getattr(results[0][1], attr) == getattr(results[1][1], attr)
    assert results[1][1].comparable_matches == 3
    assert results[1][1].pairing_exclusion_counts["same_archetype"] == 1
    assert len(results[1][1].matchups) == 2
    assert results[1][1].matchups[["W", "L", "T", "N"]].values.tolist() == [[1, 1, 1, 3]] * 2
    for attr in ("top_meta_decklist", "matchup_raw", "dense_score", "deck_identity_map"):
        assert_frame_equal(getattr(results[0][2], attr), getattr(results[1][2], attr))
    mapping = results[1][2].deck_identity_map
    assert mapping["Deck ID"].tolist() == ["a", "b"]
    assert mapping["Deck"].tolist() == ["New", "New"]
    assert not mapping["Deck ID"].duplicated().any()
    assert set(results[1][2].dense_score["Deck A"]) == {"a", "b"}
    from core.matrices import build_matrices, n_dir_from_WL
    import numpy as np
    baseline = build_matrices(results[0][2].dense_score, ["a", "b"])
    changed = build_matrices(results[1][2].dense_score, ["a", "b"])
    for old, new in zip(baseline, changed):
        assert_frame_equal(old, new)
        assert new.index.tolist() == ["a", "b"]
    w, l, ties, wr = changed
    n_dir = n_dir_from_WL(w, l)
    assert n_dir.loc["a", "b"] == 2
    assert wr.loc["a", "b"] + wr.loc["b", "a"] == 100
    assert np.isnan(np.diag(wr)).all()
    assert np.isnan(np.diag(n_dir)).all()


def test_bridge_refuses_conflicting_already_resolved_mapping():
    from test_b9_production_bridge import _frames
    frames = _frames()
    frames.matchup_raw.loc[0, "Deck A"] = "Conflicting"
    with pytest.raises(ValueError, match="multiple display names"):
        bridge_tournament_api_frames(frames)


@pytest.mark.parametrize("reverse", [False, True])
def test_publication_refuses_conflict_independent_of_row_order(tmp_path, reverse):
    items = [{"deck_id": "a", "deck_name": "Old"}, {"deck_id": "a", "deck_name": "New"}]
    if reverse:
        items.reverse()
    target = tmp_path / "run/run_manifest_latest.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"diagnostics": {"deck_identity_map": items}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Conflicting public deck labels"):
        _load_public_deck_labels(tmp_path, ["a"])
