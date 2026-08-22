from datetime import UTC, datetime
import json

import numpy as np
import pandas as pd
import pytest

from acquisition.contracts import (
    DENSE_SCORE_COLUMNS,
    MATCHUP_COLUMNS,
    TOP_META_COLUMNS,
    RawPayloadRef,
    adapt_matchup_raw,
    adapt_top_meta_decklist,
    build_acquisition_contracts,
    materialize_dense_score,
)
from acquisition.manifest import (
    AcquisitionManifest,
    AggregationSummary,
    NormalizedSummary,
    RawSummary,
    validate_manifest_dict,
)
from acquisition.scope import ScopePolicy
from acquisition.selection import TournamentSelection


def observed_matchups():
    return pd.DataFrame(
        [
            {"Deck A": "A", "Deck B": "B", "W": 2, "L": 1, "T": 1},
            {"Deck A": "B", "Deck B": "A", "W": 1, "L": 2, "T": 1},
            {"Deck A": "A", "Deck B": "C", "W": 0, "L": 0, "T": 2},
            {"Deck A": "C", "Deck B": "A", "W": 0, "L": 0, "T": 2},
        ]
    )


def test_top_meta_adapter_uses_canonical_columns_and_optional_legacy_share():
    meta = pd.DataFrame(
        [
            {"Deck ID": "a", "Deck": "A", "Count": 6, "Share_%": 60.0, "Rank": 1},
            {"Deck ID": "b", "Deck": "B", "Count": 4, "Share_%": 40.0, "Rank": 2},
        ]
    )
    canonical = adapt_top_meta_decklist(meta)
    legacy = adapt_top_meta_decklist(meta, include_legacy_share=True)
    assert tuple(canonical.columns) == TOP_META_COLUMNS
    assert "Share" not in canonical.columns
    assert legacy["Share"].tolist() == ["60.00%", "40.00%"]


def test_matchup_adapter_recomputes_n_and_wr_from_counts():
    raw = observed_matchups()
    raw["N"] = 999
    raw["WR_dir"] = -1
    out = adapt_matchup_raw(raw)
    ab = out.set_index(["Deck A", "Deck B"]).loc[("A", "B")]
    assert tuple(out.columns) == MATCHUP_COLUMNS
    assert int(ab["N"]) == 4
    assert ab["WR_dir"] == pytest.approx(100 * 2 / 3)


def test_dense_contract_materializes_t_times_t_minus_1_in_axis_order():
    dense = materialize_dense_score(observed_matchups(), ["A", "B", "C"])
    assert tuple(dense.columns) == DENSE_SCORE_COLUMNS
    assert len(dense) == 3 * 2
    assert list(zip(dense["Deck A"], dense["Deck B"])) == [
        ("A", "B"), ("A", "C"), ("B", "A"), ("B", "C"), ("C", "A"), ("C", "B")
    ]
    assert not (dense["Deck A"] == dense["Deck B"]).any()


def test_dense_contract_preserves_tie_only_as_evidence():
    dense = materialize_dense_score(observed_matchups(), ["A", "B", "C"])
    ac = dense.set_index(["Deck A", "Deck B"]).loc[("A", "C")]
    assert (int(ac["W"]), int(ac["L"]), int(ac["T"]), int(ac["N"])) == (0, 0, 2, 2)
    assert np.isnan(ac["WR_dir"])


def test_dense_contract_materializes_zero_evidence_without_inventing_games():
    dense = materialize_dense_score(observed_matchups(), ["A", "B", "C"])
    bc = dense.set_index(["Deck A", "Deck B"]).loc[("B", "C")]
    cb = dense.set_index(["Deck A", "Deck B"]).loc[("C", "B")]
    assert (int(bc["W"]), int(bc["L"]), int(bc["T"]), int(bc["N"])) == (0, 0, 0, 0)
    assert (int(cb["W"]), int(cb["L"]), int(cb["T"]), int(cb["N"])) == (0, 0, 0, 0)
    assert np.isnan(bc["WR_dir"])
    assert np.isnan(cb["WR_dir"])


def test_contract_artifact_hashes_and_counts_are_built_from_canonical_frames():
    meta = pd.DataFrame([{"Rank": 1, "Deck ID": "a", "Deck": "A", "Count": 1, "Share_%": 100.0}])
    matchup = adapt_matchup_raw(observed_matchups())
    dense = materialize_dense_score(matchup, ["A", "B", "C"])
    contracts = build_acquisition_contracts(meta, matchup, dense)
    assert contracts.dense_score.row_count == 6
    assert len(contracts.dense_score.sha256) == 64


def test_public_contracts_never_expose_player_ids():
    meta = pd.DataFrame([{"Rank": 1, "Deck ID": "a", "Deck": "A", "Count": 1, "Share_%": 100.0}])
    top = adapt_top_meta_decklist(meta)
    matchup = adapt_matchup_raw(observed_matchups())
    dense = materialize_dense_score(matchup, ["A", "B", "C"])
    for frame in (top, matchup, dense):
        assert not any("player" in column.lower() for column in frame.columns)


def test_manifest_serialization_has_required_nested_sections_and_no_player_ids():
    meta = pd.DataFrame([{"Rank": 1, "Deck ID": "a", "Deck": "A", "Count": 1, "Share_%": 100.0}])
    matchup = adapt_matchup_raw(observed_matchups())
    dense = materialize_dense_score(matchup, ["A", "B", "C"])
    contracts = build_acquisition_contracts(meta, matchup, dense)
    started = datetime(2026, 8, 22, 12, tzinfo=UTC)
    manifest = AcquisitionManifest(
        schema_version="1.0",
        run_id="run-1",
        created_at=datetime(2026, 8, 22, 12, 1, tzinfo=UTC),
        acquisition_started_at=started,
        source="limitless-tournament-api",
        software_git_revision="deadbeef",
        scope=ScopePolicy("pocket_release_window_v1", "POCKET", "STANDARD", "B4", "Set", datetime(2026, 8, 1, tzinfo=UTC), started, "cat-v1"),
        selection=TournamentSelection(("t1",), {"wrong_game": 0}),
        raw=RawSummary((RawPayloadRef("details", "s1", "a" * 64, started, "tournaments/t1/details.json", "t1"),)),
        normalized=NormalizedSummary(1, 2, 1, {"participants": "b" * 64}),
        aggregation=AggregationSummary(2, 2, 0, 1, {"bye": 0}),
        rate_limit_observations=({"status_code": 200, "remaining": "9"},),
        contracts=contracts,
    )
    payload = manifest.to_dict()
    validate_manifest_dict(payload)
    encoded = manifest.to_json()
    json.loads(encoded)
    assert payload["selection"]["included_count"] == 1
    assert payload["scope"]["start"].endswith("Z")
    assert "player_id" not in encoded.lower()
