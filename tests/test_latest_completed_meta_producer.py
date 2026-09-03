from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.latest_completed_meta_producer import (
    PUBLIC_RANKING_COLUMNS,
    _find_casefold_file,
    _fragment,
    _load_public_deck_labels,
    _load_tournament_api_manifest,
    _normalize_ranking,
    _reconcile_ranking,
)


def sample_ranking() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Rank": 1,
                "Deck": "Pikachu",
                "Score_%": 60.123456,
                "MAS_%": 55.123456,
                "LB_%": 50.123456,
                "BT_%": 65.123456,
                "SE_%": 2.123456,
                "N_eff": 100,
                "Opp_used": 1,
                "Opp_total": 1,
                "Coverage_%": 100.0,
            },
            {
                "Rank": 2,
                "Deck": "Mewtwo",
                "Score_%": 39.876544,
                "MAS_%": 44.876544,
                "LB_%": 40.876544,
                "BT_%": 34.876544,
                "SE_%": 2.876544,
                "N_eff": 100,
                "Opp_used": 1,
                "Opp_total": 1,
                "Coverage_%": 100.0,
            },
        ]
    )


def test_normalize_ranking_has_stable_columns_and_precision():
    normalized = _normalize_ranking(sample_ranking())

    assert tuple(normalized.columns) == PUBLIC_RANKING_COLUMNS
    assert normalized.loc[0, "Score_%"] == 60.1235
    assert normalized.loc[0, "N_eff"] == 100


def test_normalize_ranking_rejects_duplicate_decks():
    ranking = sample_ranking()
    ranking.loc[1, "Deck"] = "Pikachu"

    with pytest.raises(ValueError, match="duplicate"):
        _normalize_ranking(ranking)


def test_reconcile_ranking_requires_same_values_and_deck_order():
    source = sample_ranking()
    regenerated = sample_ranking().set_index("Rank")

    assert _reconcile_ranking(source, regenerated) == 0.0
    regenerated.loc[1, "Score_%"] += 0.01
    with pytest.raises(ValueError, match="differs"):
        _reconcile_ranking(source, regenerated)


def test_casefold_lookup_supports_legacy_output_directory_casing(tmp_path: Path):
    target = tmp_path / "Decklists" / "raw" / "decklist_raw_latest.csv"
    target.parent.mkdir(parents=True)
    target.write_text("Deck\nPikachu\n", encoding="utf-8")

    found = _find_casefold_file(tmp_path, "decklists/raw/decklist_raw_latest.csv")

    assert found == target


def test_fragment_is_compact_and_scientifically_cautious():
    manifest = {
        "snapshot": {"set": {"code": "B3b", "name": "Everyday Wonders"}, "format": "standard"},
        "analysis": {
            "core_decks": 2,
            "decisive_matches": 100,
            "coverage_pct": {"min": 100.0, "max": 100.0},
        },
    }

    fragment = _fragment(ranking=sample_ranking(), manifest=manifest)

    assert "See MARS in action" in fragment
    assert "not a match win probability" in fragment
    assert "not affiliated with or endorsed by Limitless TCG" in fragment
    assert "public/latest-meta/heatmap.png" in fragment


def test_tournament_api_manifest_accepts_exact_completed_scope(
    tmp_path: Path,
):
    import json

    path = tmp_path / "manifest.json"

    payload = {
        "run_id": "limitless-api-live-example",
        "source": "Limitless Tournament API",
        "software": {"git_revision": "abc123"},
        "scope": {
            "game": "POCKET",
            "format": "STANDARD",
            "set_code": "B4",
            "set_name": "Ruler of the Skies",
            "start": "2026-07-30T01:00:00Z",
            "end": "2026-08-27T01:00:00Z",
            "catalog_version": "test-catalog",
        },
        "selection": {
            "tournament_ids": ["one", "two"],
            "included_count": 2,
            "failures": [],
        },
        "normalized": {
            "row_counts": {
                "participants": 100,
                "pairings": 200,
            }
        },
        "aggregation": {
            "comparable_matches": 150,
        },
    }

    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = _load_tournament_api_manifest(
        path,
        set_code="B4",
        set_name="Ruler of the Skies",
    )

    assert loaded["scope"]["end"] == "2026-08-27T01:00:00Z"
    assert loaded["aggregation"]["comparable_matches"] == 150


def test_tournament_api_manifest_rejects_wrong_completed_set(
    tmp_path: Path,
):
    import json

    path = tmp_path / "manifest.json"

    payload = {
        "run_id": "limitless-api-live-example",
        "source": "Limitless Tournament API",
        "scope": {
            "game": "POCKET",
            "format": "STANDARD",
            "set_code": "B3b",
            "set_name": "Everyday Wonders",
            "start": "2026-06-30T01:00:00Z",
            "end": "2026-07-30T01:00:00Z",
        },
        "selection": {
            "tournament_ids": [],
            "included_count": 0,
            "failures": [],
        },
        "normalized": {
            "row_counts": {
                "participants": 0,
                "pairings": 0,
            }
        },
        "aggregation": {
            "comparable_matches": 0,
        },
    }

    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="set does not match"):
        _load_tournament_api_manifest(
            path,
            set_code="B4",
            set_name="Ruler of the Skies",
        )


def test_public_deck_labels_are_human_readable_and_collision_safe(
    tmp_path: Path,
):
    import json

    manifest = tmp_path / "run" / "run_manifest_latest.json"
    manifest.parent.mkdir(parents=True)

    manifest.write_text(
        json.dumps(
            {
                "diagnostics": {
                    "deck_identity_map": [
                        {
                            "deck_id": "alpha-id",
                            "deck_name": "Alpha Deck",
                        },
                        {
                            "deck_id": "beta-id",
                            "deck_name": "Shared Deck",
                        },
                        {
                            "deck_id": "gamma-id",
                            "deck_name": "Shared Deck",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    labels = _load_public_deck_labels(
        tmp_path,
        ["alpha-id", "beta-id", "gamma-id"],
    )

    assert labels["alpha-id"] == "Alpha Deck"
    assert labels["beta-id"] == "Shared Deck [beta-id]"
    assert labels["gamma-id"] == "Shared Deck [gamma-id]"
