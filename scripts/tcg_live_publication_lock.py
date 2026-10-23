"""Exclusive publication lease for a single shared TCG Live state.

The lock lives next to the canonical state, not under per-candidate work_root,
so independent candidate identities and work roots serialize publication.
Any exception after publication might have begun leaves a durable fail-closed
lock until a human verifies state and destinations and clears it.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import socket
from typing import Iterator


class PublicationLockedError(RuntimeError):
    """A concurrent or interrupted TCG publication needs manual inspection."""


class PublicationLease:
    def __init__(
        self, *, state_path: Path, target_dir: Path,
        lock_paths: tuple[Path, ...],
    ):
        self.state_path = state_path
        self.target_dir = target_dir
        self.lock_paths = lock_paths
        self.active = True
        self._clean = False

    def assert_destination(self, *, state_path: Path, target_dir: Path) -> None:
        if (
            not self.active
            or self.state_path != state_path.resolve()
            or self.target_dir != target_dir.resolve()
            or not all(path.is_file() for path in self.lock_paths)
        ):
            raise PublicationLockedError(
                "TCG publication lease absent or for another destination"
            )

    def mark_clean(self) -> None:
        """Allow release only after full success or a no-write preflight."""
        if not self.active:
            raise PublicationLockedError("TCG publication lease is inactive")
        self._clean = True


def publication_lock_path(state_path: Path) -> Path:
    path = state_path.resolve()
    return path.with_name(path.name + ".publication.lock")


def _destination_lock_path(target: Path) -> Path:
    path = target.resolve()
    return path.with_name(path.name + ".publication.lock")


@contextmanager
def publication_lock(
    *, state_path: Path, target_dir: Path,
    onedrive_target: Path | None = None,
) -> Iterator[PublicationLease]:
    """Reserve shared state + every actual destination across all processes.

    Lock paths are sorted before acquisition (no cyclic wait). A conflict
    during acquisition releases provisional locks; after entry, failure or
    process death keeps every lock for manual reconciliation. No expiry.
    """
    state = state_path.resolve()
    target = target_dir.resolve()
    destinations = [publication_lock_path(state), _destination_lock_path(target)]
    if onedrive_target is not None:
        destinations.append(_destination_lock_path(onedrive_target))
    lock_paths = tuple(sorted(set(destinations), key=lambda p: str(p)))
    acquired: list[Path] = []
    try:
        for lock_path in lock_paths:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise PublicationLockedError(
                    f"TCG publication lock exists at {lock_path}; "
                    "inspect state, OneDrive and public targets before manual recovery"
                ) from exc
            acquired.append(lock_path)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({
                    "schema_version": 1,
                    "started_at": datetime.now(UTC).isoformat(),
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "state_path": str(state),
                    "target_dir": str(target),
                    "onedrive_target": (
                        str(onedrive_target.resolve())
                        if onedrive_target is not None else None
                    ),
                    "lock_paths": [str(p) for p in lock_paths],
                    "recovery": "MANUAL_INSPECTION_REQUIRED_AFTER_INTERRUPTION",
                }, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
    except BaseException:
        # No publication has started. Unwind only the provisional locks.
        for lock_path in reversed(acquired):
            lock_path.unlink(missing_ok=True)
        raise

    lease = PublicationLease(
        state_path=state, target_dir=target, lock_paths=lock_paths
    )
    try:
        yield lease
    finally:
        lease.active = False
        if lease._clean:
            for lock_path in reversed(lock_paths):
                lock_path.unlink(missing_ok=True)
