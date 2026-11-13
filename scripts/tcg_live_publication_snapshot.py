"""Read-once, canonical validation of an immutable TCG public bundle snapshot.

Only the bytes returned by this function may be passed to TCG publishers.
The temporary validation copy prevents source mutations between validation
and the bytes selected for publication (F-08-R3).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from scripts.latest_completed_meta import validate_bundle


REQUIRED_SNAPSHOT_FILES = (
    "ranking.csv",
    "heatmap.png",
    "manifest.json",
    "fragment.md",
)
PUBLIC_SNAPSHOT_FILES = ("ranking.csv", "heatmap.png", "manifest.json")


@dataclass(frozen=True)
class ValidatedTCGSnapshot:
    payloads: Mapping[str, bytes]
    hashes: Mapping[str, str]
    manifest: Mapping[str, Any]


def validated_tcg_snapshot(
    bundle_dir: Path, plan: Mapping[str, Any]
) -> ValidatedTCGSnapshot:
    """Validate one fixed byte snapshot using the existing bundle contract.

    Reading each source exactly once eliminates the preflight-to-publish
    source-file TOCTOU. The canonical validator reads only isolated copies.
    """
    payloads = {
        name: (bundle_dir / name).read_bytes()
        for name in REQUIRED_SNAPSHOT_FILES
    }
    with TemporaryDirectory(prefix="tcg-public-snapshot-") as directory:
        root = Path(directory)
        for name, payload in payloads.items():
            (root / name).write_bytes(payload)
        manifest = validate_bundle(
            root,
            {
                "action": "publish",
                "completed_set": dict(plan["latest_completed_window"]),
            },
        )
    window = plan["latest_completed_window"]
    release = manifest.get("set") or {}
    if (
        str(manifest.get("game") or "").upper() != "PTCG"
        or str(manifest.get("format") or "").lower() != "standard"
        or str(release.get("code") or "") != str(window.get("code") or "")
        or str(release.get("name") or "") != str(window.get("name") or "")
    ):
        raise ValueError("TCG snapshot game/format/release identity mismatch")
    hashes = {
        name: hashlib.sha256(payloads[name]).hexdigest()
        for name in PUBLIC_SNAPSHOT_FILES
    }
    return ValidatedTCGSnapshot(
        payloads=payloads,
        hashes=hashes,
        manifest=manifest,
    )
