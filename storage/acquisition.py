from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


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
    """Small cache-first adapter for API JSON responses; not a reproducibility store."""

    def __init__(self, root: str | Path = "cache/limitless_api") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def set_json(self, key: str, value: Any) -> None:
        path = self._path(key)
        payload = {
            "cache_key": str(key),
            "value": value,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("cache_key") != str(key):
            return None
        return payload.get("value")


__all__ = ["AcquisitionPaths", "FileJsonCache", "init_acquisition_paths"]
