from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from pipelines.limitless_api_acquisition import run_limitless_api_acquisition
from sources.limitless.tournament_api.release_catalog import load_release_catalog_snapshot


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "reference" / "pocket_releases.json"
STARTED = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 22, 12, 5, tzinfo=UTC)


def _details(
    tid: str,
    date: str,
    *,
    fmt: str | None = "STANDARD",
    public: bool = True,
    decklists: bool = True,
    players: int = 2,
):
    return {
        "id": tid,
        "game": "POCKET",
        "format": fmt,
        "name": f"Tournament {tid}",
        "date": date,
        "players": players,
        "organizer": {"id": 7, "name": "Fixture Org"},
        "platform": "PTCGP",
        "decklists": decklists,
        "isPublic": public,
        "isOnline": True,
        "phases": [{"phase": 1, "type": "SWISS", "rounds": 1, "mode": "BO1"}],
        "bannedCards": [],
        "specialRules": [],
    }


def _standings(deck_a=("deck-a", "Deck A"), deck_b=("deck-b", "Deck B")):
    return [
        {
            "player": "p1",
            "placing": 1,
            "record": {"wins": 1, "losses": 0, "ties": 0},
            "decklist": {"cards": []},
            "deck": {"id": deck_a[0], "name": deck_a[1]},
            "drop": None,
        },
        {
            "player": "p2",
            "placing": 2,
            "record": {"wins": 0, "losses": 1, "ties": 0},
            "decklist": {"cards": []},
            "deck": {"id": deck_b[0], "name": deck_b[1]},
            "drop": None,
        },
    ]


def _pairings(*, winner="p1"):
    return [
        {
            "phase": 1,
            "round": 1,
            "table": 1,
            "player1": "p1",
            "player2": "p2",
            "winner": winner,
        }
    ]


class FakeClient:
    def __init__(self):
        self.calls = []
        self.rate_limit_observations = ()
        self.discovery = [
            {"id": "old", "game": "POCKET", "format": "STANDARD", "name": "Old", "date": "2026-06-29T23:00:00Z", "players": 2},
            {"id": "t-private", "game": "POCKET", "format": "STANDARD", "name": "Private", "date": "2026-07-08T12:00:00Z", "players": 2},
            {"id": "t-no-decks", "game": "POCKET", "format": "STANDARD", "name": "No decks", "date": "2026-07-09T12:00:00Z", "players": 2},
            {"id": "t-custom", "game": "POCKET", "format": "CUSTOM", "name": "Custom", "date": "2026-07-10T12:00:00Z", "players": 2},
            {"id": "t2", "game": "POCKET", "format": None, "name": "T2", "date": "2026-07-15T12:00:00Z", "players": 2},
            {"id": "t1", "game": "POCKET", "format": "STANDARD", "name": "T1", "date": "2026-07-05T12:00:00Z", "players": 2},
            {"id": "new-boundary", "game": "POCKET", "format": "STANDARD", "name": "Boundary", "date": "2026-07-30T01:00:00Z", "players": 2},
        ]
        self.details = {
            "t1": _details("t1", "2026-07-05T12:00:00Z"),
            "t2": _details("t2", "2026-07-15T12:00:00Z", fmt=None),
            "t-private": _details("t-private", "2026-07-08T12:00:00Z", public=False),
            "t-no-decks": _details("t-no-decks", "2026-07-09T12:00:00Z", decklists=False),
            "t-custom": _details("t-custom", "2026-07-10T12:00:00Z", fmt="CUSTOM"),
        }
        self.standings = {
            "t1": _standings(),
            "t2": _standings(("deck-a", "Deck A"), ("deck-c", "Deck C")),
        }
        self.pairings = {
            "t1": _pairings(winner="p1"),
            "t2": _pairings(winner=0),
        }

    def list_tournaments(self, **kwargs):
        self.calls.append(("list_tournaments", kwargs))
        return list(self.discovery)

    def get_tournament_details(self, tid, **kwargs):
        self.calls.append(("details", tid, kwargs))
        return dict(self.details[tid])

    def get_tournament_standings(self, tid, **kwargs):
        self.calls.append(("standings", tid, kwargs))
        return [dict(row) for row in self.standings[tid]]

    def get_tournament_pairings(self, tid, **kwargs):
        self.calls.append(("pairings", tid, kwargs))
        return [dict(row) for row in self.pairings[tid]]


