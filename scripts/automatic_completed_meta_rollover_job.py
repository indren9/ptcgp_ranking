from __future__ import annotations

import argparse
import copy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable

import yaml

from pipelines.deck_ranking import run_deck_ranking
from pipelines.limitless_api_acquisition import run_limitless_api_acquisition
from scripts.automatic_completed_meta_rollover import (
    ReplayEvidence,
    RolloverHooks,
    RolloverLock,
    run_rollover_transaction,
)
from scripts.latest_completed_meta import (
    build_publication_plan,
    read_catalog,
    read_json,
)
from scripts.latest_completed_meta_producer import build_bundle
from sources.limitless.tournament_api.object_store import (
    S3ObjectStoreBackend,
    persist_canonical_raw_run,
    restore_canonical_raw_run,
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


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _published_api_run_id(public_manifest: Path) -> str | None:
    if not public_manifest.exists():
        return None
    payload = read_json(public_manifest, missing={}) or {}
    source = payload.get("source") or {}
    tournament_api = source.get("tournament_api") or {}
    run_id = str(tournament_api.get("run_id") or "").strip()
    return run_id or None


def _write_production_config(
    *,
    repo_root: Path,
    work_root: Path,
    raw_root: Path,
    release_catalog: Path,
    replay_run_id: str,
    completed_code: str,
) -> Path:
    source_path = repo_root / "config" / "pocket.yaml"
    cfg = copy.deepcopy(
        yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    )

    source = cfg.setdefault("source", {})
    source["provider"] = "limitless"
    source["game"] = "POCKET"
    source["acquisition"] = "tournament_api"
    source["format"] = {"mode": "code", "code": "standard"}
    source["tournament_api"] = {
        "execution_mode": "offline",
        "replay_run_id": replay_run_id,
        "raw_store_root": str(raw_root),
        "cache_root": str(work_root / "cache-offline"),
        "release_catalog": str(release_catalog),
        "cache_ttl_min": 0,
        "reuse_latest_raw": True,
    }

    cfg.setdefault("scraping", {})["set"] = {
        "mode": "code",
        "code": completed_code,
    }

    saving = cfg.setdefault("saving", {})
    saving["output_profile"] = "debug"
    saving["include_time_when_changed"] = False
    saving["filename_prefix_with_set"] = False

    cfg.setdefault("paths", {})["output_dir"] = str(
        work_root / "source-output"
    )

    out = work_root / "production_pocket.yaml"
    out.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return out


def run_job(
    *,
    repo_root: Path,
    work_root: Path,
    generated_at: str | None = None,
    backend_factory: Callable[[], Any] = S3ObjectStoreBackend.from_env,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    work_root = work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    catalog_path = repo_root / "public" / "expansions_pocket_standard.csv"
    state_path = (
        repo_root / ".github" / "latest-completed-meta-state.json"
    )
    release_catalog = repo_root / "data" / "reference" / "pocket_releases.json"
    public_manifest = repo_root / "public" / "latest-meta" / "manifest.json"

    entries = read_catalog(catalog_path)
    state = read_json(state_path, missing=None)
    timestamp = generated_at or _utc_now_iso()

    preview = build_publication_plan(
        entries,
        state,
        generated_at=timestamp,
    )
    if preview["action"] == "noop":
        return {
            "action": "noop",
            "reason": preview["reason"],
            "current_set": preview["current_set"],
            "completed_set": preview["completed_set"],
            "published": False,
        }

    backend = backend_factory()
    live_raw_root = work_root / "raw-live"
    replay_raw_root = work_root / "raw-replay"
    revision = _git_revision(repo_root)
    evidence: dict[str, Any] = {}

    def restore_prior_raw(plan, window):
        prior_run_id = _published_api_run_id(public_manifest)
        if not prior_run_id:
            return None
        return restore_canonical_raw_run(
            live_raw_root,
            prior_run_id,
            backend,
        )

    def acquire_live(plan, window) -> Path:
        result = run_limitless_api_acquisition(
            game="POCKET",
            format="STANDARD",
            set_mode="code",
            set_code=window.completed_code,
            acquisition_started_at=datetime.now(UTC),
            execution_mode="live",
            raw_store_root=live_raw_root,
            release_catalog=release_catalog,
            cache_root=work_root / "cache-live",
            cache_ttl_min=0,
            run_id=None,
            software_git_revision=revision,
            reuse_latest_raw=True,
        )
        evidence["live_run_id"] = result.manifest.run_id
        return (
            live_raw_root
            / "runs"
            / result.manifest.run_id
            / "manifest.json"
        )

    def persist_raw(run_id: str, manifest_path: Path):
        result = persist_canonical_raw_run(
            live_raw_root,
            run_id,
            backend,
        )
        evidence["object_manifest_key"] = result.manifest_key
        return result

    def replay_offline(run_id: str, plan, window) -> ReplayEvidence:
        if replay_raw_root.exists():
            shutil.rmtree(replay_raw_root)
        restore_canonical_raw_run(
            replay_raw_root,
            run_id,
            backend,
        )

        config_path = _write_production_config(
            repo_root=repo_root,
            work_root=work_root,
            raw_root=replay_raw_root,
            release_catalog=release_catalog,
            replay_run_id=run_id,
            completed_code=window.completed_code,
        )
        evidence["config_path"] = config_path

        production = run_deck_ranking(
            base_dir=repo_root,
            config_path=config_path,
            output_dir=work_root / "source-output",
            run_scrape=True,
            run_core=True,
            run_mars=True,
            run_heatmap=False,
            run_report=False,
            configure_logs=True,
            show_progress=False,
        )
        evidence["production"] = production

        offline_run_id = str(
            production.diagnostics["tournament_api_run_id"]
        )
        manifest_path = (
            replay_raw_root / "runs" / offline_run_id / "manifest.json"
        )
        diagnostics_path = (
            replay_raw_root / "runs" / offline_run_id / "diagnostics.json"
        )
        source_run = base_for_expansion(
            production.paths.outputs,
            production.expansion,
        )
        return ReplayEvidence(
            source_run=source_run,
            manifest_path=manifest_path,
            diagnostics_path=diagnostics_path,
        )

    def produce_bundle(
        plan,
        source_run: Path,
        acquisition_manifest: Path,
    ) -> Path:
        bundle_dir = work_root / "bundle"
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)
        completed = plan["completed_set"]
        build_bundle(
            source_run=source_run,
            config_path=evidence["config_path"],
            bundle_dir=bundle_dir,
            set_code=completed["code"],
            set_name=completed["name"],
            acquired_on=datetime.now(UTC).date().isoformat(),
            acquisition_manifest=acquisition_manifest,
            source_revision=revision,
            generated_at=timestamp,
        )
        return bundle_dir

    hooks = RolloverHooks(
        acquire_live=acquire_live,
        persist_raw=persist_raw,
        replay_offline=replay_offline,
        produce_bundle=produce_bundle,
        restore_prior_raw=restore_prior_raw,
    )

    with RolloverLock(work_root / "rollover.lock"):
        result = run_rollover_transaction(
            entries=entries,
            state=state,
            release_catalog_path=release_catalog,
            hooks=hooks,
            work_dir=work_root / "transaction",
            readme_path=repo_root / "README.md",
            state_path=state_path,
            target_dir=repo_root / "public" / "latest-meta",
            generated_at=timestamp,
            dry_run=False,
        )

    production = evidence["production"]
    report = {
        "action": result.plan["action"],
        "reason": result.plan["reason"],
        "current_set": result.plan["current_set"],
        "completed_set": result.plan["completed_set"],
        "published": result.published,
        "canonical_live_run_id": result.run_id,
        "restored_prior_raw": result.restored_prior_raw,
        "restore_failure_type": result.restore_failure_type,
        "object_manifest_key": evidence.get("object_manifest_key"),
        "offline_network_calls": production.diagnostics.get(
            "tournament_api_network_calls"
        ),
        "mars_rows": production.diagnostics.get("mars_rows"),
    }
    if report["offline_network_calls"] != 0:
        raise RuntimeError(
            "production completed-meta replay did not remain zero-network"
        )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "GitHub Actions production adapter for automatic "
            "Latest Completed Meta rollover."
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
        "--report",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--allow-public-write",
        action="store_true",
        help="Required safety acknowledgement for publication paths.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.allow_public_write:
        raise SystemExit(
            "--allow-public-write is required for the production rollover job"
        )

    repo_root = args.repo_root.resolve()
    original_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        report = run_job(
            repo_root=repo_root,
            work_root=args.work_root,
        )
    finally:
        os.chdir(original_cwd)

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
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
