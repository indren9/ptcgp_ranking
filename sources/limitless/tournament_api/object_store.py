from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Mapping, Protocol


SCHEMA_VERSION = "1"
DEFAULT_KEY_PREFIX = "limitless-api/v1"


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_run_id(run_id: str) -> str:
    value = str(run_id).strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid run_id")
    return value


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(str(value).strip())
    if not str(path) or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"invalid relative path: {value!r}")
    return path


def _safe_key(value: str) -> str:
    path = _safe_relative_path(value)
    return path.as_posix()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    metadata: Mapping[str, str]


class ObjectStoreBackend(Protocol):
    """Minimal private object-store contract used by raw persistence."""

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def head(self, key: str) -> ObjectInfo | None: ...


class LocalObjectStoreBackend:
    """Filesystem-backed fake object store for deterministic tests/shadow runs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _object_path(self, key: str) -> Path:
        return self.root / "objects" / Path(_safe_key(key))

    def _metadata_path(self, key: str) -> Path:
        return self.root / "metadata" / Path(f"{_safe_key(key)}.json")

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        object_path = self._object_path(key)
        metadata_path = self._metadata_path(key)
        _atomic_write_bytes(object_path, bytes(data))
        meta = {str(k).lower(): str(v) for k, v in dict(metadata or {}).items()}
        _atomic_write_bytes(metadata_path, _canonical_json_bytes(meta) + b"\n")

    def get_bytes(self, key: str) -> bytes:
        path = self._object_path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def head(self, key: str) -> ObjectInfo | None:
        path = self._object_path(key)
        if not path.exists():
            return None
        metadata_path = self._metadata_path(key)
        metadata: dict[str, str] = {}
        if metadata_path.exists():
            metadata = {
                str(k).lower(): str(v)
                for k, v in json.loads(metadata_path.read_text(encoding="utf-8")).items()
            }
        return ObjectInfo(size=path.stat().st_size, metadata=metadata)


@dataclass(frozen=True)
class S3ObjectStoreConfig:
    bucket: str
    endpoint: str | None
    region: str | None
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)
    addressing_style: str = "auto"

    @classmethod
    def from_env(cls) -> "S3ObjectStoreConfig":
        bucket = os.environ.get("MARS_RAW_S3_BUCKET", "").strip()
        access_key = os.environ.get("MARS_RAW_S3_ACCESS_KEY_ID", "").strip()
        secret_key = os.environ.get("MARS_RAW_S3_SECRET_ACCESS_KEY", "").strip()
        missing = [
            name
            for name, value in (
                ("MARS_RAW_S3_BUCKET", bucket),
                ("MARS_RAW_S3_ACCESS_KEY_ID", access_key),
                ("MARS_RAW_S3_SECRET_ACCESS_KEY", secret_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "missing required private object-store environment variables: "
                + ", ".join(missing)
            )
        addressing_style = os.environ.get(
            "MARS_RAW_S3_ADDRESSING_STYLE", "auto"
        ).strip().lower()
        if addressing_style not in {"auto", "path", "virtual"}:
            raise ValueError("MARS_RAW_S3_ADDRESSING_STYLE must be auto, path, or virtual")
        return cls(
            bucket=bucket,
            endpoint=os.environ.get("MARS_RAW_S3_ENDPOINT") or None,
            region=os.environ.get("MARS_RAW_S3_REGION") or None,
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=os.environ.get("MARS_RAW_S3_SESSION_TOKEN") or None,
            addressing_style=addressing_style,
        )


class S3ObjectStoreBackend:
    """Vendor-neutral S3-compatible backend; credentials are environment-only."""

    def __init__(self, config: S3ObjectStoreConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client or self._build_client(config)

    @staticmethod
    def _build_client(config: S3ObjectStoreConfig) -> Any:
        try:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only in real S3 mode
            raise RuntimeError(
                "boto3 is required for the S3-compatible raw persistence backend"
            ) from exc
        return boto3.client(
            "s3",
            endpoint_url=config.endpoint,
            region_name=config.region,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            aws_session_token=config.session_token,
            config=Config(s3={"addressing_style": config.addressing_style}),
        )

    @classmethod
    def from_env(cls) -> "S3ObjectStoreBackend":
        return cls(S3ObjectStoreConfig.from_env())

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        self._client.put_object(
            Bucket=self.config.bucket,
            Key=_safe_key(key),
            Body=bytes(data),
            Metadata={str(k).lower(): str(v) for k, v in dict(metadata or {}).items()},
        )

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(
            Bucket=self.config.bucket,
            Key=_safe_key(key),
        )
        return response["Body"].read()

    def head(self, key: str) -> ObjectInfo | None:
        try:
            response = self._client.head_object(
                Bucket=self.config.bucket,
                Key=_safe_key(key),
            )
        except Exception as exc:  # boto-style clients expose response metadata on errors
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return ObjectInfo(
            size=int(response["ContentLength"]),
            metadata={
                str(k).lower(): str(v)
                for k, v in dict(response.get("Metadata") or {}).items()
            },
        )


@dataclass(frozen=True)
class PersistedRawRun:
    run_id: str
    manifest_key: str
    source_manifest_sha256: str
    file_count: int
    total_bytes: int


def _load_and_validate_source_manifest(raw_root: Path, run_id: str) -> dict[str, Any]:
    manifest_path = raw_root / "runs" / run_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"acquisition manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    from acquisition.manifest import validate_manifest_dict

    validate_manifest_dict(payload)
    if str(payload.get("run_id") or "").strip() != run_id:
        raise ValueError("acquisition manifest run_id mismatch")
    if str(payload.get("source") or "").strip() != "Limitless Tournament API":
        raise ValueError("canonical raw persistence requires Limitless Tournament API source")
    return payload


def _validate_manifest_refs(raw_root: Path, manifest: Mapping[str, Any]) -> set[PurePosixPath]:
    refs = list((manifest.get("raw") or {}).get("snapshot_refs") or [])
    if not refs:
        raise ValueError("acquisition manifest contains no raw snapshot refs")

    required_files: set[PurePosixPath] = set()
    payload_types_by_tid: dict[str, set[str]] = {}
    snapshot_ids_by_tid: dict[str, set[str]] = {}

    for row in refs:
        relative = _safe_relative_path(str(row.get("relative_path") or ""))
        path = raw_root / Path(relative.as_posix())
        if not path.exists():
            raise FileNotFoundError(f"raw evidence missing: {relative.as_posix()}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected_sha = str(row.get("sha256") or "").strip().lower()
        if _json_sha256(payload) != expected_sha:
            raise ValueError(f"raw evidence hash mismatch: {relative.as_posix()}")
        required_files.add(relative)

        if "snapshots" in relative.parts:
            metadata = relative.parent / "metadata.json"
            metadata_path = raw_root / Path(metadata.as_posix())
            if not metadata_path.exists():
                raise FileNotFoundError(
                    f"raw snapshot metadata missing: {metadata.as_posix()}"
                )
            required_files.add(metadata)

        tournament_id = row.get("tournament_id")
        payload_type = str(row.get("payload_type") or "")
        snapshot_id = str(row.get("snapshot_id") or "")
        if tournament_id is not None and payload_type in {"details", "standings", "pairings"}:
            tid = str(tournament_id)
            payload_types_by_tid.setdefault(tid, set()).add(payload_type)
            snapshot_ids_by_tid.setdefault(tid, set()).add(snapshot_id)

    selected_ids = [
        str(value)
        for value in ((manifest.get("selection") or {}).get("tournament_ids") or [])
    ]
    for tid in selected_ids:
        if payload_types_by_tid.get(tid) != {"details", "standings", "pairings"}:
            raise ValueError(f"selected tournament raw evidence incomplete: {tid}")
        if len(snapshot_ids_by_tid.get(tid, set())) != 1:
            raise ValueError(f"selected tournament has inconsistent snapshot ids: {tid}")

    run_id = str(manifest["run_id"])
    run_dir = PurePosixPath("runs") / run_id
    for name in ("manifest.json", "raw_refs.json", "diagnostics.json"):
        path = raw_root / Path((run_dir / name).as_posix())
        if path.exists():
            required_files.add(run_dir / name)
    if run_dir / "manifest.json" not in required_files:
        required_files.add(run_dir / "manifest.json")
    return required_files


def validate_local_run_evidence(raw_root: str | Path, run_id: str) -> dict[str, Any]:
    root = Path(raw_root)
    rid = _safe_run_id(run_id)
    manifest = _load_and_validate_source_manifest(root, rid)
    _validate_manifest_refs(root, manifest)
    return manifest


def _blob_key(prefix: str, digest: str) -> str:
    base = _safe_key(prefix)
    return f"{base}/objects/sha256/{digest[:2]}/{digest}"


def _run_manifest_key(prefix: str, run_id: str) -> str:
    return f"{_safe_key(prefix)}/runs/{_safe_run_id(run_id)}/manifest.json"


def _put_verified_blob(backend: ObjectStoreBackend, key: str, data: bytes) -> None:
    digest = _sha256_bytes(data)
    metadata = {"sha256": digest, "size": str(len(data))}
    existing = backend.head(key)
    if existing is None:
        backend.put_bytes(key, data, metadata=metadata)
        existing = backend.head(key)
    if existing is None:
        raise IOError(f"object-store verification failed: {key}")
    if existing.size != len(data) or existing.metadata.get("sha256") != digest:
        raise IOError(f"object-store object mismatch: {key}")


def persist_canonical_raw_run(
    raw_root: str | Path,
    run_id: str,
    backend: ObjectStoreBackend,
    *,
    key_prefix: str = DEFAULT_KEY_PREFIX,
) -> PersistedRawRun:
    root = Path(raw_root)
    rid = _safe_run_id(run_id)
    source_manifest = _load_and_validate_source_manifest(root, rid)
    required_files = _validate_manifest_refs(root, source_manifest)

    file_rows: list[dict[str, Any]] = []
    total_bytes = 0
    for relative in sorted(required_files, key=lambda item: item.as_posix()):
        data = (root / Path(relative.as_posix())).read_bytes()
        digest = _sha256_bytes(data)
        key = _blob_key(key_prefix, digest)
        _put_verified_blob(backend, key, data)
        file_rows.append(
            {
                "relative_path": relative.as_posix(),
                "object_key": key,
                "sha256": digest,
                "size": len(data),
            }
        )
        total_bytes += len(data)

    source_manifest_path = root / "runs" / rid / "manifest.json"
    index = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "source": "Limitless Tournament API",
        "source_manifest_sha256": _sha256_bytes(source_manifest_path.read_bytes()),
        "files": file_rows,
    }
    index_bytes = _canonical_json_bytes(index) + b"\n"
    manifest_key = _run_manifest_key(key_prefix, rid)

    existing = backend.head(manifest_key)
    if existing is not None:
        if backend.get_bytes(manifest_key) != index_bytes:
            raise ValueError(f"canonical raw run already exists with different content: {rid}")
    else:
        # Atomic-like promotion: every referenced blob is uploaded + verified first;
        # the canonical run manifest is written last.
        _put_verified_blob(backend, manifest_key, index_bytes)

    return PersistedRawRun(
        run_id=rid,
        manifest_key=manifest_key,
        source_manifest_sha256=index["source_manifest_sha256"],
        file_count=len(file_rows),
        total_bytes=total_bytes,
    )


def _restore_latest_pointers(raw_root: Path, manifest: Mapping[str, Any]) -> None:
    refs = list((manifest.get("raw") or {}).get("snapshot_refs") or [])
    tournament_snapshots: dict[str, str] = {}
    catalog_snapshots: dict[str, str] = {}
    for row in refs:
        tournament_id = row.get("tournament_id")
        payload_type = str(row.get("payload_type") or "")
        snapshot_id = str(row.get("snapshot_id") or "").strip()
        if tournament_id is not None and payload_type in {"details", "standings", "pairings"}:
            tournament_snapshots[str(tournament_id)] = snapshot_id
        elif payload_type.startswith("catalog:"):
            catalog_snapshots[payload_type.split(":", 1)[1]] = snapshot_id

    for tid, snapshot_id in tournament_snapshots.items():
        pointer = _canonical_json_bytes({"snapshot_id": snapshot_id}) + b"\n"
        _atomic_write_bytes(raw_root / "tournaments" / tid / "latest.json", pointer)
    for name, snapshot_id in catalog_snapshots.items():
        pointer = _canonical_json_bytes({"snapshot_id": snapshot_id}) + b"\n"
        _atomic_write_bytes(raw_root / "catalog" / name / "latest.json", pointer)


def restore_canonical_raw_run(
    raw_root: str | Path,
    run_id: str,
    backend: ObjectStoreBackend,
    *,
    key_prefix: str = DEFAULT_KEY_PREFIX,
    restore_latest_pointers: bool = True,
) -> PersistedRawRun:
    root = Path(raw_root)
    rid = _safe_run_id(run_id)
    manifest_key = _run_manifest_key(key_prefix, rid)
    index_bytes = backend.get_bytes(manifest_key)
    index = json.loads(index_bytes.decode("utf-8"))
    if str(index.get("schema_version")) != SCHEMA_VERSION:
        raise ValueError("unsupported canonical raw run manifest schema")
    if str(index.get("run_id")) != rid:
        raise ValueError("canonical raw run manifest run_id mismatch")
    if str(index.get("source")) != "Limitless Tournament API":
        raise ValueError("canonical raw run source mismatch")

    root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".raw-restore-{rid}.", dir=root.parent))
    total_bytes = 0
    try:
        for row in index.get("files") or []:
            relative = _safe_relative_path(str(row.get("relative_path") or ""))
            key = _safe_key(str(row.get("object_key") or ""))
            expected_sha = str(row.get("sha256") or "").lower()
            expected_size = int(row.get("size", -1))
            data = backend.get_bytes(key)
            if len(data) != expected_size or _sha256_bytes(data) != expected_sha:
                raise IOError(f"canonical raw object verification failed: {relative.as_posix()}")
            _atomic_write_bytes(stage / Path(relative.as_posix()), data)
            total_bytes += len(data)

        staged_manifest = stage / "runs" / rid / "manifest.json"
        if not staged_manifest.exists():
            raise FileNotFoundError("restored canonical run has no acquisition manifest")
        if _sha256_bytes(staged_manifest.read_bytes()) != str(index.get("source_manifest_sha256") or ""):
            raise ValueError("restored acquisition manifest hash mismatch")

        staged_source = _load_and_validate_source_manifest(stage, rid)
        _validate_manifest_refs(stage, staged_source)

        for row in index.get("files") or []:
            relative = _safe_relative_path(str(row["relative_path"]))
            staged_path = stage / Path(relative.as_posix())
            destination = root / Path(relative.as_posix())
            if destination.exists() and destination.read_bytes() != staged_path.read_bytes():
                raise ValueError(
                    f"local immutable raw evidence conflicts with canonical store: {relative.as_posix()}"
                )
            _atomic_write_bytes(destination, staged_path.read_bytes())

        if restore_latest_pointers:
            _restore_latest_pointers(root, staged_source)
        validate_local_run_evidence(root, rid)
    finally:
        import shutil

        shutil.rmtree(stage, ignore_errors=True)

    return PersistedRawRun(
        run_id=rid,
        manifest_key=manifest_key,
        source_manifest_sha256=str(index["source_manifest_sha256"]),
        file_count=len(index.get("files") or []),
        total_bytes=total_bytes,
    )


__all__ = [
    "DEFAULT_KEY_PREFIX",
    "LocalObjectStoreBackend",
    "ObjectInfo",
    "ObjectStoreBackend",
    "PersistedRawRun",
    "S3ObjectStoreBackend",
    "S3ObjectStoreConfig",
    "persist_canonical_raw_run",
    "restore_canonical_raw_run",
    "validate_local_run_evidence",
]
