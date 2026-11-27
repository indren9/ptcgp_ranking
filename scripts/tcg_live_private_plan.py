"""P2 R3 candidate: read-only OneDrive-private latest-completed idempotence gate.

This is deliberately independent of the GitHub public publication state.
No source acquisition, I/O or mutation occurs while planning.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.tcg_live_latest_completed import build_plan
from sources.limitless.tournament_api.release_catalog import parse_utc_datetime
from storage.onedrive_publisher import canonical_game_root


class PrivatePlanError(ValueError):
    pass


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _verified_private_manifest(root: Path) -> dict[str, Any] | None:
    manifest = root / "manifest.json"
    other = [root / "ranking.csv", root / "heatmap.png"]
    if not manifest.exists():
        if any(p.exists() for p in other):
            raise PrivatePlanError("private snapshot is incomplete: manifest absent")
        return None
    try:
        obj = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise PrivatePlanError("private manifest is invalid") from exc
    if not isinstance(obj, dict) or obj.get("game") != "PTCG":
        raise PrivatePlanError("private manifest has wrong game or schema")
    if str(obj.get("format", "")).lower() != "standard":
        raise PrivatePlanError("private manifest has wrong format")
    outputs = obj.get("outputs") or {}
    for key, path in (("ranking", root / "ranking.csv"), ("heatmap", root / "heatmap.png")):
        meta = outputs.get(key) or {}
        expected = str(meta.get("sha256") or "").lower()
        if len(expected) != 64 or not path.is_file() or _sha(path) != expected:
            raise PrivatePlanError(f"private snapshot artifact integrity mismatch: {key}")
    source = obj.get("source") or {}
    if source.get("acquisition") != "Tournament API":
        raise PrivatePlanError("private manifest does not prove canonical Tournament API")
    return obj


def private_plan(
    *, catalog_path: Path,
    target_root: Path | None = None,
    acquisition_started_at: datetime | None = None,
) -> dict[str, Any]:
    """Return noop, publish_required, or fail-closed on corruption/change."""
    now = (acquisition_started_at or datetime.now(UTC)).astimezone(UTC)
    wanted = build_plan(catalog_path=catalog_path, state=None, acquisition_started_at=now)
    target = wanted["latest_completed_window"]
    if str(target["code"]).startswith("TMP-"):
        raise PrivatePlanError("provisional identity cannot be published as completed")
    root = (target_root or canonical_game_root("PTCG")).resolve()
    present = _verified_private_manifest(root)
    precondition = {
        "target_root": str(root),
        "manifest_sha256": _sha(root / "manifest.json") if present is not None else None,
        "catalog_path": str(Path(catalog_path).resolve()),
        "catalog_sha256": _sha(Path(catalog_path).resolve()),
        "planned_at_utc": now.isoformat().replace("+00:00", "Z"),
    }
    if present is None:
        return {**wanted, "action": "publish_required", "reason": "no_private_snapshot",
                "private_precondition": precondition}
    existing_set = present.get("set") or {}
    existing_window = (present.get("snapshot") or {}).get("collection_window") or {}
    source_info = ((present.get("source") or {}).get("tournament_api") or {})
    old = {
        "code": str(existing_set.get("code") or ""),
        "name": str(existing_set.get("name") or ""),
        "start": existing_window.get("start"),
        "end": existing_window.get("end"),
        "catalog_version": source_info.get("catalog_version"),
    }
    if not all(old.get(k) for k in ("code", "name", "start", "end", "catalog_version")):
        raise PrivatePlanError("private manifest identity/lineage is incomplete")
    same = all(str(old.get(k)) == str(target.get(k)) for k in old)
    if same:
        return {**wanted, "action": "noop", "reason": "verified_private_snapshot_already_current"}
    if old["code"].casefold() == str(target["code"]).casefold():
        raise PrivatePlanError("same completed code with changed window/lineage: manual review")
    try:
        old_end = parse_utc_datetime(old["end"], field_name="private old end")
        new_end = parse_utc_datetime(target["end"], field_name="private new end")
    except (TypeError, ValueError) as exc:
        raise PrivatePlanError("private snapshot UTC boundary is invalid") from exc
    if old_end >= new_end:
        raise PrivatePlanError("completed snapshot would regress to older window")
    return {**wanted, "action": "publish_required", "reason": "new_completed_window",
            "private_precondition": precondition}
