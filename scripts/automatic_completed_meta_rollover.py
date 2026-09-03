from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.latest_completed_meta import (
    CatalogEntry,
    build_publication_plan,
    publish_bundle,
    validate_bundle,
)
from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot,
)

CANONICAL_SOURCE = "Limitless Tournament API"
PUBLICATION_ALLOWLIST = frozenset(
    {
        "README.md",
        ".github/latest-completed-meta-state.json",
        "public/latest-meta/ranking.csv",
        "public/latest-meta/heatmap.png",
        "public/latest-meta/manifest.json",
    }
)


def _iso_z(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


@dataclass(frozen=True)
class ReleaseWindow:
    completed_code: str
    completed_name: str
    current_code: str
    current_name: str
    start: str
    end: str
    catalog_version: str


@dataclass(frozen=True)
class ReplayEvidence:
    source_run: Path
    manifest_path: Path
    diagnostics_path: Path


@dataclass(frozen=True)
class RolloverHooks:
    acquire_live: Callable[[Mapping[str, Any], ReleaseWindow], Path]
    persist_raw: Callable[[str, Path], Any]
    replay_offline: Callable[[str, Mapping[str, Any], ReleaseWindow], ReplayEvidence]
    produce_bundle: Callable[[Mapping[str, Any], Path, Path], Path]
    restore_prior_raw: Callable[[Mapping[str, Any], ReleaseWindow], Any] | None = None


@dataclass(frozen=True)
class RolloverResult:
    plan: Mapping[str, Any]
    published: bool
    run_id: str | None
    restored_prior_raw: bool
    restore_failure_type: str | None


def derive_release_window(
    plan: Mapping[str, Any],
    release_catalog_path: str | Path,
) -> ReleaseWindow:
    current_plan = plan.get("current_set") or {}
    completed_plan = plan.get("completed_set") or {}
    current_code = str(current_plan.get("code") or "").strip()
    completed_code = str(completed_plan.get("code") or "").strip()
    if not current_code or not completed_code:
        raise ValueError("publication plan must identify current and completed sets")
    if current_code.casefold() == completed_code.casefold():
        raise ValueError("current and completed sets must differ")

    catalog = load_release_catalog_snapshot(release_catalog_path)
    by_code = {release.code.casefold(): release for release in catalog.releases}
    try:
        current = by_code[current_code.casefold()]
        completed = by_code[completed_code.casefold()]
    except KeyError as exc:
        raise ValueError("planned set is missing from canonical release catalog") from exc

    planned_current_name = str(current_plan.get("name") or "").strip()
    planned_completed_name = str(completed_plan.get("name") or "").strip()
    if planned_current_name and planned_current_name.casefold() != current.name.casefold():
        raise ValueError("current set name disagrees with canonical release catalog")
    if planned_completed_name and planned_completed_name.casefold() != completed.name.casefold():
        raise ValueError("completed set name disagrees with canonical release catalog")
    if completed.next_release_datetime != current.release_datetime:
        raise ValueError("completed/current sets are not adjacent in canonical release catalog")

    return ReleaseWindow(
        completed_code=completed.code,
        completed_name=completed.name,
        current_code=current.code,
        current_name=current.name,
        start=_iso_z(completed.release_datetime),
        end=_iso_z(current.release_datetime),
        catalog_version=catalog.catalog_version,
    )


def validate_canonical_live_manifest(
    manifest_path: str | Path,
    plan: Mapping[str, Any],
    window: ReleaseWindow,
) -> dict[str, Any]:
    path = Path(manifest_path)
    payload = _read_json(path)
    if payload.get("source") != CANONICAL_SOURCE:
        raise ValueError("completed-meta acquisition must use Limitless Tournament API")

    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("canonical acquisition manifest is missing run_id")

    scope = payload.get("scope") or {}
    expected_completed = plan.get("completed_set") or {}
    if str(scope.get("game") or "").upper() != "POCKET":
        raise ValueError("canonical acquisition manifest game must be POCKET")
    if str(scope.get("format") or "").upper() != "STANDARD":
        raise ValueError("canonical acquisition manifest format must be STANDARD")
    if str(scope.get("set_code") or "").casefold() != window.completed_code.casefold():
        raise ValueError("canonical acquisition manifest set_code mismatch")
    expected_name = str(expected_completed.get("name") or window.completed_name).strip()
    manifest_name = str(scope.get("set_name") or "").strip()
    if manifest_name and manifest_name.casefold() != expected_name.casefold():
        raise ValueError("canonical acquisition manifest set_name mismatch")
    if scope.get("start") != window.start or scope.get("end") != window.end:
        raise ValueError("canonical acquisition manifest release window mismatch")
    if str(scope.get("catalog_version") or "") != window.catalog_version:
        raise ValueError("canonical acquisition manifest catalog_version mismatch")

    selection = payload.get("selection") or {}
    failures = list(selection.get("failures") or [])
    if failures:
        raise ValueError("canonical acquisition manifest contains selection failures")
    tournament_ids = list(selection.get("tournament_ids") or [])
    if int(selection.get("included_count") or 0) != len(tournament_ids):
        raise ValueError("canonical acquisition manifest tournament count mismatch")
    if not list((payload.get("raw") or {}).get("snapshot_refs") or []):
        raise ValueError("canonical acquisition manifest has no frozen raw refs")
    return payload


def validate_exact_offline_replay(
    live_manifest_path: str | Path,
    replay: ReplayEvidence,
) -> dict[str, Any]:
    live = _read_json(Path(live_manifest_path))
    offline = _read_json(Path(replay.manifest_path))
    diagnostics = _read_json(Path(replay.diagnostics_path))

    if offline.get("source") != CANONICAL_SOURCE:
        raise ValueError("offline replay source mismatch")
    if offline.get("scope") != live.get("scope"):
        raise ValueError("offline replay scope differs from canonical LIVE")
    if offline.get("selection") != live.get("selection"):
        raise ValueError("offline replay selection differs from canonical LIVE")
    if offline.get("raw") != live.get("raw"):
        raise ValueError("offline replay raw refs differ from canonical LIVE")
    if (offline.get("normalized") or {}).get("hashes") != (
        live.get("normalized") or {}
    ).get("hashes"):
        raise ValueError("offline replay normalized hashes differ from canonical LIVE")
    if offline.get("contracts") != live.get("contracts"):
        raise ValueError("offline replay contract hashes differ from canonical LIVE")
    if (offline.get("aggregation") or {}).get("comparable_matches") != (
        live.get("aggregation") or {}
    ).get("comparable_matches"):
        raise ValueError("offline replay comparable-match count differs from canonical LIVE")
    if diagnostics.get("execution_mode") != "offline":
        raise ValueError("replay diagnostics must report execution_mode=offline")
    if diagnostics.get("replay_run_id") != live.get("run_id"):
        raise ValueError("replay diagnostics do not reference canonical LIVE run")
    if int(diagnostics.get("network_calls", -1)) != 0:
        raise ValueError("exact OFFLINE replay must use zero network calls")
    return offline


def _snapshot_publication_files(
    *,
    readme_path: Path,
    state_path: Path,
    target_dir: Path,
) -> dict[Path, bytes | None]:
    paths = [
        readme_path,
        state_path,
        target_dir / "ranking.csv",
        target_dir / "heatmap.png",
        target_dir / "manifest.json",
    ]
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def _restore_publication_snapshot(snapshot: Mapping[Path, bytes | None]) -> None:
    for path, payload in snapshot.items():
        if payload is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def publish_bundle_rollback_safe(
    *,
    bundle_dir: Path,
    plan_path: Path,
    readme_path: Path,
    state_path: Path,
    target_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return publish_bundle(
            bundle_dir=bundle_dir,
            plan_path=plan_path,
            readme_path=readme_path,
            state_path=state_path,
            target_dir=target_dir,
            dry_run=True,
        )

    snapshot = _snapshot_publication_files(
        readme_path=readme_path,
        state_path=state_path,
        target_dir=target_dir,
    )
    try:
        return publish_bundle(
            bundle_dir=bundle_dir,
            plan_path=plan_path,
            readme_path=readme_path,
            state_path=state_path,
            target_dir=target_dir,
            dry_run=False,
        )
    except Exception:
        _restore_publication_snapshot(snapshot)
        raise


def _try_restore_prior(
    hook: Callable[[Mapping[str, Any], ReleaseWindow], Any] | None,
    plan: Mapping[str, Any],
    window: ReleaseWindow,
) -> tuple[bool, str | None]:
    if hook is None:
        return False, None
    try:
        hook(plan, window)
    except FileNotFoundError:
        # Missing historical evidence permits a fresh canonical LIVE.
        return False, "FileNotFoundError"
    return True, None


class RolloverLock:
    """Small local duplicate-run guard; workflow concurrency remains a separate gate."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> "RolloverLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise RuntimeError("completed-meta rollover is already running") from exc
        os.write(self._fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)


def run_rollover_transaction(
    *,
    entries: Sequence[CatalogEntry],
    state: Mapping[str, Any] | None,
    release_catalog_path: str | Path,
    hooks: RolloverHooks,
    work_dir: str | Path,
    readme_path: str | Path,
    state_path: str | Path,
    target_dir: str | Path,
    generated_at: str | None = None,
    dry_run: bool = False,
) -> RolloverResult:
    plan = build_publication_plan(
        entries,
        state,
        generated_at=generated_at,
    )
    if plan["action"] == "noop":
        return RolloverResult(
            plan=plan,
            published=False,
            run_id=None,
            restored_prior_raw=False,
            restore_failure_type=None,
        )

    window = derive_release_window(plan, release_catalog_path)
    restored, restore_failure = _try_restore_prior(
        hooks.restore_prior_raw,
        plan,
        window,
    )

    acquisition_manifest_path = Path(hooks.acquire_live(plan, window))
    live_manifest = validate_canonical_live_manifest(
        acquisition_manifest_path,
        plan,
        window,
    )
    run_id = str(live_manifest["run_id"])

    # Canonical raw persistence is mandatory before OFFLINE replay/publication.
    hooks.persist_raw(run_id, acquisition_manifest_path)

    replay = hooks.replay_offline(run_id, plan, window)
    validate_exact_offline_replay(acquisition_manifest_path, replay)

    bundle_dir = Path(
        hooks.produce_bundle(plan, replay.source_run, acquisition_manifest_path)
    )
    validate_bundle(bundle_dir, plan)

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    plan_path = work / "latest-meta-plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    publish_bundle_rollback_safe(
        bundle_dir=bundle_dir,
        plan_path=plan_path,
        readme_path=Path(readme_path),
        state_path=Path(state_path),
        target_dir=Path(target_dir),
        dry_run=dry_run,
    )
    return RolloverResult(
        plan=plan,
        published=not dry_run,
        run_id=run_id,
        restored_prior_raw=restored,
        restore_failure_type=restore_failure,
    )


__all__ = [
    "CANONICAL_SOURCE",
    "PUBLICATION_ALLOWLIST",
    "ReleaseWindow",
    "ReplayEvidence",
    "RolloverHooks",
    "RolloverLock",
    "RolloverResult",
    "derive_release_window",
    "publish_bundle_rollback_safe",
    "run_rollover_transaction",
    "validate_canonical_live_manifest",
    "validate_exact_offline_replay",
]
