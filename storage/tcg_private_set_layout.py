"""MARS P2 WP3 — TCG-private per-expansion layout, aligned with Pocket.

CANDIDATE ONLY.  Never changes Pocket's publication routes or public GitHub.
Readers use verified immutable expansion directories; latest_completed is a
derived selection, not a second mutable copy of the bundle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from typing import Any, Callable, Mapping

from domain.expansions import strip_expansion_code_prefix
from scripts.latest_completed_meta import validate_bundle
from scripts.tcg_live_latest_completed import build_plan
from sources.limitless.tournament_api.release_catalog import parse_utc_datetime
from storage.routing import _sanitize_name

FORMAT = "standard"
FILES = {
    "ranking": "rankings/mars/mars_ranking_latest.csv",
    "heatmap": "matrices/heatmaps/wr_heatmap_latest.png",
    "manifest": "run/run_manifest_latest.json",
}
STAGING_PREFIX = ".p2-uncommitted-"


class LayoutError(RuntimeError):
    """Unsafe directory, state or identity; do not publish."""


@dataclass(frozen=True)
class SetPublication:
    status: str
    target_root: str
    set_code: str
    new_sha256: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target_root": self.target_root,
            "set_code": self.set_code,
            "new_sha256": dict(self.new_sha256),
        }


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ranking_root(root: Path | str | None = None) -> Path:
    """TCG-only default. Never reuse Pocket's PTCGP_ONEDRIVE_META_ROOT."""
    candidate = (
        Path(root) if root is not None
        else Path.home() / "OneDrive" / "Giochi" / "Pokemon" / "TCG" / "Ranking"
    )
    return candidate.expanduser().resolve()


def _safe_root(root: Path) -> None:
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise LayoutError("ranking root is a filesystem redirect")
    parent = root.parent
    if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
        raise LayoutError("ranking root parent is a filesystem redirect")


def _set_dir_name(window: Mapping[str, Any]) -> str:
    code = str(window.get("code") or "").strip()
    name = str(window.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,29}", code) or code.startswith("TMP-"):
        raise LayoutError("completed code must be canonical and safe")
    if not name:
        raise LayoutError("completed name is missing")
    clean = _sanitize_name(strip_expansion_code_prefix(code, name))
    if not clean:
        raise LayoutError("completed name is not usable as a directory")
    return f"{code}__{clean}"


def set_path(root: Path | str, window: Mapping[str, Any]) -> Path:
    base = ranking_root(root)
    target = base / FORMAT / _set_dir_name(window)
    if target.parent != base / FORMAT:
        raise LayoutError("set directory escapes the ranking root")
    return target


def verify_set_directory(path: Path) -> dict[str, Any]:
    """Validate one consumer-visible directory without mutating it."""
    if not path.is_dir() or path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    ):
        raise LayoutError("completed set folder missing or redirected")
    for relative in FILES.values():
        file = path / relative
        if not file.is_file() or file.is_symlink():
            raise LayoutError("completed set file missing or redirected: " + relative)
    try:
        obj = json.loads((path / FILES["manifest"]).read_text(encoding="utf-8"))
        if obj.get("game") != "PTCG" or str(obj.get("format") or "").lower() != FORMAT:
            raise LayoutError("private set manifest has wrong game/format")
        if ((obj.get("source") or {}).get("acquisition")) != "Tournament API":
            raise LayoutError("private set manifest does not prove Tournament API")
        window = (obj.get("snapshot") or {}).get("collection_window") or {}
        identity = {
            "code": (obj.get("set") or {}).get("code"),
            "name": (obj.get("set") or {}).get("name"),
        }
        if _set_dir_name(identity) != path.name:
            raise LayoutError("set folder identity disagrees with manifest")
        start = parse_utc_datetime(window.get("start"), field_name="private start")
        end = parse_utc_datetime(window.get("end"), field_name="private end")
        if not start < end:
            raise LayoutError("invalid completed window")
        outputs = obj.get("outputs") or {}
        for kind in ("ranking", "heatmap"):
            entry = outputs.get(kind) or {}
            if entry.get("path") != FILES[kind] or entry.get("sha256") != _sha(path / FILES[kind]):
                raise LayoutError("private set hash or output path mismatch: " + kind)
        version = ((obj.get("source") or {}).get("tournament_api") or {}).get("catalog_version")
        if not version:
            raise LayoutError("missing completed catalog version")
        return obj
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        if isinstance(exc, LayoutError):
            raise
        raise LayoutError("invalid or incomplete private set directory") from exc


