from __future__ import annotations

import argparse
import copy
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable

import yaml

from acquisition.scope import EligibilityPolicy, ScopePolicy
from pipelines.deck_ranking import run_deck_ranking
from pipelines.limitless_api_acquisition import (
    run_limitless_api_acquisition,
)
from scripts.automatic_completed_meta_rollover import (
    ReplayEvidence,
    validate_exact_offline_replay,
)
from scripts.tcg_live_latest_completed import (
    build_plan,
    read_json,
)
from scripts.tcg_live_publication import (
    build_tcg_live_bundle,
    publish_tcg_live_bundle,
)
from sources.limitless.tournament_api.object_store import (
    S3ObjectStoreBackend,
    persist_canonical_raw_run,
    restore_canonical_raw_run,
)
from sources.limitless.tournament_api.release_catalog import (
    parse_utc_datetime,
)
from storage.routing import base_for_expansion


def _git_revision(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(
        path.read_text(encoding="utf-8")
    ) or {}

    source = payload.get("source") or {}
    api = source.get("tournament_api") or {}

    if str(source.get("game") or "").upper() != "PTCG":
        raise ValueError("TCG Live job requires source.game=PTCG")

    if str(source.get("acquisition") or "") != "tournament_api":
        raise ValueError(
            "TCG Live job requires acquisition=tournament_api"
        )

    if not api:
        raise ValueError(
            "TCG Live Tournament API configuration is missing"
        )

    return payload


def _api_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(
        (cfg.get("source") or {})
        .get("tournament_api")
        or {}
    )


def _eligibility(api: dict[str, Any]) -> EligibilityPolicy:
    raw = dict(api.get("eligibility") or {})

    return EligibilityPolicy(
        policy_id=str(raw["policy_id"]),
        game="PTCG",
        allowed_formats=tuple(
            raw.get("allowed_formats") or ("STANDARD",)
        ),
        require_public=bool(
            raw.get("require_public", True)
        ),
        require_decklists=bool(
            raw.get("require_decklists", True)
        ),
        require_online=bool(
            raw.get("require_online", True)
        ),
        allowed_platforms=tuple(
            raw.get("allowed_platforms") or ("PTCGL",)
        ),
    )


def _scope(
    plan: dict[str, Any],
    api: dict[str, Any],
) -> ScopePolicy:
    window = plan["latest_completed_window"]

    return ScopePolicy(
        policy_id=str(api["scope_policy_id"]),
        game="PTCG",
        format="STANDARD",
        set_code=str(window["code"]),
        set_name=str(window["name"]),
        start_datetime=parse_utc_datetime(
            window["start"],
            field_name="start",
        ),
        end_datetime=parse_utc_datetime(
            window["end"],
            field_name="end",
        ),
        catalog_version=str(
            window["catalog_version"]
        ),
    )


def _write_offline_config(
    *,
    source_config: Path,
    destination: Path,
    raw_root: Path,
    cache_root: Path,
    output_root: Path,
    release_catalog: Path,
    replay_run_id: str,
    completed_code: str,
) -> Path:
    cfg = copy.deepcopy(
        _load_config(source_config)
    )

    api = cfg["source"]["tournament_api"]

    api["execution_mode"] = "offline"
    api["replay_run_id"] = replay_run_id
    api["raw_store_root"] = str(raw_root)
    api["cache_root"] = str(cache_root)
    api["cache_ttl_min"] = 0
    api["release_catalog"] = str(release_catalog)
    api["window"] = {
        "mode": "code",
        "code": completed_code,
    }

    saving = cfg.setdefault("saving", {})
    saving["output_profile"] = "debug"
    saving["include_time_when_changed"] = False
    saving["filename_prefix_with_set"] = False

    cfg.setdefault("paths", {})["output_dir"] = str(
        output_root
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.write_text(
        yaml.safe_dump(
            cfg,
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    return destination


def run_job(
    *,
    repo_root: Path,
    work_root: Path,
    state_path: Path | None = None,
    acquisition_started_at: datetime | None = None,
    backend_factory: Callable[[], Any] = (
        S3ObjectStoreBackend.from_env
    ),
    publish: bool = False,
    public_target: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    work_root = work_root.resolve()

    work_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    config_path = repo_root / "config" / "tcg.yaml"

    state_path = (
        state_path
        or repo_root
        / ".github"
        / "tcg-live-latest-completed-meta-state.json"
    )

    cfg = _load_config(config_path)
    api = _api_config(cfg)

    catalog_path = Path(
        str(api["release_catalog"])
    )

    if not catalog_path.is_absolute():
        catalog_path = repo_root / catalog_path

    started = (
        acquisition_started_at
        or datetime.now(UTC)
    )

    state = read_json(
        state_path,
        missing=None,
    )

    plan = build_plan(
        catalog_path=catalog_path,
        state=state,
        acquisition_started_at=started,
    )

    if plan["action"] == "noop":
        return {
            "action": "noop",
            "reason": plan["reason"],
            "latest_completed_window":
                plan["latest_completed_window"],
            "network_calls": 0,
            "candidate_ready": False,
            "published": False,
        }

    scope = _scope(plan, api)
    eligibility = _eligibility(api)

    live_raw_root = work_root / "raw-live"
    replay_raw_root = work_root / "raw-replay"

    revision = _git_revision(repo_root)

    live = run_limitless_api_acquisition(
        game="PTCG",
        format="STANDARD",
        set_mode="code",
        set_code=scope.set_code,
        resolved_scope=scope,
        acquisition_started_at=started,
        execution_mode="live",
        raw_store_root=live_raw_root,
        release_catalog=catalog_path,
        cache_root=work_root / "cache-live",
        cache_ttl_min=float(
            api.get("cache_ttl_min", 0)
        ),
        min_request_interval_seconds=float(
            api.get(
                "min_request_interval_seconds",
                4.0,
            )
        ),
        eligibility=eligibility,
        run_id=None,
        replay_run_id=None,
        software_git_revision=revision,
        discovery_page_size=int(
            api.get("discovery_page_size", 50)
        ),
        discovery_max_pages=int(
            api.get("discovery_max_pages", 100)
        ),
        reuse_latest_raw=bool(
            api.get("reuse_latest_raw", True)
        ),
    )

    run_id = str(live.manifest.run_id)

    live_manifest = (
        live_raw_root
        / "runs"
        / run_id
        / "manifest.json"
    )

    backend = backend_factory()

    persisted = persist_canonical_raw_run(
        live_raw_root,
        run_id,
        backend,
    )

    if replay_raw_root.exists():
        shutil.rmtree(replay_raw_root)

    restore_canonical_raw_run(
        replay_raw_root,
        run_id,
        backend,
    )

    offline_config = _write_offline_config(
        source_config=config_path,
        destination=(
            work_root / "tcg-live-offline.yaml"
        ),
        raw_root=replay_raw_root,
        cache_root=work_root / "cache-offline",
        output_root=work_root / "source-output",
        release_catalog=catalog_path,
        replay_run_id=run_id,
        completed_code=scope.set_code,
    )

    production = run_deck_ranking(
        base_dir=repo_root,
        config_path=offline_config,
        output_dir=work_root / "source-output",
        run_scrape=True,
        run_core=True,
        run_mars=True,
        run_heatmap=False,
        run_report=False,
        configure_logs=True,
        show_progress=False,
    )

    offline_run_id = str(
        production.diagnostics[
            "tournament_api_run_id"
        ]
    )

    replay = ReplayEvidence(
        source_run=base_for_expansion(
            production.paths.outputs,
            production.expansion,
        ),
        manifest_path=(
            replay_raw_root
            / "runs"
            / offline_run_id
            / "manifest.json"
        ),
        diagnostics_path=(
            replay_raw_root
            / "runs"
            / offline_run_id
            / "diagnostics.json"
        ),
    )

    validate_exact_offline_replay(
        live_manifest,
        replay,
    )

    network_calls = int(
        production.diagnostics.get(
            "tournament_api_network_calls",
            -1,
        )
    )

    if network_calls != 0:
        raise RuntimeError(
            "TCG Live OFFLINE replay used network"
        )

    bundle_dir = None
    publication_result = None

    if publish:
        bundle_dir = work_root / "bundle"

        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)

        build_tcg_live_bundle(
            source_run=replay.source_run,
            config_path=offline_config,
            acquisition_manifest=live_manifest,
            bundle_dir=bundle_dir,
            plan=plan,
            source_revision=revision,
        )

        target = (
            public_target
            or repo_root
            / "public"
            / "tcg-live"
            / "latest-meta"
        )

        publication_result = publish_tcg_live_bundle(
            bundle_dir=bundle_dir,
            plan=plan,
            state_path=state_path,
            target_dir=target,
            dry_run=False,
        )

    return {
        "action": (
            "published"
            if publish
            else "candidate_ready"
        ),
        "reason": plan["reason"],
        "latest_completed_window":
            plan["latest_completed_window"],
        "canonical_live_run_id": run_id,
        "object_manifest_key":
            persisted.manifest_key,
        "offline_network_calls":
            network_calls,
        "mars_rows":
            production.diagnostics.get(
                "mars_rows"
            ),
        "candidate_source_run":
            str(replay.source_run),
        "candidate_ready": True,
        "bundle_dir": (
            None
            if bundle_dir is None
            else str(bundle_dir)
        ),
        "publication": publication_result,
        "published": bool(publish),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "TCG Live latest-completed candidate "
            "transaction. Publication remains disabled."
        )
    )

    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
    )

    parser.add_argument(
        "--work-root",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--state",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Publish the validated TCG Live bundle "
            "and update its dedicated state."
        ),
    )

    parser.add_argument(
        "--allow-public-write",
        action="store_true",
        help=(
            "Required safety acknowledgement "
            "when --publish is used."
        ),
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.publish and not args.allow_public_write:
        raise SystemExit(
            "--allow-public-write is required "
            "when --publish is used"
        )

    report = run_job(
        repo_root=args.repo_root,
        work_root=args.work_root,
        state_path=args.state,
        publish=args.publish,
    )

    if args.report is not None:
        args.report.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.report.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