class ExplodingClient:
    def __init__(self):
        self.calls = 0

    def __getattr__(self, name):
        if name == "rate_limit_observations":
            return ()
        self.calls += 1
        raise AssertionError(f"network/client access forbidden in offline replay: {name}")


def _run_live(tmp_path, *, run_id="live-b3b", client=None):
    return run_limitless_api_acquisition(
        game="POCKET",
        format="STANDARD",
        set_mode="code",
        set_code="B3b",
        acquisition_started_at=STARTED,
        execution_mode="live",
        raw_store_root=tmp_path / "store",
        release_catalog=CATALOG_PATH,
        client=client or FakeClient(),
        run_id=run_id,
        software_git_revision="38d14a3",
        now_fn=lambda: NOW,
    )


def test_reference_catalog_has_verified_b3b_b4_boundaries():
    catalog = load_release_catalog_snapshot(CATALOG_PATH)
    by_code = {release.code: release for release in catalog.releases}
    assert by_code["B3b"].release_datetime == datetime(2026, 6, 30, 1, 0, tzinfo=UTC)
    assert by_code["B3b"].next_release_datetime == datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    assert by_code["B4"].release_datetime == datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    assert by_code["B4"].is_current is True


def test_fixture_to_full_b8_pipeline_and_deterministic_selection(tmp_path):
    client = FakeClient()
    result = _run_live(tmp_path, client=client)

    assert result.manifest.scope.start_datetime == datetime(2026, 6, 30, 1, 0, tzinfo=UTC)
    assert result.manifest.scope.end_datetime == datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    assert result.manifest.selection.tournament_ids == ("t1", "t2")
    assert result.manifest.selection.exclusion_counts["not_public"] == 1
    assert result.manifest.selection.exclusion_counts["decklists_disabled"] == 1
    assert result.manifest.selection.exclusion_counts["wrong_format"] == 1
    assert result.diagnostics["normalized_row_counts"] == {
        "tournaments": 2,
        "participants": 4,
        "pairings": 2,
    }
    assert result.diagnostics["classified_participants"] == 4
    assert result.diagnostics["known_deck_matches"] == 2
    assert result.diagnostics["meta_rows"] == 3
    assert result.diagnostics["pairing_diagnostics"]["ties"] == 1
    assert result.contracts.top_meta_decklist.row_count == 3
    assert result.contracts.matchup_raw.row_count == 4
    assert result.contracts.dense_score.row_count == 6


def test_manifest_is_populated_and_public(tmp_path):
    result = _run_live(tmp_path)
    payload = result.manifest.to_dict()
    raw = json.dumps(payload, sort_keys=True).lower()

    assert payload["source"] == "Limitless Tournament API"
    assert payload["software"]["git_revision"] == "38d14a3"
    assert payload["scope"]["catalog_version"] == "pocket-releases-2026-08-22-v2"
    assert payload["selection"]["tournament_ids"] == ["t1", "t2"]
    assert len(payload["raw"]["snapshot_refs"]) == 9
    assert set(payload["normalized"]["hashes"]) == {"tournaments", "participants", "pairings"}
    assert payload["contracts"]["top_meta_decklist"]["sha256"]
    assert "player_id" not in raw



def test_duplicate_display_name_diagnostics_are_public_without_collapsing_ids(tmp_path):
    client = FakeClient()
    client.standings["t1"] = _standings(("dragon-1", "Dragonair Altaria"), ("other", "Other"))
    client.standings["t2"] = _standings(("dragon-2", "Dragonair Altaria"), ("deck-c", "Deck C"))

    result = _run_live(tmp_path, client=client)

    expected = {"Dragonair Altaria": ["dragon-1", "dragon-2"]}
    assert result.diagnostics["deck_identity_diagnostics"]["duplicate_display_names"] == expected
    assert result.manifest.to_dict()["aggregation"]["deck_identity_diagnostics"]["duplicate_display_names"] == expected
    assert result.contracts.top_meta_decklist.row_count == 4
    assert "player_id" not in result.manifest.to_json().lower()

