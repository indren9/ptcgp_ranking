from __future__ import annotations

import math

import pandas as pd
import pytest

from acquisition.contracts import AcquisitionFrames
from acquisition.production_bridge import bridge_tournament_api_frames, identity_mapping_diagnostics


def _frames() -> AcquisitionFrames:
    top = pd.DataFrame(
        [
            {"Rank": 1, "Deck ID": "id-1", "Deck": "Same Name", "Count": 6, "Share_%": 60.0},
            {"Rank": 2, "Deck ID": "id-2", "Deck": "Same Name", "Count": 4, "Share_%": 40.0},
        ]
    )
    sparse = pd.DataFrame(
        [
            {"Deck A ID": "id-1", "Deck A": "Same Name", "Deck B ID": "id-2", "Deck B": "Same Name", "W": 2, "L": 1, "T": 0, "N": 3, "WR_dir": 200 / 3},
            {"Deck A ID": "id-2", "Deck A": "Same Name", "Deck B ID": "id-1", "Deck B": "Same Name", "W": 1, "L": 2, "T": 0, "N": 3, "WR_dir": 100 / 3},
        ]
    )
    dense = sparse.copy()
    return AcquisitionFrames(top_meta_decklist=top, matchup_raw=sparse, dense_score=dense)


def test_bridge_uses_deck_id_as_collision_free_core_key():
    bridged = bridge_tournament_api_frames(_frames())
    assert bridged.top_meta_decklist["Deck"].tolist() == ["id-1", "id-2"]
    assert set(bridged.matchup_raw["Deck A"]) == {"id-1", "id-2"}
    assert set(bridged.dense_score["Deck A"]) == {"id-1", "id-2"}
    assert len(bridged.deck_identity_map) == 2


def test_duplicate_display_names_remain_distinct_and_mapping_is_deterministic():
    bridged = bridge_tournament_api_frames(_frames())
    diag = identity_mapping_diagnostics(bridged.deck_identity_map)
    assert diag["mapping"] == [
        {"deck_id": "id-1", "deck_name": "Same Name"},
        {"deck_id": "id-2", "deck_name": "Same Name"},
    ]
    assert diag["duplicate_display_names"] == {"Same Name": ["id-1", "id-2"]}


def test_bridge_rejects_player_id_at_public_boundary():
    frames = _frames()
    top = frames.top_meta_decklist.copy()
    top["player_id"] = "secret"
    unsafe = object.__new__(AcquisitionFrames)
    object.__setattr__(unsafe, "top_meta_decklist", top)
    object.__setattr__(unsafe, "matchup_raw", frames.matchup_raw)
    object.__setattr__(unsafe, "dense_score", frames.dense_score)
    with pytest.raises(ValueError, match="player_id"):
        bridge_tournament_api_frames(unsafe)
