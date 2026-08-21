from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.latest_completed_meta_producer import (
    PUBLIC_RANKING_COLUMNS,
    _find_casefold_file,
    _fragment,
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