def test_offline_replay_zero_network_and_semantic_hash_equality(tmp_path):
    live = _run_live(tmp_path)
    exploding = ExplodingClient()
    replay = run_limitless_api_acquisition(
        game="POCKET",
        set_code="B3b",
        acquisition_started_at=STARTED,
        execution_mode="offline",
        raw_store_root=tmp_path / "store",
        release_catalog=CATALOG_PATH,
        client=exploding,
        replay_run_id="live-b3b",
        run_id="replay-b3b",
        software_git_revision="38d14a3",
        now_fn=lambda: datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
    )

    assert exploding.calls == 0
    assert replay.diagnostics["network_calls"] == 0
    assert replay.manifest.selection.tournament_ids == live.manifest.selection.tournament_ids
    assert dict(replay.manifest.normalized.hashes) == dict(live.manifest.normalized.hashes)
    assert replay.diagnostics["contract_hashes"] == live.diagnostics["contract_hashes"]
    assert replay.diagnostics["normalized_row_counts"] == live.diagnostics["normalized_row_counts"]


def test_offline_replay_missing_raw_ref_fails_explicitly(tmp_path):
    live = _run_live(tmp_path)
    ref = next(ref for ref in live.manifest.raw.snapshot_refs if ref.tournament_id == "t1" and ref.payload_type == "standings")
    (tmp_path / "store" / ref.relative_path).unlink()

    with pytest.raises(FileNotFoundError, match="offline replay missing raw ref"):
        run_limitless_api_acquisition(
            game="POCKET",
            set_code="B3b",
            acquisition_started_at=STARTED,
            execution_mode="offline",
            raw_store_root=tmp_path / "store",
            release_catalog=CATALOG_PATH,
            replay_run_id="live-b3b",
            run_id="replay-missing",
            software_git_revision="38d14a3",
            now_fn=lambda: datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
        )


def test_current_set_freezes_acquisition_started_at_in_manifest_and_replay(tmp_path):
    started = datetime(2026, 8, 10, 10, 30, tzinfo=UTC)
    now = datetime(2026, 8, 10, 10, 35, tzinfo=UTC)
    client = FakeClient()
    client.discovery = [
        {"id": "before-b4", "game": "POCKET", "format": "STANDARD", "name": "Old", "date": "2026-07-29T20:00:00Z", "players": 2},
        {"id": "b4-t1", "game": "POCKET", "format": "STANDARD", "name": "B4", "date": "2026-08-01T12:00:00Z", "players": 2},
        {"id": "after-start", "game": "POCKET", "format": "STANDARD", "name": "Future", "date": "2026-08-10T10:31:00Z", "players": 2},
    ]
    client.details = {"b4-t1": _details("b4-t1", "2026-08-01T12:00:00Z")}
    client.standings = {"b4-t1": _standings()}
    client.pairings = {"b4-t1": _pairings()}

    live = run_limitless_api_acquisition(
        game="POCKET",
        set_mode="code",
        set_code="B4",
        acquisition_started_at=started,
        execution_mode="live",
        raw_store_root=tmp_path / "store",
        release_catalog=CATALOG_PATH,
        client=client,
        run_id="live-b4",
        software_git_revision="38d14a3",
        now_fn=lambda: now,
    )
    assert live.manifest.scope.end_datetime == started
    assert live.manifest.acquisition_started_at == started
    assert live.manifest.selection.tournament_ids == ("b4-t1",)

    replay = run_limitless_api_acquisition(
        game="POCKET",
        set_code="B4",
        acquisition_started_at=started,
        execution_mode="offline",
        raw_store_root=tmp_path / "store",
        release_catalog=CATALOG_PATH,
        replay_run_id="live-b4",
        run_id="replay-b4",
        software_git_revision="38d14a3",
        now_fn=lambda: datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )
    assert replay.manifest.scope.end_datetime == started
    assert replay.manifest.acquisition_started_at == started


def test_offline_replay_rejects_changed_acquisition_started_at(tmp_path):
    _run_live(tmp_path)
    with pytest.raises(ValueError, match="must match the frozen source manifest"):
        run_limitless_api_acquisition(
            game="POCKET",
            set_code="B3b",
            acquisition_started_at=datetime(2026, 8, 22, 12, 1, tzinfo=UTC),
            execution_mode="offline",
            raw_store_root=tmp_path / "store",
            release_catalog=CATALOG_PATH,
            replay_run_id="live-b3b",
            run_id="replay-bad-time",
            software_git_revision="38d14a3",
            now_fn=lambda: datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
        )


