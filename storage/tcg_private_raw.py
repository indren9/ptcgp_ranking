"""WP4: private TCG persistent RAW and independently restorable OneDrive archives."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from sources.limitless.tournament_api.object_store import (
    LocalObjectStoreBackend, ObjectInfo, _safe_key, _run_manifest_key,
    _sha256_bytes, _canonical_json_bytes,
)


def _safe_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("RAW persistence path is redirected")


def _exclusive_write(path: Path, data: bytes) -> None:
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


class PersistentLocalRawStore(LocalObjectStoreBackend):
    """Exclusive creation, byte-verified reads; partial writes require review."""

    def __init__(self, root: Path):
        _safe_path(root)
        super().__init__(root)

    def head(self, key: str) -> ObjectInfo | None:
        path, meta = self._object_path(key), self._metadata_path(key)
        _safe_path(path)
        _safe_path(meta)
        if not path.exists() and not meta.exists():
            return None
        if not path.is_file() or not meta.is_file():
            raise IOError("incomplete RAW object or metadata; operator review")
        data = path.read_bytes()
        recorded = json.loads(meta.read_text(encoding="utf-8"))
        if recorded.get("sha256") != _sha256_bytes(data) or recorded.get("size") != str(len(data)):
            raise IOError("RAW object bytes disagree with metadata")
        return ObjectInfo(len(data), recorded)

    def get_bytes(self, key: str) -> bytes:
        info = self.head(key)
        if info is None:
            raise FileNotFoundError(key)
        data = self._object_path(key).read_bytes()
        if _sha256_bytes(data) != info.metadata["sha256"] or len(data) != info.size:
            raise IOError("RAW object changed during read")
        return data

    def put_bytes(self, key: str, data: bytes, *, metadata=None) -> None:
        expected = {"sha256": _sha256_bytes(data), "size": str(len(data))}
        if dict(metadata or {}) != expected:
            raise ValueError("invalid RAW object metadata")
        current = self.head(key)
        if current is not None:
            if self.get_bytes(key) != data:
                raise ValueError("immutable RAW object conflict")
            return
        _exclusive_write(self._object_path(key), data)
        _exclusive_write(self._metadata_path(key), _canonical_json_bytes(expected) + b"\n")
        if self.get_bytes(key) != data:
            raise IOError("RAW object readback failed")


class RawArchive:
    """Read-only object-store adapter; restore uses the existing canonical validator."""

    def __init__(self, path: Path):
        self.path = path

    def get_bytes(self, key: str) -> bytes:
        _safe_path(self.path)
        with ZipFile(self.path) as archive:
            return archive.read(_safe_key(key))

    def head(self, key: str) -> ObjectInfo | None:
        try:
            data = self.get_bytes(key)
        except KeyError:
            return None
        return ObjectInfo(len(data), {"sha256": _sha256_bytes(data), "size": str(len(data))})


def verify_archive(path: Path, store: PersistentLocalRawStore, run_id: str) -> RawArchive:
    key = _run_manifest_key("limitless-api/v1", run_id)
    index_bytes = store.get_bytes(key)
    index = json.loads(index_bytes)
    expected = {key: (_sha256_bytes(index_bytes), len(index_bytes))}
    for row in index["files"]:
        k = _safe_key(row["object_key"])
        value = (row["sha256"], row["size"])
        if k in expected and expected[k] != value:
            raise ValueError("conflicting RAW index")
        expected[k] = value
    _safe_path(path)
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(expected) or set(names) != set(expected):
            raise ValueError("RAW backup inventory mismatch")
        for k, (digest, size) in expected.items():
            data = archive.read(k)
            if len(data) != size or _sha256_bytes(data) != digest or data != store.get_bytes(k):
                raise IOError("RAW backup content mismatch")
    return RawArchive(path)


def backup_run(store: PersistentLocalRawStore, backup_root: Path, run_id: str) -> RawArchive:
    key = _run_manifest_key("limitless-api/v1", run_id)
    index_bytes = store.get_bytes(key)
    index = json.loads(index_bytes)
    if index.get("run_id") != run_id:
        raise ValueError("RAW index identity mismatch")
    destination = backup_root / (run_id + ".zip")
    _safe_path(destination)
    if destination.exists():
        return verify_archive(destination, store, run_id)
    backup_root.mkdir(parents=True, exist_ok=True)
    # Build outside OneDrive; exclusive destination creation never replaces a backup.
    with tempfile.TemporaryDirectory(prefix="raw-backup-", dir=store.root) as temporary:
        stage = Path(temporary) / "run.zip"
        with ZipFile(stage, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
            for k in sorted({row["object_key"] for row in index["files"]}):
                archive.writestr(_safe_key(k), store.get_bytes(k))
            archive.writestr(key, index_bytes)
        verify_archive(stage, store, run_id)
        with stage.open("rb") as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
    return verify_archive(destination, store, run_id)


def private_raw_roots(repo_root: Path) -> tuple[Path, Path]:
    local = repo_root.resolve().parent / "mars-raw-tcg"
    onedrive = Path.home() / "OneDrive"
    configured = os.environ.get("OneDriveConsumer")
    if not configured or Path(configured).resolve() != onedrive.resolve() or not onedrive.is_dir():
        raise ValueError("personal OneDrive root not verified")
    backup = onedrive / "Giochi" / "Pokemon" / "TCG" / "RAW_Backup"
    _safe_path(local)
    _safe_path(backup)
    if local.is_relative_to(onedrive.resolve()) or backup.is_relative_to(local):
        raise ValueError("RAW primary and backup must be separate")
    if not backup.parent.is_dir():
        raise ValueError("TCG private backup parent absent")
    return local, backup
