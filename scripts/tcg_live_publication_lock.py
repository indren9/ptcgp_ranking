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
    def __init__(self, *, state_path: Path, target_dir: Path, lock_path: Path):
        self.state_path = state_path
        self.target_dir = target_dir
        self.lock_path = lock_path
        self.active = True
        self._clean = False

    def assert_destination(self, *, state_path: Path, target_dir: Path) -> None:
        if (
            not self.active
            or self.state_path != state_path.resolve()
            or self.target_dir != target_dir.resolve()
            or not self.lock_path.is_file()
        ):
            raise PublicationLockedError(
                "TCG publication lease absent or for another destination"
            )

    def mark_clean(self) -> None:
        """Allow lock removal only after full success or a no-write preflight."""
        if not self.active:
            raise PublicationLockedError("TCG publication lease is inactive")
        self._clean = True


def publication_lock_path(state_path: Path) -> Path:
    path = state_path.resolve()
    return path.with_name(path.name + ".publication.lock")


@contextmanager
def publication_lock(
    *, state_path: Path, target_dir: Path
) -> Iterator[PublicationLease]:
    """Fail-closed cross-process mutex; never silently expire stale locks.

    All TCG publishers for the same canonical state must use this exact path.
    The path is stable across identities, work roots and process restarts.
    """
    state = state_path.resolve()
    target = target_dir.resolve()
    lock_path = publication_lock_path(state)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PublicationLockedError(
            f"TCG publication lock exists at {lock_path}; "
            "inspect state, OneDrive and public targets before manual recovery"
        ) from exc

    lease = PublicationLease(
        state_path=state, target_dir=target, lock_path=lock_path
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({
                "schema_version": 1,
                "started_at": datetime.now(UTC).isoformat(),
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "state_path": str(state),
                "target_dir": str(target),
                "recovery": "MANUAL_INSPECTION_REQUIRED_AFTER_INTERRUPTION",
            }, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield lease
    finally:
        lease.active = False
        # Never clear on unverified exceptions, partial OneDrive publication,
        # or abrupt process death. Explicitly marked clean = no unsafe retry.
        if lease._clean:
            lock_path.unlink(missing_ok=True)