def test_live_reuses_stable_raw_without_tournament_payload_calls(tmp_path):
    first_client = FakeClient()
    first = _run_live(tmp_path, run_id="live-1", client=first_client)

    second_client = FakeClient()
    second = _run_live(tmp_path, run_id="live-2", client=second_client)

    payload_calls = [
        call
        for call in second_client.calls
        if call[0] in {"details", "standings", "pairings"}
    ]

    # t1/t2 were selected in the first run and therefore have valid raw.
    # At age >=72h they must produce zero tournament payload GETs.
    stable_payload_calls = [
        call
        for call in payload_calls
        if call[1] in {"t1", "t2"}
    ]
    assert stable_payload_calls == []

    # Excluded discovery candidates have no frozen tournament snapshot.
    # They must conservatively re-fetch details so eligibility can be
    # evaluated again, but never reach standings/pairings when excluded.
    nonselected_payload_calls = [
        call
        for call in payload_calls
        if call[1] in {"t-private", "t-no-decks", "t-custom"}
    ]
    assert {
        (call[0], call[1])
        for call in nonselected_payload_calls
    } == {
        ("details", "t-private"),
        ("details", "t-no-decks"),
        ("details", "t-custom"),
    }

    assert second.diagnostics["stability_horizon_hours"] == 72.0
    assert second.diagnostics["tournaments_stable_reused"] == 2
    assert second.diagnostics["reused_tournament_snapshots"] == 2
    assert (
        second.diagnostics["normalized_hashes"]
        == first.diagnostics["normalized_hashes"]
    )
    assert (
        second.diagnostics["contract_hashes"]
        == first.diagnostics["contract_hashes"]
    )


def test_public_contract_columns_do_not_expose_player_ids(tmp_path):
    result = _run_live(tmp_path)
    for artifact in (
        result.contracts.top_meta_decklist,
        result.contracts.matchup_raw,
        result.contracts.dense_score,
    ):
        assert all("player" not in column.lower() for column in artifact.columns)


def test_runtime_frames_are_present_and_match_contract_hashes(tmp_path):
    from acquisition.contracts import hash_dataframe

    result = _run_live(tmp_path)
    assert result.frames.top_meta_decklist is not None
    assert result.frames.matchup_raw is not None
    assert result.frames.dense_score is not None
    assert hash_dataframe(result.frames.top_meta_decklist) == result.contracts.top_meta_decklist.sha256
    assert hash_dataframe(result.frames.matchup_raw) == result.contracts.matchup_raw.sha256
    assert hash_dataframe(result.frames.dense_score) == result.contracts.dense_score.sha256


def test_offline_replay_runtime_frames_are_semantically_identical(tmp_path):
    live = _run_live(tmp_path)
    replay = run_limitless_api_acquisition(
        game="POCKET",
        set_code="B3b",
        acquisition_started_at=STARTED,
        execution_mode="offline",
        raw_store_root=tmp_path / "store",
        release_catalog=CATALOG_PATH,
        replay_run_id="live-b3b",
        run_id="replay-frames",
        software_git_revision="38d14a3",
        now_fn=lambda: datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
    )

    pd = pytest.importorskip("pandas")
    pd.testing.assert_frame_equal(
        live.frames.top_meta_decklist,
        replay.frames.top_meta_decklist,
    )
    pd.testing.assert_frame_equal(live.frames.matchup_raw, replay.frames.matchup_raw)
    pd.testing.assert_frame_equal(live.frames.dense_score, replay.frames.dense_score)


def test_runtime_frames_are_public_and_preserve_duplicate_display_names_by_id(tmp_path):
    client = FakeClient()
    client.standings["t1"] = _standings(("dragon-1", "Dragonair Altaria"), ("other", "Other"))
    client.standings["t2"] = _standings(("dragon-2", "Dragonair Altaria"), ("deck-c", "Deck C"))

    result = _run_live(tmp_path, client=client)
    frames = result.frames

    for df in (frames.top_meta_decklist, frames.matchup_raw, frames.dense_score):
        assert all("player" not in str(column).lower() for column in df.columns)

    dragon = frames.top_meta_decklist[frames.top_meta_decklist["Deck"] == "Dragonair Altaria"]
    assert set(dragon["Deck ID"]) == {"dragon-1", "dragon-2"}
    assert len(dragon) == 2


