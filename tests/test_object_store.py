from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sources.limitless.tournament_api.object_store import (
    LocalObjectStoreBackend,
    S3ObjectStoreConfig,
    persist_canonical_raw_run,
    restore_canonical_raw_run,
)


def _canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_sha(value):
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(payload) + b"\n")


def _make_run(root: Path, run_id: str = "live-test") -> None:
    tid = "t1"
    sid = "snapshot-abc"
    details = {"id": tid, "game": "POCKET", "format": "STANDARD"}
    standings = [
        {"player": "private-player-1", "deck": {"id": "deck-a", "name": "Deck A"}},
        {"player": "private-player-2", "deck": {"id": "deck-b", "name": "Deck B"}},
    ]
    pairings = [{"player1": "private-player-1", "player2": "private-player-2", "winner": "private-player-1"}]
    catalog = [{"id": tid, "date": "2026-08-01T00:00:00Z"}]

    snapshot_root = root / "tournaments" / tid / "snapshots" / sid
    _write_json(snapshot_root / "details.json", details)
    _write_json(snapshot_root / "standings.json", standings)
    _write_json(snapshot_root / "pairings.json", pairings)
    _write_json(
        snapshot_root / "metadata.json",
        {
            "schema_version": "1",
            "tournament_id": tid,
            "snapshot_id": sid,
            "payload_hashes": {
                "details": _json_sha(details),
                "standings": _json_sha(standings),
                "pairings": _json_sha(pairings),
            },
        },
    )

    catalog_sid = _json_sha(catalog)
    catalog_root = root / "catalog" / "tournaments" / "snapshots" / catalog_sid
    _write_json(catalog_root / "catalog.json", catalog)
    _write_json(
        catalog_root / "metadata.json",
        {"schema_version": "1", "snapshot_id": catalog_sid, "sha256": catalog_sid},
    )

    refs = [
        {
            "payload_type": "catalog:tournaments",
            "tournament_id": None,
            "snapshot_id": catalog_sid,
            "sha256": _json_sha(catalog),
            "fetched_at": "2026-09-03T10:00:00Z",
            "relative_path": f"catalog/tournaments/snapshots/{catalog_sid}/catalog.json",
        },
        {
            "payload_type": "details",
            "tournament_id": tid,
            "snapshot_id": sid,
            "sha256": _json_sha(details),
            "fetched_at": "2026-09-03T10:00:00Z",
            "relative_path": f"tournaments/{tid}/snapshots/{sid}/details.json",
        },
        {
            "payload_type": "standings",
            "tournament_id": tid,
            "snapshot_id": sid,
            "sha256": _json_sha(standings),
            "fetched_at": "2026-09-03T10:00:00Z",
            "relative_path": f"tournaments/{tid}/snapshots/{sid}/standings.json",
        },
        {
            "payload_type": "pairings",
            "tournament_id": tid,
            "snapshot_id": sid,
            "sha256": _json_sha(pairings),
            "fetched_at": "2026-09-03T10:00:00Z",
            "relative_path": f"tournaments/{tid}/snapshots/{sid}/pairings.json",
        },
    ]
    manifest = {
        "schema_version": "1",
        "run_id": run_id,
        "created_at": "2026-09-03T10:01:00Z",
        "acquisition_started_at": "2026-09-03T10:00:00Z",
        "source": "Limitless Tournament API",
        "software": {"git_revision": "deadbeef"},
        "scope": {
            "policy_id": "fixture",
            "game": "POCKET",
            "format": "STANDARD",
            "set_code": "B4",
            "set_name": "Ruler of the Skies",
            "start": "2026-07-30T01:00:00Z",
            "end": "2026-08-27T01:00:00Z",
            "catalog_version": "fixture-v1",
        },
        "selection": {
            "tournament_ids": [tid],
            "included_count": 1,
            "exclusion_counts": {},
            "failures": [],
        },
        "raw": {"snapshot_refs": refs},
        "normalized": {"row_counts": {}, "hashes": {}, "diagnostics": {}},
        "aggregation": {
            "total_participants": 2,
            "classified_participants": 2,
            "unclassified_participants": 0,
            "comparable_matches": 1,
            "pairing_exclusion_counts": {},
            "deck_identity_diagnostics": {},
        },
        "rate_limit_observations": [],
        "contracts": {},
    }
    run_root = root / "runs" / run_id
    _write_json(run_root / "manifest.json", manifest)
    _write_json(run_root / "raw_refs.json", {"run_id": run_id, "raw_refs": refs})
    _write_json(run_root / "diagnostics.json", {"execution_mode": "live"})


