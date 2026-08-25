from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from pipelines.limitless_api_acquisition import (
    STABILITY_HORIZON_HOURS,
    _stability_action,
    _tournament_age_hours,
    run_limitless_api_acquisition,
)
from sources.limitless.tournament_api.raw_store import ImmutableRawStore


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "data" / "reference" / "pocket_releases.json"

RUN_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 25, 12, 5, tzinfo=UTC)


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _details(tid: str, date: datetime):
    return {
        "id": tid,
        "game": "POCKET",
        "format": "STANDARD",
        "name": f"Tournament {tid}",
        "date": _z(date),
        "players": 2,
        "organizer": {"id": 1, "name": "Fixture"},
        "platform": "PTCGP",
        "decklists": True,
        "isPublic": True,
        "isOnline": True,
        "phases": [
            {
                "phase": 1,
                "type": "SWISS",
                "rounds": 1,
                "mode": "BO1",
            }
        ],
        "bannedCards": [],
        "specialRules": [],
    }


def _discovery_row(tid: str, date: datetime):
    return {
        "id": tid,
        "game": "POCKET",
        "format": "STANDARD",
        "name": f"Tournament {tid}",
        "date": _z(date),
        "players": 2,
    }


def _standings(tid: str, *, deck_b: str = "deck-b"):
    return [
        {
            "player": f"{tid}-p1",
            "placing": 1,
            "record": {"wins": 1, "losses": 0, "ties": 0},
            "decklist": {"cards": []},
            "deck": {"id": "deck-a", "name": "Deck A"},
            "drop": None,
        },
        {
            "player": f"{tid}-p2",
            "placing": 2,
            "record": {"wins": 0, "losses": 1, "ties": 0},
            "decklist": {"cards": []},
            "deck": {"id": deck_b, "name": "Deck B"},
            "drop": None,
        },
    ]


def _pairings(tid: str, *, winner: str | None = None):
    return [
        {
            "phase": 1,
            "round": 1,
            "table": 1,
            "player1": f"{tid}-p1",
            "player2": f"{tid}-p2",
            "winner": winner or f"{tid}-p1",
        }
    ]


class StabilityClient:
    def __init__(
        self,
        discovery,
        *,
        details=None,
        standings=None,
        pairings=None,
    ):
        self.discovery = list(discovery)
        self.details = dict(details or {})
        self.standings = dict(standings or {})
        self.pairings = dict(pairings or {})
        self.calls = []
        self.rate_limit_observations = ()
        self.cache = None

    def list_tournaments(self, **kwargs):
        self.calls.append(("discovery", kwargs))
        return [dict(row) for row in self.discovery]

    def get_tournament_details(self, tid, **kwargs):
        self.calls.append(("details", tid, kwargs))
        if tid not in self.details:
            raise AssertionError(f"unexpected details call for {tid}")
        return dict(self.details[tid])

    def get_tournament_standings(self, tid, **kwargs):
        self.calls.append(("standings", tid, kwargs))
        if tid not in self.standings:
            raise AssertionError(f"unexpected standings call for {tid}")
        return [dict(row) for row in self.standings[tid]]

    def get_tournament_pairings(self, tid, **kwargs):
        self.calls.append(("pairings", tid, kwargs))
        if tid not in self.pairings:
            raise AssertionError(f"unexpected pairings call for {tid}")
        return [dict(row) for row in self.pairings[tid]]


class ExplodingClient:
    def __init__(self):
        self.calls = 0
        self.rate_limit_observations = ()
        self.cache = None

    def __getattr__(self, name):
        self.calls += 1
        raise AssertionError(
            f"network/client access forbidden in offline replay: {name}"
        )


def _seed_raw(
    raw_root: Path,
    tid: str,
    date: datetime,
    *,
    standings=None,
    pairings=None,
):
    store = ImmutableRawStore(raw_root)

    return store.save_tournament_snapshot(
        tid,
        details=_details(tid, date),
        standings=standings or _standings(tid),
        pairings=pairings or _pairings(tid),
        fetched_at=RUN_AT - timedelta(minutes=10),
    )


def _run_live(
    raw_root: Path,
    client,
    run_id: str,
):
    return run_limitless_api_acquisition(
        game="POCKET",
        format="STANDARD",
        set_mode="code",
        set_code="B4",
        acquisition_started_at=RUN_AT,
        execution_mode="live",
        raw_store_root=raw_root,
        release_catalog=CATALOG,
        client=client,
        cache_ttl_min=0,
        run_id=run_id,
        software_git_revision="rf1-corr-test",
        discovery_page_size=200,
        reuse_latest_raw=True,
        now_fn=lambda: NOW,
    )


