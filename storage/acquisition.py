from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable


@dataclass(frozen=True)
class AcquisitionPaths:
    root: Path
    catalog: Path
    tournaments: Path
    runs: Path


def init_acquisition_paths(root: str | Path = "data/raw/limitless_api") -> AcquisitionPaths:
    base = Path(root)
    paths = AcquisitionPaths(
        root=base,
        catalog=base / "catalog",
        tournaments=base / "tournaments",
        runs=base / "runs",
    )
    for path in (paths.root, paths.catalog, paths.tournaments, paths.runs):
        path.mkdir(parents=True, exist_ok=True)
    return paths


class FileJsonCache:
    """TTL-aware persistent cache for API JSON responses.

    This cache is an HTTP optimization only. It is not a reproducibility
    store; immutable acquisition evidence lives in ImmutableRawStore.
    """

    def __init__(
        self,
        root: str | Path = "cache/limitless_api",
        *,
        ttl_min: float = 0.0,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if float(ttl_min) < 0:
            raise ValueError("ttl_min must be non-negative")

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_min = float(ttl_min)
        self._now = now_fn or (lambda: datetime.now(UTC))

        self.hit_count = 0
        self.miss_count = 0
        self.expired_miss_count = 0
        self.legacy_miss_count = 0

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def _now_utc(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime):
            raise TypeError("now_fn must return datetime")
        if value.tzinfo is None:
            raise ValueError("now_fn must return timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _parse_cached_at(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    def set_json(self, key: str, value: Any) -> None:
        path = self._path(key)
        payload = {
            "cache_key": str(key),
            "cached_at": self._now_utc().isoformat().replace("+00:00", "Z"),
            "value": value,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    def get_json(self, key: str) -> Any | None:
        path = self._path(key)

        if not path.exists():
            self.miss_count += 1
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.miss_count += 1
            return None

        if (
            not isinstance(payload, dict)
            or payload.get("cache_key") != str(key)
        ):
            self.miss_count += 1
            return None

        cached_at = self._parse_cached_at(payload.get("cached_at"))
        if cached_at is None:
            self.miss_count += 1
            self.legacy_miss_count += 1
            return None

        # Canonical production policy: TTL=0 disables persistent reads.
        if self.ttl_min <= 0:
            self.miss_count += 1
            self.expired_miss_count += 1
            return None

        age_seconds = (
            self._now_utc() - cached_at
        ).total_seconds()

        if age_seconds <= self.ttl_min * 60.0:
            self.hit_count += 1
            return payload.get("value")

        self.miss_count += 1
        self.expired_miss_count += 1
        return None


__all__ = ["AcquisitionPaths", "FileJsonCache", "init_acquisition_paths"]