def latest_completed(root: Path | str) -> tuple[Path, dict[str, Any]] | None:
    """Select latest VERIFIED completed set; reject ambiguous/corrupted history."""
    base = ranking_root(root)
    standard = base / FORMAT
    if not standard.exists():
        return None
    if not standard.is_dir():
        raise LayoutError("standard destination is not a directory")
    winners = []
    seen_codes: set[str] = set()
    seen_ends: set[datetime] = set()
    for folder in standard.iterdir():
        if folder.name.startswith(STAGING_PREFIX):
            raise LayoutError("incomplete staging directory requires operator review")
        if not folder.is_dir() or "__" not in folder.name:
            raise LayoutError("unexpected entry in completed-set index")
        manifest = verify_set_directory(folder)
        code = str((manifest.get("set") or {})["code"]).casefold()
        end = parse_utc_datetime(manifest["snapshot"]["collection_window"]["end"], field_name="latest end")
        if code in seen_codes or end in seen_ends:
            raise LayoutError("ambiguous completed set identity or timestamp")
        seen_codes.add(code)
        seen_ends.add(end)
        winners.append((end, folder, manifest))
    if not winners:
        return None
    winners.sort(key=lambda item: item[0])
    return winners[-1][1], winners[-1][2]


def _window_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    source = manifest.get("source") or {}
    return {
        "code": (manifest.get("set") or {}).get("code"),
        "name": (manifest.get("set") or {}).get("name"),
        "start": (manifest.get("snapshot") or {}).get("collection_window", {}).get("start"),
        "end": (manifest.get("snapshot") or {}).get("collection_window", {}).get("end"),
        "catalog_version": (source.get("tournament_api") or {}).get("catalog_version"),
    }