def test_local_backend_persist_restore_round_trip(tmp_path: Path):
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    object_root = tmp_path / "object-store"
    _make_run(source)

    backend = LocalObjectStoreBackend(object_root)
    persisted = persist_canonical_raw_run(source, "live-test", backend)

    assert persisted.file_count == 9
    assert persisted.total_bytes > 0
    assert "private-player" not in persisted.manifest_key

    restored_info = restore_canonical_raw_run(restored, "live-test", backend)
    assert restored_info.source_manifest_sha256 == persisted.source_manifest_sha256
    assert (restored / "runs/live-test/manifest.json").read_bytes() == (
        source / "runs/live-test/manifest.json"
    ).read_bytes()
    assert json.loads((restored / "tournaments/t1/latest.json").read_text())["snapshot_id"] == "snapshot-abc"


def test_manifest_is_published_only_after_all_objects_verify(tmp_path: Path):
    source = tmp_path / "source"
    _make_run(source)

    class FailingBackend(LocalObjectStoreBackend):
        def __init__(self, root):
            super().__init__(root)
            self.count = 0

        def put_bytes(self, key, data, *, metadata=None):
            self.count += 1
            if self.count == 3:
                raise IOError("simulated upload failure")
            return super().put_bytes(key, data, metadata=metadata)

    backend = FailingBackend(tmp_path / "object-store")
    with pytest.raises(IOError, match="simulated upload failure"):
        persist_canonical_raw_run(source, "live-test", backend)
    assert backend.head("limitless-api/v1/runs/live-test/manifest.json") is None


def test_tampered_raw_ref_blocks_promotion(tmp_path: Path):
    source = tmp_path / "source"
    _make_run(source)
    pairings = source / "tournaments/t1/snapshots/snapshot-abc/pairings.json"
    pairings.write_text('{"tampered":true}\n', encoding="utf-8")

    backend = LocalObjectStoreBackend(tmp_path / "object-store")
    with pytest.raises(ValueError, match="raw evidence hash mismatch"):
        persist_canonical_raw_run(source, "live-test", backend)
    assert backend.head("limitless-api/v1/runs/live-test/manifest.json") is None


def test_restore_rejects_tampered_object(tmp_path: Path):
    source = tmp_path / "source"
    _make_run(source)
    backend = LocalObjectStoreBackend(tmp_path / "object-store")
    persist_canonical_raw_run(source, "live-test", backend)

    index = json.loads(backend.get_bytes("limitless-api/v1/runs/live-test/manifest.json"))
    target = index["files"][0]
    backend.put_bytes(target["object_key"], b"tampered", metadata={"sha256": "bad", "size": "8"})

    with pytest.raises(IOError, match="canonical raw object verification failed"):
        restore_canonical_raw_run(tmp_path / "restored", "live-test", backend)


def test_s3_config_is_environment_only_and_repr_hides_credentials(monkeypatch):
    monkeypatch.setenv("MARS_RAW_S3_BUCKET", "private-bucket")
    monkeypatch.setenv("MARS_RAW_S3_ENDPOINT", "https://s3.example.invalid")
    monkeypatch.setenv("MARS_RAW_S3_REGION", "eu-test-1")
    monkeypatch.setenv("MARS_RAW_S3_ACCESS_KEY_ID", "access-secretish")
    monkeypatch.setenv("MARS_RAW_S3_SECRET_ACCESS_KEY", "super-secret")

    config = S3ObjectStoreConfig.from_env()
    text = repr(config)
    assert config.bucket == "private-bucket"
    assert config.endpoint == "https://s3.example.invalid"
    assert "access-secretish" not in text
    assert "super-secret" not in text


def test_s3_backend_uses_standard_object_contract_without_vendor_assumptions():
    from io import BytesIO
    from sources.limitless.tournament_api.object_store import S3ObjectStoreBackend

    class FakeS3Client:
        def __init__(self):
            self.objects = {}

        def put_object(self, **kwargs):
            self.objects[(kwargs["Bucket"], kwargs["Key"])] = (
                bytes(kwargs["Body"]),
                dict(kwargs.get("Metadata") or {}),
            )

        def head_object(self, **kwargs):
            data, metadata = self.objects[(kwargs["Bucket"], kwargs["Key"])]
            return {"ContentLength": len(data), "Metadata": metadata}

        def get_object(self, **kwargs):
            data, _ = self.objects[(kwargs["Bucket"], kwargs["Key"])]
            return {"Body": BytesIO(data)}

    config = S3ObjectStoreConfig(
        bucket="private-bucket",
        endpoint="https://object.example.invalid",
        region="eu-test-1",
        access_key_id="hidden-access",
        secret_access_key="hidden-secret",
    )
    client = FakeS3Client()
    backend = S3ObjectStoreBackend(config, client=client)
    backend.put_bytes("prefix/object", b"abc", metadata={"sha256": "x"})

    assert backend.get_bytes("prefix/object") == b"abc"
    info = backend.head("prefix/object")
    assert info is not None
    assert info.size == 3
    assert info.metadata["sha256"] == "x"
