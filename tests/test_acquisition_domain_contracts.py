from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from acquisition.contracts import (
    AcquisitionContracts,
    ContractArtifact,
    DENSE_SCORE_COLUMNS,
    MATCHUP_COLUMNS,
    RawPayloadRef,
    TOP_META_COLUMNS,
)
from acquisition.manifest import AcquisitionManifest, AggregationSummary, NormalizedSummary, RawSummary
from acquisition.scope import EligibilityPolicy, ScopePolicy
from acquisition.selection import TournamentSelection
from domain.releases import ExpansionRelease


def utc_dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


def test_expansion_release_normalizes_aware_datetime_to_utc():
    release = ExpansionRelease(
        code="B3b",
        name="Everyday Wonders",
        release_datetime=datetime(2026, 7, 10, 2, tzinfo=timezone(timedelta(hours=2))),
        next_release_datetime=utc_dt(20),
        is_current=False,
        source="limitless-pocket-database",
        catalog_version="2026-08-22",
    )

    assert release.release_datetime == datetime(2026, 7, 10, 0, tzinfo=UTC)
    assert release.release_datetime.tzinfo is UTC


def test_expansion_release_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        ExpansionRelease(
            code="B4",
            name="Current",
            release_datetime=datetime(2026, 8, 1),
            next_release_datetime=None,
            is_current=True,
            source="official",
            catalog_version="v1",
        )


def test_completed_and_current_release_shapes_are_consistent():
    with pytest.raises(ValueError, match="completed expansion"):
        ExpansionRelease("B3b", "Old", utc_dt(1), None, False, "official", "v1")
    with pytest.raises(ValueError, match="current expansion"):
        ExpansionRelease("B4", "Current", utc_dt(1), utc_dt(2), True, "official", "v1")


def test_scope_policy_is_half_open_ready_and_normalized():
    scope = ScopePolicy(
        policy_id="pocket_release_window_v1",
        game="pocket",
        format="standard",
        set_code="B3b",
        set_name="Everyday Wonders",
        start_datetime=utc_dt(1),
        end_datetime=utc_dt(2),
        catalog_version="v1",
    )

    assert scope.game == "POCKET"
    assert scope.format == "STANDARD"
    assert scope.start_datetime < scope.end_datetime


def test_scope_policy_rejects_empty_or_reversed_window():
    with pytest.raises(ValueError, match="after start_datetime"):
        ScopePolicy("v1", "POCKET", "STANDARD", "B4", "Set", utc_dt(2), utc_dt(1), "v1")


def test_eligibility_policy_matches_frozen_pocket_v1_defaults():
    policy = EligibilityPolicy()

    assert policy.game == "POCKET"
    assert policy.allowed_formats == (None, "STANDARD")
    assert policy.require_public is True
    assert policy.require_decklists is True


def test_tournament_selection_requires_sorted_unique_ids_and_freezes_diagnostics():
    selection = TournamentSelection(
        tournament_ids=("aaa", "bbb"),
        exclusion_counts={"wrong_game": 2, "outside_window": 3},
        failures=("fetch:xyz",),
    )

    assert selection.included_count == 2
    with pytest.raises(TypeError):
        selection.exclusion_counts["wrong_game"] = 99
    with pytest.raises(FrozenInstanceError):
        selection.tournament_ids = ("zzz",)

    with pytest.raises(ValueError, match="sorted"):
        TournamentSelection(("bbb", "aaa"), {}, ())
    with pytest.raises(ValueError, match="unique"):
        TournamentSelection(("aaa", "aaa"), {}, ())


def test_raw_payload_ref_requires_safe_relative_path_sha_and_utc():
    ref = RawPayloadRef(
        payload_type="standings",
        tournament_id="abc",
        snapshot_id="snap-1",
        sha256="a" * 64,
        fetched_at=utc_dt(1),
        relative_path="tournaments/abc/snapshots/snap-1/standings.json",
    )

    assert ref.relative_path.endswith("standings.json")
    assert ref.fetched_at.tzinfo is UTC
    with pytest.raises(ValueError, match="inside the raw store"):
        RawPayloadRef("details", "snap", "b" * 64, utc_dt(1), "../details.json", "abc")


def _contracts() -> AcquisitionContracts:
    return AcquisitionContracts(
        top_meta_decklist=ContractArtifact("top_meta_decklist", TOP_META_COLUMNS, 10, "1" * 64),
        matchup_raw=ContractArtifact("matchup_raw", MATCHUP_COLUMNS, 20, "2" * 64),
        dense_score=ContractArtifact("dense_score", DENSE_SCORE_COLUMNS, 90, "3" * 64),
    )


def test_acquisition_contracts_pin_canonical_columns():
    contracts = _contracts()
    assert contracts.top_meta_decklist.columns == TOP_META_COLUMNS
    assert contracts.matchup_raw.columns[-1] == "WR_dir"

    with pytest.raises(ValueError, match="canonical acquisition contract"):
        AcquisitionContracts(
            top_meta_decklist=ContractArtifact("top", ("Deck",), 1),
            matchup_raw=ContractArtifact("matchup", MATCHUP_COLUMNS, 1),
            dense_score=ContractArtifact("dense", DENSE_SCORE_COLUMNS, 1),
        )


def test_manifest_serialization_is_json_compatible_and_has_no_player_ids():
    scope = ScopePolicy(
        "pocket_release_window_v1",
        "POCKET",
        "STANDARD",
        "B4",
        "Current",
        utc_dt(1),
        utc_dt(2),
        "catalog-v1",
    )
    manifest = AcquisitionManifest(
        schema_version="1.0",
        run_id="run-20260822",
        created_at=utc_dt(2, 1),
        acquisition_started_at=utc_dt(2),
        source="limitless-tournament-api",
        software_git_revision="deadbeef",
        scope=scope,
        selection=TournamentSelection(("t1", "t2"), {"wrong_game": 1}),
        raw=RawSummary((
            RawPayloadRef("details", "s1", "a" * 64, utc_dt(2), "tournaments/t1/details.json", "t1"),
        )),
        normalized=NormalizedSummary(2, 20, 40, {"tournaments": "b" * 64}),
        aggregation=AggregationSummary(20, 18, 2, 30, {"bye": 1}),
        rate_limit_observations=({"remaining": 42},),
        contracts=_contracts(),
    )

    data = manifest.to_dict()
    import json
    json.dumps(data, sort_keys=True)
    assert data["created_at"].endswith("Z")
    assert data["selection"]["tournament_ids"] == ["t1", "t2"]
    assert "player_id" not in str(data)


def test_aggregation_summary_reconciles_classification_counts():
    with pytest.raises(ValueError, match=r"classified \+ unclassified"):
        AggregationSummary(10, 8, 1, 4, {})
