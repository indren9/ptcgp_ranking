from pathlib import Path
import json
import pytest
from test_object_store import _make_run
from sources.limitless.tournament_api.object_store import (
    persist_canonical_raw_run, restore_canonical_raw_run,
)
from storage.tcg_private_raw import PersistentLocalRawStore, backup_run, verify_archive


def test_persistent_backup_exact_restore(tmp_path):
    source = tmp_path / "source"
    _make_run(source)
    store = PersistentLocalRawStore(tmp_path / "primary")
    first = persist_canonical_raw_run(source, "live-test", store)
    archive = backup_run(store, tmp_path / "backup", "live-test")
    before = archive.path.read_bytes()
    assert persist_canonical_raw_run(source, "live-test", store) == first
    assert backup_run(store, tmp_path / "backup", "live-test").path.read_bytes() == before
    result = restore_canonical_raw_run(tmp_path / "restored", "live-test", archive)
    assert result == first
    index = json.loads(archive.get_bytes(first.manifest_key))
    for row in index["files"]:
        rel = row["relative_path"]
        assert (tmp_path / "restored" / rel).read_bytes() == (source / rel).read_bytes()


def test_primary_tampering_blocks_reuse(tmp_path):
    source = tmp_path / "source"
    _make_run(source)
    store = PersistentLocalRawStore(tmp_path / "primary")
    result = persist_canonical_raw_run(source, "live-test", store)
    index = json.loads(store.get_bytes(result.manifest_key))
    store._object_path(index["files"][0]["object_key"]).write_bytes(b"corrupt")
    with pytest.raises(IOError):
        persist_canonical_raw_run(source, "live-test", store)


def test_incomplete_backup_fails_closed_no_overwrite(tmp_path):
    source = tmp_path / "source"
    _make_run(source)
    store = PersistentLocalRawStore(tmp_path / "primary")
    persist_canonical_raw_run(source, "live-test", store)
    target = tmp_path / "backup" / "live-test.zip"
    target.parent.mkdir()
    target.write_bytes(b"partial")
    with pytest.raises(Exception):
        backup_run(store, target.parent, "live-test")
    assert target.read_bytes() == b"partial"


def test_missing_metadata_fails_closed(tmp_path):
    store = PersistentLocalRawStore(tmp_path / "primary")
    path = store._object_path("test")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"incomplete")
    with pytest.raises(IOError):
        store.head("test")
    assert path.read_bytes() == b"incomplete"


def test_private_default_selects_local_before_acquisition(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from scripts import tcg_live_latest_completed_job as job
    root = Path(__file__).resolve().parents[1]
    started = datetime(2026, 11, 27, tzinfo=UTC)
    monkeypatch.setattr(job, "plan_set_publication", lambda **kwargs: {
        **job.build_plan(catalog_path=root / "data/reference/ptcg_live_windows.json",
                         state=None, acquisition_started_at=started),
        "action": "publish_required",
    })
    primary, backup = tmp_path / "primary", tmp_path / "backup"
    monkeypatch.setattr(job, "private_raw_roots", lambda repo: (primary, backup))
    monkeypatch.setattr(job.S3ObjectStoreBackend, "from_env",
                        lambda: pytest.fail("private route must not initialize S3"))
    monkeypatch.setattr(job, "_git_revision", lambda repo: "fixture")
    def stop_before_network(**kwargs):
        assert primary.is_dir()
        raise RuntimeError("acquisition stub")
    monkeypatch.setattr(job, "run_limitless_api_acquisition", stop_before_network)
    with pytest.raises(RuntimeError, match="acquisition stub"):
        job.run_job(repo_root=root, work_root=tmp_path / "work",
                    acquisition_started_at=started, publish_private=True)
