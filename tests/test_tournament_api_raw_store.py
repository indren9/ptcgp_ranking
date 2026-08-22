from datetime import UTC, datetime
import json

import pytest

from sources.limitless.tournament_api.raw_store import ImmutableRawStore, sha256_json


def now():
    return datetime(2026, 8, 22, 12, tzinfo=UTC)


def payloads():
    return {
        "details": {"id": "t1", "game": "POCKET", "decklists": True},
        "standings": [{"player": "p1", "placing": 1}],
        "pairings": [{"phase": 1, "round": 1, "player1": "p1", "player2": None, "winner": "p1"}],
    }


def test_same_payload_reuses_content_addressed_snapshot(tmp_path):
    store = ImmutableRawStore(tmp_path / "raw")
    data = payloads()

    first = store.save_tournament_snapshot("t1", fetched_at=now(), **data)
    second = store.save_tournament_snapshot("t1", fetched_at=now(), **data)

    assert first.snapshot_id == second.snapshot_id
    snapshots = list((tmp_path / "raw" / "tournaments" / "t1" / "snapshots").iterdir())
    assert len(snapshots) == 1
    assert store.latest_snapshot_id("t1") == first.snapshot_id


def test_changed_payload_creates_new_snapshot_without_overwriting_old(tmp_path):
    store = ImmutableRawStore(tmp_path / "raw")
    data = payloads()
    first = store.save_tournament_snapshot("t1", fetched_at=now(), **data)

    changed = payloads()
    changed["standings"] = [{"player": "p1", "placing": 2}]
    second = store.save_tournament_snapshot("t1", fetched_at=now(), **changed)

    assert first.snapshot_id != second.snapshot_id
    assert len(list((tmp_path / "raw" / "tournaments" / "t1" / "snapshots").iterdir())) == 2
    assert store.load_tournament_snapshot("t1", first.snapshot_id)["standings"][0]["placing"] == 1
    assert store.load_tournament_snapshot("t1", second.snapshot_id)["standings"][0]["placing"] == 2


def test_offline_replay_validates_hashes(tmp_path):
    store = ImmutableRawStore(tmp_path / "raw")
    snap = store.save_tournament_snapshot("t1", fetched_at=now(), **payloads())

    replay = store.load_tournament_snapshot("t1", snap.snapshot_id, validate=True)
    assert replay["details"]["id"] == "t1"

    standings_path = tmp_path / "raw" / snap.refs[1].relative_path
    standings_path.write_text(json.dumps([{"player": "tampered"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.load_tournament_snapshot("t1", snap.snapshot_id, validate=True)


def test_run_raw_refs_are_immutable_and_sorted(tmp_path):
    store = ImmutableRawStore(tmp_path / "raw")
    snap = store.save_tournament_snapshot("t1", fetched_at=now(), **payloads())

    path = store.write_run_raw_refs("run-1", tournament_ids=("t1",), refs=snap.refs)
    again = store.write_run_raw_refs("run-1", tournament_ids=("t1",), refs=reversed(snap.refs))
    assert path == again
    loaded = store.load_run_raw_refs("run-1")
    assert loaded["tournament_ids"] == ["t1"]
    assert [row["payload_type"] for row in loaded["raw_refs"]] == ["details", "pairings", "standings"]

    other = store.save_tournament_snapshot("t2", fetched_at=now(), **{**payloads(), "details": {"id": "t2"}})
    with pytest.raises(ValueError, match="immutable"):
        store.write_run_raw_refs("run-1", tournament_ids=("t1", "t2"), refs=(*snap.refs, *other.refs))


def test_catalog_snapshot_is_content_addressed(tmp_path):
    store = ImmutableRawStore(tmp_path / "raw")
    payload = [{"id": "t1"}]
    first = store.save_catalog_snapshot("tournaments", payload, fetched_at=now())
    second = store.save_catalog_snapshot("tournaments", payload, fetched_at=now())
    assert first.snapshot_id == second.snapshot_id == sha256_json(payload)
