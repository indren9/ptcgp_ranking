from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from acquisition.contracts import RawPayloadRef
from domain.releases import require_utc
from storage.acquisition import AcquisitionPaths, init_acquisition_paths

PAYLOAD_TYPES = ("details", "standings", "pairings")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_bytes(path, canonical_json_bytes(value) + b"\n")


def _safe_component(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"invalid {field_name}")
    return text


@dataclass(frozen=True)
class TournamentRawSnapshot:
    tournament_id: str
    snapshot_id: str
    refs: tuple[RawPayloadRef, ...]


class ImmutableRawStore:
    """Content-addressed raw storage for exact offline replay of API payloads."""

    def __init__(self, root: str | Path = "data/raw/limitless_api") -> None:
        self.paths: AcquisitionPaths = init_acquisition_paths(root)

    def save_tournament_snapshot(
        self,
        tournament_id: str,
        *,
        details: Mapping[str, Any],
        standings: list[Mapping[str, Any]],
        pairings: list[Mapping[str, Any]],
        fetched_at: datetime,
    ) -> TournamentRawSnapshot:
        tid = _safe_component(tournament_id, field_name="tournament_id")
        fetched = require_utc(fetched_at, field_name="fetched_at")
        payloads: dict[str, Any] = {
            "details": dict(details),
            "standings": [dict(row) for row in standings],
            "pairings": [dict(row) for row in pairings],
        }
        hashes = {name: sha256_json(payload) for name, payload in payloads.items()}
        bundle_descriptor = {
            "schema_version": "1",
            "tournament_id": tid,
            "payload_hashes": hashes,
        }
        snapshot_id = sha256_json(bundle_descriptor)
        snapshot_root = self.paths.tournaments / tid / "snapshots" / snapshot_id

        metadata = {
            "schema_version": "1",
            "tournament_id": tid,
            "snapshot_id": snapshot_id,
            "bundle_sha256": sha256_json(bundle_descriptor),
            "payload_hashes": hashes,
            "fetched_at": fetched.isoformat().replace("+00:00", "Z"),
        }

        if snapshot_root.exists():
            self._validate_tournament_snapshot_dir(snapshot_root, metadata)
        else:
            snapshot_root.parent.mkdir(parents=True, exist_ok=True)
            temp_root = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=snapshot_root.parent))
            try:
                for name, payload in payloads.items():
                    _atomic_write_json(temp_root / f"{name}.json", payload)
                _atomic_write_json(temp_root / "metadata.json", metadata)
                try:
                    os.rename(temp_root, snapshot_root)
                except FileExistsError:
                    self._validate_tournament_snapshot_dir(snapshot_root, metadata)
            finally:
                if temp_root.exists():
                    shutil.rmtree(temp_root, ignore_errors=True)

        latest_pointer = {
            "snapshot_id": snapshot_id,
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        _atomic_write_json(self.paths.tournaments / tid / "latest.json", latest_pointer)

        refs = tuple(
            RawPayloadRef(
                payload_type=name,
                tournament_id=tid,
                snapshot_id=snapshot_id,
                sha256=hashes[name],
                fetched_at=fetched,
                relative_path=(
                    Path("tournaments") / tid / "snapshots" / snapshot_id / f"{name}.json"
                ).as_posix(),
            )
            for name in PAYLOAD_TYPES
        )
        return TournamentRawSnapshot(tid, snapshot_id, refs)

    def _validate_tournament_snapshot_dir(self, snapshot_root: Path, expected_metadata: Mapping[str, Any] | None = None) -> None:
        metadata_path = snapshot_root / "metadata.json"
        if not metadata_path.exists():
            raise ValueError(f"snapshot missing metadata: {snapshot_root}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if expected_metadata is not None:
            for field in ("tournament_id", "snapshot_id", "payload_hashes"):
                if metadata.get(field) != expected_metadata.get(field):
                    raise ValueError(f"immutable snapshot metadata mismatch for {snapshot_root}")

        for name in PAYLOAD_TYPES:
            payload_path = snapshot_root / f"{name}.json"
            if not payload_path.exists():
                raise ValueError(f"snapshot missing {name}.json: {snapshot_root}")
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            actual_hash = sha256_json(payload)
            expected_hash = (metadata.get("payload_hashes") or {}).get(name)
            if actual_hash != expected_hash:
                raise ValueError(f"hash mismatch for {payload_path}")

    def load_tournament_snapshot(self, tournament_id: str, snapshot_id: str, *, validate: bool = True) -> dict[str, Any]:
        tid = _safe_component(tournament_id, field_name="tournament_id")
        sid = _safe_component(snapshot_id, field_name="snapshot_id")
        root = self.paths.tournaments / tid / "snapshots" / sid
        if not root.exists():
            raise FileNotFoundError(root)
        if validate:
            self._validate_tournament_snapshot_dir(root)
        return {
            name: json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            for name in PAYLOAD_TYPES
        } | {
            "metadata": json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        }

    def latest_snapshot_id(self, tournament_id: str) -> str | None:
        tid = _safe_component(tournament_id, field_name="tournament_id")
        path = self.paths.tournaments / tid / "latest.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = str(payload.get("snapshot_id") or "").strip()
        return value or None

    def save_catalog_snapshot(
        self,
        catalog_name: str,
        payload: Any,
        *,
        fetched_at: datetime,
    ) -> RawPayloadRef:
        name = _safe_component(catalog_name, field_name="catalog_name")
        fetched = require_utc(fetched_at, field_name="fetched_at")
        digest = sha256_json(payload)
        root = self.paths.catalog / name / "snapshots" / digest
        payload_path = root / "catalog.json"
        metadata_path = root / "metadata.json"
        if root.exists():
            stored = json.loads(payload_path.read_text(encoding="utf-8"))
            if sha256_json(stored) != digest:
                raise ValueError(f"catalog snapshot hash mismatch: {root}")
        else:
            root.parent.mkdir(parents=True, exist_ok=True)
            temp_root = Path(tempfile.mkdtemp(prefix=f".{digest}.", dir=root.parent))
            try:
                _atomic_write_json(temp_root / "catalog.json", payload)
                _atomic_write_json(
                    temp_root / "metadata.json",
                    {
                        "schema_version": "1",
                        "catalog_name": name,
                        "snapshot_id": digest,
                        "sha256": digest,
                        "fetched_at": fetched.isoformat().replace("+00:00", "Z"),
                    },
                )
                try:
                    os.rename(temp_root, root)
                except FileExistsError:
                    stored = json.loads(payload_path.read_text(encoding="utf-8"))
                    if sha256_json(stored) != digest:
                        raise ValueError(f"catalog snapshot hash mismatch: {root}")
            finally:
                if temp_root.exists():
                    shutil.rmtree(temp_root, ignore_errors=True)
        _atomic_write_json(self.paths.catalog / name / "latest.json", {"snapshot_id": digest})
        return RawPayloadRef(
            payload_type=f"catalog:{name}",
            snapshot_id=digest,
            sha256=digest,
            fetched_at=fetched,
            relative_path=(Path("catalog") / name / "snapshots" / digest / "catalog.json").as_posix(),
        )

    def write_run_raw_refs(
        self,
        run_id: str,
        *,
        tournament_ids: tuple[str, ...],
        refs: Iterable[RawPayloadRef],
    ) -> Path:
        rid = _safe_component(run_id, field_name="run_id")
        ids = tuple(str(value).strip() for value in tournament_ids)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("tournament_ids must be sorted and unique")
        rows = [
            {
                "payload_type": ref.payload_type,
                "tournament_id": ref.tournament_id,
                "snapshot_id": ref.snapshot_id,
                "sha256": ref.sha256,
                "fetched_at": ref.fetched_at.isoformat().replace("+00:00", "Z"),
                "relative_path": ref.relative_path,
            }
            for ref in refs
        ]
        rows.sort(key=lambda row: (row.get("tournament_id") or "", row["payload_type"], row["relative_path"]))
        payload = {
            "schema_version": "1",
            "run_id": rid,
            "tournament_ids": list(ids),
            "raw_refs": rows,
        }
        path = self.paths.runs / rid / "raw_refs.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if canonical_json_bytes(existing) != canonical_json_bytes(payload):
                raise ValueError(f"run raw refs are immutable once written: {rid}")
            return path
        _atomic_write_json(path, payload)
        return path

    def load_run_raw_refs(self, run_id: str) -> dict[str, Any]:
        rid = _safe_component(run_id, field_name="run_id")
        path = self.paths.runs / rid / "raw_refs.json"
        if not path.exists():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "ImmutableRawStore",
    "PAYLOAD_TYPES",
    "TournamentRawSnapshot",
    "canonical_json_bytes",
    "sha256_json",
]
