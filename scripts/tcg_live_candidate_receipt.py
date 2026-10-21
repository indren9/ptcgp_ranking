"""Local, verified TCG Live candidate receipt; never a publication pointer.

Receipts are scoped to an exact completed window, effective configuration,
catalog bytes and software revision. They only suppress redundant
non-publishing acquisitions in the same persistent work root.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = 1
REQUIRED_OUTPUTS = (
    "run_manifest",
    "mars_ranking",
    "score_flat",
    "wr_matrix",
    "n_dir_matrix",
)
REQUIRED_FILES = (
    "live_manifest",
    "replay_manifest",
    *REQUIRED_OUTPUTS,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def candidate_identity(
    *,
    window: Mapping[str, Any],
    config_path: Path,
    catalog_path: Path,
    revision: str,
) -> dict[str, Any]:
    fields = ("code", "name", "start", "end", "catalog_version")
    if any(field not in window for field in fields):
        raise ValueError("candidate window has missing identity fields")
    if not revision.strip():
        raise ValueError("candidate software revision is missing")
    return {
        "window": {field: window[field] for field in fields},
        "config_sha256": sha256_file(config_path),
        "catalog_sha256": sha256_file(catalog_path),
        "software_revision": revision,
    }


def _receipt_path(work_root: Path, identity: Mapping[str, Any]) -> Path:
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return work_root / "candidate-receipts" / (key + ".json")


@contextmanager
def candidate_lock(
    *, work_root: Path, identity: Mapping[str, Any]
) -> Iterator[None]:
    """Exclude concurrent acquisition/publication for a single candidate.

    A crash may leave the lock in place: fail closed and require manual
    inspection of the prior attempt before removing it. No time-based expiry.
    """
    receipt_path = _receipt_path(work_root, identity)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = receipt_path.with_suffix(".lock")
    try:
        descriptor = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError as exc:
        raise RuntimeError(
            "TCG Live candidate locked; inspect existing attempt before retry"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid()}, sort_keys=True) + "\\n")
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _record_file(path: Path, work_root: Path) -> dict[str, str]:
    root = work_root.resolve(strict=True)
    checked = path.resolve(strict=True)
    if not checked.is_file() or not checked.is_relative_to(root):
        raise ValueError("candidate file must be under the work root")
    return {
        "path": checked.relative_to(root).as_posix(),
        "sha256": sha256_file(checked),
    }


def _verified_file(
    work_root: Path, record: Mapping[str, Any]
) -> Path:
    relative = record.get("path")
    digest = record.get("sha256")
    if not isinstance(relative, str) or not relative:
        raise ValueError("invalid candidate file path")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("invalid candidate file checksum")
    path = work_root / relative
    checked = _record_file(path, work_root)
    if checked != {"path": relative, "sha256": digest}:
        raise ValueError("candidate file checksum/path mismatch")
    return path


def load_verified_candidate(
    *,
    work_root: Path,
    identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    receipt_path = _receipt_path(work_root, identity)
    if not receipt_path.exists():
        return None

    # Existing but invalid receipts are errors, not permission to fetch again
    # or to reuse unverified work. A human must resolve the conflict.
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("identity") != identity
        ):
            raise ValueError("candidate receipt identity/schema mismatch")
        records = receipt["files"]
        report = receipt["report"]
        if not isinstance(records, dict) or not isinstance(report, dict):
            raise ValueError("candidate receipt fields are malformed")
        if set(records) != set(REQUIRED_FILES):
            raise ValueError("candidate receipt file inventory is incomplete")
        checked_files = {
            key: _verified_file(work_root, records[key])
            for key in REQUIRED_FILES
        }
        live = json.loads(
            checked_files["live_manifest"].read_text(encoding="utf-8")
        )
        replay = json.loads(
            checked_files["replay_manifest"].read_text(encoding="utf-8")
        )
        run_id = report["canonical_live_run_id"]
        if (
            not isinstance(run_id, str)
            or not run_id
            or live.get("run_id") != run_id
            or replay.get("run_id") != run_id
            or report.get("offline_network_calls") != 0
            or not isinstance(report.get("object_manifest_key"), str)
            or not report["object_manifest_key"]
        ):
            raise ValueError("candidate manifest/run identity mismatch")
        source_run = (work_root / receipt["source_run"]).resolve(strict=True)
        if (
            not source_run.is_dir()
            or not source_run.is_relative_to(work_root.resolve(strict=True))
        ):
            raise ValueError("candidate source run is outside work root")
        if not all(
            checked_files[key].resolve(strict=True).is_relative_to(source_run)
            for key in REQUIRED_OUTPUTS
        ):
            raise ValueError("candidate outputs do not belong to source run")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(
            "TCG Live candidate receipt invalid; stop for manual review"
        ) from exc

    return {
        "action": "candidate_ready",
        "reason": "validated_candidate_already_ready",
        "latest_completed_window": dict(identity["window"]),
        **report,
        "candidate_source_run": str(source_run),
        "candidate_ready": True,
        "candidate_reused": True,
        "network_calls": 0,
        "bundle_dir": None,
        "publication": None,
        "onedrive_publication": None,
        "published": False,
    }


def save_verified_candidate(
    *,
    work_root: Path,
    identity: Mapping[str, Any],
    live_manifest: Path,
    replay_manifest: Path,
    outputs: Mapping[str, Path],
    source_run: Path,
    run_id: str,
    object_manifest_key: str,
    mars_rows: Any,
) -> Path:
    if not run_id or not object_manifest_key:
        raise ValueError("candidate requires persisted raw run provenance")
    root = work_root.resolve(strict=True)
    source = source_run.resolve(strict=True)
    if not source.is_dir() or not source.is_relative_to(root):
        raise ValueError("candidate source run must belong to work root")
    if any(not outputs.get(key) for key in REQUIRED_OUTPUTS):
        raise ValueError("candidate is missing required Core/MARS outputs")
    files = {
        "live_manifest": _record_file(live_manifest, root),
        "replay_manifest": _record_file(replay_manifest, root),
    }
    files.update({
        key: _record_file(Path(outputs[key]), root)
        for key in REQUIRED_OUTPUTS
    })
    if any(
        not (root / files[key]["path"]).resolve(strict=True).is_relative_to(source)
        for key in REQUIRED_OUTPUTS
    ):
        raise ValueError("candidate output is not inside its source run")

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "identity": dict(identity),
        "files": files,
        "source_run": source.relative_to(root).as_posix(),
        "report": {
            "canonical_live_run_id": run_id,
            "object_manifest_key": object_manifest_key,
            "offline_network_calls": 0,
            "mars_rows": mars_rows,
        },
    }
    receipt_path = _receipt_path(root, identity)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path.exists():
        raise RuntimeError(
            "candidate receipt already exists; review before overwriting"
        )

    # Only a fully successful LIVE/persist/OFFLINE/Core/MARS sequence reaches
    # this write. The atomic replace avoids treating partial JSON as success.
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=receipt_path.parent,
            prefix=".candidate-", suffix=".tmp", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, receipt_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return receipt_path