def _snapshot_id(result, tid: str):
    ids = {
        ref.snapshot_id
        for ref in result.manifest.raw.snapshot_refs
        if ref.tournament_id == tid
    }
    assert len(ids) == 1
    return next(iter(ids))


def _payload_calls(client, tid: str | None = None):
    rows = [
        call
        for call in client.calls
        if call[0] in {"details", "standings", "pairings"}
    ]

    if tid is None:
        return rows

    return [
        call
        for call in rows
        if call[1] == tid
    ]


def _fetch_maps(tid: str, date: datetime):
    return {
        "details": {tid: _details(tid, date)},
        "standings": {tid: _standings(tid)},
        "pairings": {tid: _pairings(tid)},
    }


def test_frozen_stability_horizon_and_http_cache_default():
    cfg = yaml.safe_load(
        (REPO_ROOT / "config" / "pocket.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert STABILITY_HORIZON_HOURS == 72.0
    assert cfg["source"]["acquisition"] == "tournament_api"
    assert cfg["source"]["tournament_api"]["cache_ttl_min"] == 0


def test_age_71h59_with_raw_refetches_all_payloads(tmp_path):
    tid = "recent-7159"
    date = RUN_AT - timedelta(hours=71, minutes=59)
    raw_root = tmp_path / "raw"

    _seed_raw(raw_root, tid, date)
    maps = _fetch_maps(tid, date)

    client = StabilityClient(
        [_discovery_row(tid, date)],
        **maps,
    )

    result = _run_live(raw_root, client, "recent-7159")

    assert [call[0] for call in _payload_calls(client, tid)] == [
        "details",
        "standings",
        "pairings",
    ]
    assert result.diagnostics["tournaments_recent_refetched"] == 1
    assert result.diagnostics["tournaments_stable_reused"] == 0


def test_exact_72h_with_valid_raw_reuses_without_payload_calls(tmp_path):
    tid = "stable-72"
    date = RUN_AT - timedelta(hours=72)
    raw_root = tmp_path / "raw"

    _seed_raw(raw_root, tid, date)

    client = StabilityClient(
        [_discovery_row(tid, date)],
    )

    result = _run_live(raw_root, client, "stable-72")

    assert _payload_calls(client, tid) == []
    assert result.diagnostics["stability_horizon_hours"] == 72.0
    assert result.diagnostics["tournaments_stable_reused"] == 1
    assert result.diagnostics["tournaments_recent_refetched"] == 0


def test_over_72h_with_valid_raw_reuses_without_payload_calls(tmp_path):
    tid = "stable-old"
    date = RUN_AT - timedelta(days=10)
    raw_root = tmp_path / "raw"

    _seed_raw(raw_root, tid, date)

    client = StabilityClient(
        [_discovery_row(tid, date)],
    )

    result = _run_live(raw_root, client, "stable-old")

    assert _payload_calls(client, tid) == []
    assert result.diagnostics["tournaments_stable_reused"] == 1


def test_over_72h_without_raw_fetches_all_payloads(tmp_path):
    tid = "new-old"
    date = RUN_AT - timedelta(days=10)
    raw_root = tmp_path / "raw"

    maps = _fetch_maps(tid, date)

    client = StabilityClient(
        [_discovery_row(tid, date)],
        **maps,
    )

    result = _run_live(raw_root, client, "new-old")

    assert [call[0] for call in _payload_calls(client, tid)] == [
        "details",
        "standings",
        "pairings",
    ]
    assert result.diagnostics["tournaments_new"] == 1
    assert result.diagnostics["tournaments_stable_reused"] == 0


def test_missing_or_unparseable_date_is_conservative_fetch_action():
    missing_age = _tournament_age_hours(
        {"date": None},
        acquisition_started_at=RUN_AT,
    )
    invalid_age = _tournament_age_hours(
        {"date": "not-a-date"},
        acquisition_started_at=RUN_AT,
    )

    assert missing_age is None
    assert invalid_age is None

    assert _stability_action(
        age_hours=missing_age,
        has_valid_raw=True,
    ) == "conservative"

    assert _stability_action(
        age_hours=invalid_age,
        has_valid_raw=True,
    ) == "conservative"


def test_mixed_100_tournaments_fetches_only_5(tmp_path):
    raw_root = tmp_path / "raw"

    stable_ids = [f"s{i:03d}" for i in range(95)]
    recent_ids = [f"r{i}" for i in range(3)]
    new_ids = [f"n{i}" for i in range(2)]

    stable_date = RUN_AT - timedelta(days=10)

    discovery = []
    details = {}
    standings = {}
    pairings = {}

    for tid in stable_ids:
        discovery.append(_discovery_row(tid, stable_date))
        _seed_raw(raw_root, tid, stable_date)

    for index, tid in enumerate(recent_ids, start=1):
        date = RUN_AT - timedelta(hours=index)
        discovery.append(_discovery_row(tid, date))

        # Recent has valid prior raw but must still be fully refreshed.
        _seed_raw(raw_root, tid, date)

        details[tid] = _details(tid, date)
        standings[tid] = _standings(tid)
        pairings[tid] = _pairings(tid)

    for tid in new_ids:
        date = RUN_AT - timedelta(days=10)
        discovery.append(_discovery_row(tid, date))

        details[tid] = _details(tid, date)
        standings[tid] = _standings(tid)
        pairings[tid] = _pairings(tid)

    client = StabilityClient(
        discovery,
        details=details,
        standings=standings,
        pairings=pairings,
    )

    result = _run_live(raw_root, client, "mixed-100")

    payload_calls = _payload_calls(client)

    assert result.diagnostics["tournaments_discovered"] == 100
    assert result.manifest.selection.included_count == 100

    assert result.diagnostics["tournaments_stable_reused"] == 95
    assert result.diagnostics["tournaments_recent_refetched"] == 3
    assert result.diagnostics["tournaments_new"] == 2
    assert result.diagnostics["tournaments_conservative_refetched"] == 0

    assert sum(call[0] == "details" for call in payload_calls) == 5
    assert sum(call[0] == "standings" for call in payload_calls) == 5
    assert sum(call[0] == "pairings" for call in payload_calls) == 5

    stable_payload_calls = [
        call
        for call in payload_calls
        if call[1] in set(stable_ids)
    ]
    assert stable_payload_calls == []


def test_discovery_is_fresh_and_second_run_can_find_new_tournament(
    tmp_path,
):
    raw_root = tmp_path / "raw"

    tid_a = "A"
    tid_b = "B"
    date_a = RUN_AT - timedelta(hours=2)
    date_b = RUN_AT - timedelta(hours=1)

    maps_a = _fetch_maps(tid_a, date_a)
    client1 = StabilityClient(
        [_discovery_row(tid_a, date_a)],
        **maps_a,
    )

    first = _run_live(raw_root, client1, "discovery-1")

    details = {
        tid_a: _details(tid_a, date_a),
        tid_b: _details(tid_b, date_b),
    }
    standings = {
        tid_a: _standings(tid_a),
        tid_b: _standings(tid_b),
    }
    pairings = {
        tid_a: _pairings(tid_a),
        tid_b: _pairings(tid_b),
    }

    client2 = StabilityClient(
        [
            _discovery_row(tid_a, date_a),
            _discovery_row(tid_b, date_b),
        ],
        details=details,
        standings=standings,
        pairings=pairings,
    )

    second = _run_live(raw_root, client2, "discovery-2")

    assert first.manifest.selection.tournament_ids == (tid_a,)
    assert second.manifest.selection.tournament_ids == (tid_a, tid_b)

    discovery_call = next(
        call
        for call in client2.calls
        if call[0] == "discovery"
    )

    assert discovery_call[1]["use_cache"] is False


def test_recent_details_same_standings_changed_are_observed(tmp_path):
    tid = "recent-standings"
    date = RUN_AT - timedelta(hours=1)
    raw_root = tmp_path / "raw"

    old_snapshot = _seed_raw(raw_root, tid, date)

    changed_standings = _standings(
        tid,
        deck_b="deck-c",
    )

    client = StabilityClient(
        [_discovery_row(tid, date)],
        details={tid: _details(tid, date)},
        standings={tid: changed_standings},
        pairings={tid: _pairings(tid)},
    )

    result = _run_live(raw_root, client, "recent-standings")

    assert [call[0] for call in _payload_calls(client, tid)] == [
        "details",
        "standings",
        "pairings",
    ]
    assert _snapshot_id(result, tid) != old_snapshot.snapshot_id
    assert result.diagnostics["tournaments_recent_refetched"] == 1


def test_recent_details_same_pairings_changed_are_observed(tmp_path):
    tid = "recent-pairings"
    date = RUN_AT - timedelta(hours=1)
    raw_root = tmp_path / "raw"

    old_snapshot = _seed_raw(raw_root, tid, date)

    changed_pairings = _pairings(
        tid,
        winner=f"{tid}-p2",
    )

    client = StabilityClient(
        [_discovery_row(tid, date)],
        details={tid: _details(tid, date)},
        standings={tid: _standings(tid)},
        pairings={tid: changed_pairings},
    )

    result = _run_live(raw_root, client, "recent-pairings")

    assert [call[0] for call in _payload_calls(client, tid)] == [
        "details",
        "standings",
        "pairings",
    ]
    assert _snapshot_id(result, tid) != old_snapshot.snapshot_id
    assert result.diagnostics["tournaments_recent_refetched"] == 1


def test_offline_replay_remains_zero_network_and_hash_identical(tmp_path):
    tid = "offline-source"
    date = RUN_AT - timedelta(hours=1)
    raw_root = tmp_path / "raw"

    maps = _fetch_maps(tid, date)

    live_client = StabilityClient(
        [_discovery_row(tid, date)],
        **maps,
    )

    live = _run_live(
        raw_root,
        live_client,
        "offline-source-live",
    )

    exploding = ExplodingClient()

    replay = run_limitless_api_acquisition(
        game="POCKET",
        format="STANDARD",
        set_code="B4",
        acquisition_started_at=RUN_AT,
        execution_mode="offline",
        raw_store_root=raw_root,
        release_catalog=CATALOG,
        client=exploding,
        replay_run_id="offline-source-live",
        run_id="offline-replay",
        software_git_revision="rf1-corr-test",
        now_fn=lambda: NOW + timedelta(hours=1),
    )

    assert exploding.calls == 0
    assert replay.diagnostics["network_calls"] == 0
    assert (
        replay.diagnostics["normalized_hashes"]
        == live.diagnostics["normalized_hashes"]
    )
    assert (
        replay.diagnostics["contract_hashes"]
        == live.diagnostics["contract_hashes"]
    )



def test_invalid_discovery_date_recovers_from_valid_fresh_details(
    tmp_path,
):
    tid = "recoverable-invalid-discovery-date"
    detail_date = RUN_AT - timedelta(hours=2)
    raw_root = tmp_path / "raw"

    discovery_row = _discovery_row(tid, detail_date)
    discovery_row["date"] = "not-a-date"

    client = StabilityClient(
        [discovery_row],
        details={tid: _details(tid, detail_date)},
        standings={tid: _standings(tid)},
        pairings={tid: _pairings(tid)},
    )

    result = _run_live(
        raw_root,
        client,
        "recoverable-invalid-discovery-date",
    )

    # The invalid discovery date must not prematurely drop the row.
    assert result.manifest.selection.tournament_ids == (tid,)

    # Because discovery age was unknowable, this is conservative,
    # not stable/new-by-age.
    assert result.diagnostics[
        "discovery_conservative_candidates"
    ] == 1
    assert result.diagnostics[
        "tournaments_conservative_refetched"
    ] == 1

    # Fresh details recover a valid in-window date; after final
    # selection the remaining tournament payloads are acquired.
    assert [
        call[0]
        for call in _payload_calls(client, tid)
    ] == [
        "details",
        "standings",
        "pairings",
    ]

    assert result.diagnostics[
        "selection_exclusion_counts"
    ]["invalid_record"] == 0


def test_invalid_discovery_date_with_invalid_details_is_excluded(
    tmp_path,
):
    bad_tid = "unrecoverable-invalid-date"
    good_tid = "valid-peer"
    good_date = RUN_AT - timedelta(hours=1)
    raw_root = tmp_path / "raw"

    bad_discovery = _discovery_row(bad_tid, good_date)
    bad_discovery["date"] = None

    good_discovery = _discovery_row(good_tid, good_date)

    bad_details = _details(bad_tid, good_date)
    bad_details["date"] = "still-not-a-date"

    client = StabilityClient(
        [
            bad_discovery,
            good_discovery,
        ],
        details={
            bad_tid: bad_details,
            good_tid: _details(good_tid, good_date),
        },
        standings={
            # Deliberately no standings for bad_tid: any such call
            # would fail the fixture.
            good_tid: _standings(good_tid),
        },
        pairings={
            # Deliberately no pairings for bad_tid.
            good_tid: _pairings(good_tid),
        },
    )

    result = _run_live(
        raw_root,
        client,
        "unrecoverable-invalid-discovery-date",
    )

    # Conservative discovery candidate did receive fresh details.
    bad_calls = _payload_calls(client, bad_tid)
    assert [call[0] for call in bad_calls] == ["details"]

    # Fresh details still cannot prove a valid tournament date.
    # Final selector excludes it explicitly as invalid_record.
    assert bad_tid not in result.manifest.selection.tournament_ids
    assert result.manifest.selection.tournament_ids == (good_tid,)
    assert result.diagnostics[
        "selection_exclusion_counts"
    ]["invalid_record"] == 1

    # Discovery itself records why the row required conservative fetch.
    assert result.diagnostics[
        "discovery_conservative_candidates"
    ] == 1

    # No standings/pairings are acquired for the excluded tournament.
    assert not [
        call
        for call in bad_calls
        if call[0] in {"standings", "pairings"}
    ]
