"""Checksummed atomic state, immutable generations, and fail-closed locking."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import uuid

from .model import CheckpointError, IntegrityError, RebuildError, canonical, digest, stamp


def now() -> str:
    return stamp(datetime.now(UTC))


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    atomic_write(path, canonical(value) + b"\n")


def save_state(path: Path, state: dict) -> None:
    write_json(path, {"state": state, "sha256": digest(state)})


def load_state(path: Path, window_id: str, stages: tuple[str, ...]) -> dict | None:
    if not path.exists():
        return None
    try:
        wrapper = json.loads(path.read_bytes())
        state = wrapper["state"]
        if wrapper["sha256"] != digest(state):
            raise ValueError("state checksum mismatch")
        if state["schema_version"] != 1 or state["window_id"] != window_id:
            raise ValueError("unsupported schema/window identity")
        if set(state["stages"]) != set(stages) or not isinstance(state["raw"], dict):
            raise ValueError("invalid stage/raw schema")
        for stage in state["stages"].values():
            if stage["status"] not in {"PENDING", "RUNNING", "VALID", "STALE", "FAILED"}:
                raise ValueError("invalid stage status")
            if stage["status"] == "VALID":
                for field in ("input_fingerprint", "output_fingerprint", "artifact", "started_at", "completed_at"):
                    if not stage.get(field):
                        raise ValueError(f"missing valid-stage field {field}")
        if state["status"] not in {"PENDING", "PARTIAL", "BLOCKED", "DONE", "STALE"}:
            raise ValueError("invalid overall status")
        for item in state["raw"].values():
            if item["state"] not in {"PENDING", "VALID", "FAILED_RETRYABLE", "FAILED_BLOCKED", "STALE"}:
                raise ValueError("invalid raw state")
            if item["state"] == "VALID" and not all(item.get(k) for k in ("artifact", "sha256", "input_fingerprint")):
                raise ValueError("incomplete valid raw record")
        for key in ("window", "window_fingerprint", "current_stage", "tournament_ids", "counts", "last_checkpoint", "error", "provenance"):
            state[key]
        return state
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise CheckpointError(f"Corrupt/unsupported checkpoint {path}: {exc}. Preserve it and restore a verified backup; never reset silently.") from exc


def inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise IntegrityError("artifact reference escapes its isolated window")
    return candidate


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def artifact(root: Path, directory: Path) -> dict:
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise IntegrityError("symlink/escaped generation output")
        if path.is_file():
            # Flush adapter-created files before publishing the state pointer.
            with path.open("r+b") as handle:
                os.fsync(handle.fileno())
            files[path.relative_to(directory).as_posix()] = file_digest(path)
    if not files or "result.json" not in files:
        raise IntegrityError("generation has no committed result")
    return {"path": directory.relative_to(root).as_posix(), "files": files, "sha256": digest(files)}


def check_artifact(root: Path, ref: dict) -> bool:
    try:
        directory = inside(root, ref["path"])
        if not ref["files"] or ref["sha256"] != digest(ref["files"]):
            return False
        actual = {p.relative_to(directory).as_posix(): file_digest(p)
                  for p in directory.rglob("*") if p.is_file() and inside(directory, p.relative_to(directory).as_posix())}
        return actual == ref["files"]
    except (OSError, KeyError, ValueError, IntegrityError):
        return False


def read_result(root: Path, ref: dict):
    if not check_artifact(root, ref):
        raise IntegrityError("generation integrity failure")
    return json.loads((inside(root, ref["path"]) / "result.json").read_bytes())


@contextmanager
def writer_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "writer.lock"
    token = uuid.uuid4().hex
    metadata = {"token": token, "pid": os.getpid(), "host": socket.gethostname(), "created_at": now()}
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RebuildError(f"Writer lock exists: {lock}. Active, stale, or unknown locks all fail closed. Verify host/PID and that no writer is running, then manually archive the lock before retrying. Never delete an unknown lock.") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical(metadata))
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        # Only release the exact lock owned by this process.
        if lock.exists() and lock.read_bytes() == canonical(metadata):
            lock.unlink()