def plan_set_publication(
    *, catalog_path: Path, acquisition_started_at: datetime | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Read-only private planner. Pins prior state for locked revalidation."""
    started = (acquisition_started_at or datetime.now(UTC)).astimezone(UTC)
    base = ranking_root(root)
    _safe_root(base)
    catalog = Path(catalog_path).resolve()
    source = build_plan(catalog_path=catalog, state=None, acquisition_started_at=started)
    wanted = source["latest_completed_window"]
    proposed = set_path(base, wanted)
    previous = latest_completed(base)
    old_path, old_manifest = previous if previous else (None, None)
    guard = {
        "ranking_root": str(base),
        "prior_relative": str(old_path.relative_to(base)).replace("\\", "/") if old_path else None,
        "prior_manifest_sha256": _sha(old_path / FILES["manifest"]) if old_path else None,
        "catalog_path": str(catalog),
        "catalog_sha256": _sha(catalog),
        "planned_at_utc": started.isoformat().replace("+00:00", "Z"),
        "new_relative": str(proposed.relative_to(base)).replace("\\", "/"),
    }
    if old_manifest is None:
        return {**source, "action": "publish_required", "reason": "no_private_snapshot",
                "set_layout_precondition": guard}
    old = _window_from_manifest(old_manifest)
    if all(str(old[k]) == str(wanted[k]) for k in old):
        return {**source, "action": "noop", "reason": "verified_private_snapshot_already_current"}
    if str(old["code"]).casefold() == str(wanted["code"]).casefold():
        raise LayoutError("same completed code has changed lineage")
    old_end = parse_utc_datetime(old["end"], field_name="old completed end")
    new_end = parse_utc_datetime(wanted["end"], field_name="new completed end")
    if old_end >= new_end:
        raise LayoutError("completed set would regress")
    if proposed.exists():
        raise LayoutError("new completed set directory already exists")
    return {**source, "action": "publish_required", "reason": "new_completed_window",
            "set_layout_precondition": guard}


def _fsync_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as inp, dest.open("xb") as out:
        shutil.copyfileobj(inp, out, length=1024 * 1024)
        out.flush()
        os.fsync(out.fileno())


def _validate_plan_locked(plan: Mapping[str, Any], base: Path) -> Path:
    guard = plan.get("set_layout_precondition")
    if not isinstance(guard, dict) or set(guard) != {
        "ranking_root", "prior_relative", "prior_manifest_sha256",
        "catalog_path", "catalog_sha256", "planned_at_utc", "new_relative",
    }:
        raise LayoutError("set publication plan lacks required precondition")
    if plan.get("action") != "publish_required" or guard["ranking_root"] != str(base):
        raise LayoutError("private set publication plan and target mismatch")
    catalog = Path(str(guard["catalog_path"]))
    if not catalog.is_file() or _sha(catalog) != guard["catalog_sha256"]:
        raise LayoutError("catalog changed after private planning")
    started = parse_utc_datetime(guard["planned_at_utc"], field_name="planned_at")
    now = datetime.now(UTC)
    source = build_plan(catalog_path=catalog, state=None,
                        acquisition_started_at=max(started, now))
    wanted = plan.get("latest_completed_window") or {}
    if any(str(source["latest_completed_window"].get(k)) != str(wanted.get(k))
           for k in ("code", "name", "start", "end", "catalog_version")):
        raise LayoutError("private completed release changed after planning")
    target = set_path(base, wanted)
    if str(target.relative_to(base)).replace("\\", "/") != guard["new_relative"]:
        raise LayoutError("proposed completed set directory changed")
    old = latest_completed(base)
    old_path, _ = old if old else (None, None)
    relative = str(old_path.relative_to(base)).replace("\\", "/") if old_path else None
    if relative != guard["prior_relative"]:
        raise LayoutError("latest completed set changed after planning")
    old_sha = _sha(old_path / FILES["manifest"]) if old_path else None
    if old_sha != guard["prior_manifest_sha256"]:
        raise LayoutError("previous set manifest changed after planning")
    if old:
        prior = _window_from_manifest(old[1])
        if str(prior["code"]).casefold() == str(wanted["code"]).casefold():
            raise LayoutError("private same-code publication is forbidden")
        if parse_utc_datetime(prior["end"],field_name="prior end") >= parse_utc_datetime(
            wanted["end"],field_name="new end"
        ):
            raise LayoutError("private publication would regress")
    if target.exists():
        raise LayoutError("completed set folder already exists")
    return target


def publish_set_bundle(
    *, bundle_dir: Path, plan: Mapping[str, Any],
    root: Path | str | None = None,
    failure_injector: Callable[[str, str], None] | None = None,
) -> SetPublication:
    """Stage three files under a hidden sibling, then rename directory ONCE.

    This protects local reader visibility, not cloud sync or power-loss durability.
    """
    base = ranking_root(root)
    _safe_root(base)
    src = Path(bundle_dir).resolve()
    verified = validate_bundle(src, {
        "action": "publish",
        "completed_set": dict(plan["latest_completed_window"]),
    })
    if verified.get("game") != "PTCG" or str(verified.get("format") or "").lower() != FORMAT:
        raise LayoutError("source bundle has wrong game or format")
    standard = base / FORMAT
    base.mkdir(parents=True, exist_ok=True)
    lock = base / ".p2-writer-lock"
    try:
        handle = lock.open("x",encoding="utf-8")
    except FileExistsError as exc:
        raise LayoutError("another writer or pending recovery owns the set layout") from exc
    stage: Path | None = None
    try:
        handle.write("MARS P2 private TCG set-layout writer\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        target = _validate_plan_locked(plan, base)
        standard.mkdir(parents=True,exist_ok=True)
        stage = standard / (STAGING_PREFIX + uuid.uuid4().hex)
        stage.mkdir()
        for kind in ("ranking","heatmap"):
            source_file = src / ("ranking.csv" if kind=="ranking" else "heatmap.png")
            dest = stage / FILES[kind]
            _fsync_copy(source_file, dest)
            expected = str((verified.get("outputs") or {}).get(kind, {}).get("sha256") or "")
            if not expected or _sha(dest) != expected:
                raise LayoutError("staged file SHA-256 mismatch: " + kind)
            if failure_injector:
                failure_injector("after_copy", kind)
        manifest = json.loads((src/"manifest.json").read_text(encoding="utf-8"))
        for kind in ("ranking","heatmap"):
            manifest["outputs"][kind]["path"] = FILES[kind]
        dest_manifest = stage / FILES["manifest"]
        dest_manifest.parent.mkdir(parents=True,exist_ok=True)
        with dest_manifest.open("x",encoding="utf-8",newline="\n") as out:
            out.write(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+"\n")
            out.flush()
            os.fsync(out.fileno())
        # The staged directory is hidden, so check content using the eventual
        # canonical name via the manifest without reading any published path.
        if _set_dir_name(_window_from_manifest(manifest)) != target.name:
            raise LayoutError("staged manifest identity conflicts with target")
        for kind in ("ranking","heatmap"):
            if _sha(stage/FILES[kind]) != manifest["outputs"][kind]["sha256"]:
                raise LayoutError("staged output checksum mismatch")
        if failure_injector:
            failure_injector("before_commit", target.name)
        if target.exists():
            raise LayoutError("consumer target appeared during staging")
        os.rename(stage, target)
        stage = None
        verify_set_directory(target)
        return SetPublication(
            "PASS", str(target), str(plan["latest_completed_window"]["code"]),
            {kind:_sha(target/path) for kind,path in FILES.items()},
        )
    finally:
        # A regular exception may leave hidden staging as evidence; fail-closed.
        # A hard crash also leaves lock/staging for explicit review.
        lock.unlink(missing_ok=True)