def test_live_and_offline_replay_propagate_pairing_normalization_diagnostics(tmp_path):
    client = FakeClient()
    client.pairings["t1"] = [
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

    live = _run_live(tmp_path, client=client, run_id="live-pairing-normalization")
    exploding = ExplodingClient()
    replay = run_limitless_api_acquisition(
        game="POCKET",
        format="STANDARD",
        set_code="B3b",
        acquisition_started_at=STARTED,
        execution_mode="offline",
        raw_store_root=tmp_path / "store",
        release_catalog=CATALOG_PATH,
        client=exploding,
        replay_run_id="live-pairing-normalization",
        run_id="replay-pairing-normalization",
        software_git_revision="38d14a3",
        now_fn=lambda: datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
    )

    expected = {
        "canonicalized_player2_bye_count": 1,
        "excluded_pairing_no_players_count": 1,
        "pairing_base_collision_count": 0,
        "pairing_rematch_occurrence_count": 0,
        "pairing_match_discriminator_count": 0,
        "pairing_table_fallback_count": 0,
        "pairing_deduplicated_count": 0,
        "pairing_unresolved_conflict_count": 0,
    }
    assert live.diagnostics["normalization_diagnostics"] == expected
    assert live.manifest.to_dict()["normalized"]["diagnostics"] == expected
    assert live.diagnostics["pairing_diagnostics"]["byes"] == 1
    assert live.diagnostics["known_deck_matches"] == 2

    assert exploding.calls == 0
    assert replay.diagnostics["network_calls"] == 0
    assert replay.diagnostics["normalization_diagnostics"] == expected
    assert replay.manifest.to_dict()["normalized"]["diagnostics"] == expected
    assert replay.diagnostics["known_deck_matches"] == live.diagnostics["known_deck_matches"]
    assert replay.diagnostics["normalized_hashes"] == live.diagnostics["normalized_hashes"]
    assert replay.diagnostics["contract_hashes"] == live.diagnostics["contract_hashes"]


def test_hierarchical_pairing_occurrence_preserves_legitimate_rematch_and_replays_identically(tmp_path):
    client = FakeClient()
    client.pairings["t1"] = [
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
    before = json.loads(json.dumps(client.pairings["t1"]))

    live = _run_live(tmp_path, client=client, run_id="live-pairing-occurrence")
    replay = run_limitless_api_acquisition(
        game="POCKET",
        format="STANDARD",
        set_code="B3b",
        acquisition_started_at=STARTED,
        execution_mode="offline",
        raw_store_root=tmp_path / "store",
        release_catalog=CATALOG_PATH,
        client=ExplodingClient(),
        replay_run_id="live-pairing-occurrence",
        run_id="replay-pairing-occurrence",
        software_git_revision="38d14a3",
        now_fn=lambda: datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
    )

    # t1 contributes two legitimate rematch occurrences; t2 contributes one.
    assert live.diagnostics["known_deck_matches"] == 3
    assert client.pairings["t1"] == before
    norm_diag = live.diagnostics["normalization_diagnostics"]
    assert norm_diag["pairing_base_collision_count"] == 1
    assert norm_diag["pairing_rematch_occurrence_count"] == 1
    assert norm_diag["pairing_match_discriminator_count"] == 2
    assert norm_diag["pairing_deduplicated_count"] == 0
    assert live.manifest.to_dict()["normalized"]["diagnostics"] == norm_diag

    assert replay.diagnostics["network_calls"] == 0
    assert replay.diagnostics["known_deck_matches"] == live.diagnostics["known_deck_matches"]
    assert replay.diagnostics["normalization_diagnostics"] == norm_diag
    assert replay.diagnostics["normalized_hashes"] == live.diagnostics["normalized_hashes"]
    assert replay.diagnostics["contract_hashes"] == live.diagnostics["contract_hashes"]
