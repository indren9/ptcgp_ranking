from __future__ import annotations

import argparse
import copy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

import yaml

from pipelines.deck_ranking import run_deck_ranking
from pipelines.limitless_api_acquisition import run_limitless_api_acquisition
from scripts.automatic_completed_meta_rollover import (
    ReplayEvidence,
    RolloverHooks,
    RolloverLock,
    run_rollover_transaction,
)
from scripts.latest_completed_meta import CatalogEntry, README_END, README_START
from scripts.latest_completed_meta_producer import build_bundle
from sources.limitless.tournament_api.object_store import (
    LocalObjectStoreBackend,
    persist_canonical_raw_run,
    restore_canonical_raw_run,
)
from storage.routing import base_for_expansion


SHADOW_COMPLETED = CatalogEntry(code="Z98", name="D2B Shadow Previous")
SHADOW_CURRENT = CatalogEntry(code="Z99", name="D2B Shadow Current")
SHADOW_OLDER = {"code": "Z97", "name": "D2B Shadow Older"}
SHADOW_CATALOG_VERSION = "d2b4-shadow-v1"
SHADOW_STARTED = datetime(2026, 2, 2, 12, 0, tzinfo=UTC)
SHADOW_NOW = datetime(2026, 2, 2, 12, 5, tzinfo=UTC)
SHADOW_LIVE_RUN_ID = "d2b4-shadow-live"
TOURNAMENT_COUNT = 30
DECKS = tuple(
    (f"shadow-deck-{index}", f"Shadow Deck {index}")
    for index in range(1, 7)
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(paths: list[Path]) -> dict[str, str | None]:
    return {
        str(path): _sha256(path) if path.is_file() else None
        for path in paths
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _release_catalog_payload() -> dict[str, Any]:
    return {
        "catalog_version": SHADOW_CATALOG_VERSION,
        "source": "D2B local shadow fixture",
        "releases": [
            {
                "code": SHADOW_COMPLETED.code,
                "name": SHADOW_COMPLETED.name,
                "release_datetime": "2026-01-01T00:00:00Z",
                "next_release_datetime": "2026-02-01T00:00:00Z",
                "is_current": False,
                "source": "D2B local shadow fixture",
                "catalog_version": SHADOW_CATALOG_VERSION,
            },
            {
                "code": SHADOW_CURRENT.code,
                "name": SHADOW_CURRENT.name,
                "release_datetime": "2026-02-01T00:00:00Z",
                "next_release_datetime": None,
                "is_current": True,
                "source": "D2B local shadow fixture",
                "catalog_version": SHADOW_CATALOG_VERSION,
            },
        ],
    }


class ShadowTournamentApiClient:
    """Deterministic in-process Tournament API fixture; it never uses the network."""

    rate_limit_observations: tuple[Any, ...] = ()

    def __init__(self) -> None:
        base = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
        self.discovery = [
            {
                "id": f"shadow-t{index:02d}",
                "game": "POCKET",
                "format": "STANDARD",
                "name": f"D2B Shadow Tournament {index:02d}",
                "date": (base + timedelta(hours=20 * (index - 1)))
                .isoformat()
                .replace("+00:00", "Z"),
                "players": len(DECKS),
            }
            for index in range(1, TOURNAMENT_COUNT + 1)
        ]

    @staticmethod
    def _event_index(tournament_id: str) -> int:
        return int(tournament_id.rsplit("t", 1)[-1])

    def list_tournaments(self, **kwargs):
        return list(self.discovery)

    def get_tournament_details(self, tournament_id: str, **kwargs):
        item = next(row for row in self.discovery if row["id"] == tournament_id)
        return {
            "id": tournament_id,
            "game": "POCKET",
            "format": "STANDARD",
            "name": item["name"],
            "date": item["date"],
            "players": len(DECKS),
            "organizer": {"id": 9001, "name": "D2B Shadow Fixture"},
            "platform": "PTCGP",
            "decklists": True,
            "isPublic": True,
            "isOnline": True,
            "phases": [
                {
                    "phase": 1,
                    "type": "SWISS",
                    "rounds": 15,
                    "mode": "BO1",
                }
            ],
            "bannedCards": [],
            "specialRules": [],
        }

    def get_tournament_pairings(self, tournament_id: str, **kwargs):
        event_index = self._event_index(tournament_id)
        rows = []
        round_no = 0
        for left in range(len(DECKS)):
            for right in range(left + 1, len(DECKS)):
                round_no += 1
                left_player = f"shadow-player-{left + 1}"
                right_player = f"shadow-player-{right + 1}"
                upset = (event_index + left + right) % 5 == 0
                winner = right_player if upset else left_player
                rows.append(
                    {
                        "phase": 1,
                        "round": round_no,
                        "table": round_no,
                        "player1": left_player,
                        "player2": right_player,
                        "winner": winner,
                    }
                )
        return rows

    def get_tournament_standings(self, tournament_id: str, **kwargs):
        pairings = self.get_tournament_pairings(tournament_id)
        wins = {f"shadow-player-{index}": 0 for index in range(1, len(DECKS) + 1)}
        losses = dict(wins)
        for pairing in pairings:
            first = pairing["player1"]
            second = pairing["player2"]
            winner = pairing["winner"]
            loser = second if winner == first else first
            wins[winner] += 1
            losses[loser] += 1

        ordered = sorted(
            wins,
            key=lambda player: (-wins[player], losses[player], player),
        )
        placing = {player: index for index, player in enumerate(ordered, start=1)}
        rows = []
        for index, (deck_id, deck_name) in enumerate(DECKS, start=1):
            player = f"shadow-player-{index}"
            rows.append(
                {
                    "player": player,
                    "placing": placing[player],
                    "record": {
                        "wins": wins[player],
                        "losses": losses[player],
                        "ties": 0,
                    },
                    "decklist": {"cards": []},
                    "deck": {"id": deck_id, "name": deck_name},
                    "drop": None,
                }
            )
        return rows


def _write_shadow_config(
    *,
    repo_root: Path,
    shadow_root: Path,
    raw_root: Path,
    release_catalog: Path,
    replay_run_id: str,
) -> Path:
    config_path = repo_root / "config" / "pocket.yaml"
    cfg = copy.deepcopy(
        yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
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
        "cache_root": str(shadow_root / "cache"),
        "release_catalog": str(release_catalog),
        "cache_ttl_min": 0,
        "reuse_latest_raw": True,
    }

    scraping = cfg.setdefault("scraping", {})
    scraping["set"] = {"mode": "code", "code": SHADOW_COMPLETED.code}

    cfg.setdefault("top_meta", {})["threshold_pct"] = 100.0
    analysis = cfg.setdefault("analysis", {})
    analysis.setdefault("candidate_pool", {})["share_pct"] = 100.0
    analysis.setdefault("wildcard_pass", {})["enabled"] = False

    cfg["nan_filter"] = {
        "mode": "fixed",
        "max_nan_ratio": 1.0,
        "min_nan_allowed": 1,
        "use_ceil": False,
    }

    saving = cfg.setdefault("saving", {})
    saving["output_profile"] = "debug"
    saving["include_time_when_changed"] = False
    saving["filename_prefix_with_set"] = False

    cfg.setdefault("paths", {})["output_dir"] = str(
        shadow_root / "production-output"
    )

    out = shadow_root / "shadow_pocket.yaml"
    out.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return out


def _assert_public_privacy(bundle_dir: Path) -> None:
    text = "\n".join(
        (bundle_dir / name).read_text(encoding="utf-8")
        for name in ("fragment.md", "ranking.csv", "manifest.json")
    ).casefold()
    forbidden = (
        "shadow-player-",
        "authorization:",
        "secret_access_key",
        "access_key_id",
        "c:\\users\\",
    )
    found = [token for token in forbidden if token in text]
    if found:
        raise AssertionError(
            "shadow public bundle leaked private/sensitive text: "
            + ", ".join(found)
        )
    manifest = json.loads(
        (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    )
    if (manifest.get("source") or {}).get("contains_personal_data") is not False:
        raise AssertionError(
            "shadow public manifest must state contains_personal_data=false"
        )
    if (manifest.get("source") or {}).get("acquisition") != "Tournament API":
        raise AssertionError(
            "shadow public bundle must be sourced from Tournament API"
        )


def run_shadow(repo_root: Path, shadow_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    shadow_root = shadow_root.resolve()

    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    shadow_root.mkdir(parents=True)

    release_catalog = shadow_root / "pocket_releases_shadow.json"
    _write_json(release_catalog, _release_catalog_payload())

    real_public_paths = [
        repo_root / "README.md",
        repo_root / ".github" / "latest-completed-meta-state.json",
        repo_root / "public" / "latest-meta" / "ranking.csv",
        repo_root / "public" / "latest-meta" / "heatmap.png",
        repo_root / "public" / "latest-meta" / "manifest.json",
    ]
    real_before = _snapshot(real_public_paths)

    staging_root = shadow_root / "staged-repository"
    staged_readme = staging_root / "README.md"
    staged_state = (
        staging_root / ".github" / "latest-completed-meta-state.json"
    )
    staged_target = staging_root / "public" / "latest-meta"
    staged_readme.parent.mkdir(parents=True, exist_ok=True)
    staged_readme.write_text(
        "D2B-4 shadow repository\n\n"
        f"{README_START}\n"
        "old shadow latest-meta\n"
        f"{README_END}\n",
        encoding="utf-8",
    )
    _write_json(
        staged_state,
        {
            "schema_version": 1,
            "game": "POCKET",
            "format": "standard",
            "observed_current_set": SHADOW_OLDER,
            "published_completed_set": SHADOW_OLDER,
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    live_raw_root = shadow_root / "raw-live"
    replay_raw_root = shadow_root / "raw-restored"
    object_store_root = shadow_root / "private-object-store"
    backend = LocalObjectStoreBackend(object_store_root)
    evidence: dict[str, Any] = {}

    def restore_prior_raw(plan, window):
        raise FileNotFoundError("intentional D2B-4 cold-start shadow")

    def acquire_live(plan, window) -> Path:
        result = run_limitless_api_acquisition(
            game="POCKET",
            format="STANDARD",
            set_mode="code",
            set_code=window.completed_code,
            acquisition_started_at=SHADOW_STARTED,
            execution_mode="live",
            raw_store_root=live_raw_root,
            release_catalog=release_catalog,
            client=ShadowTournamentApiClient(),
            run_id=SHADOW_LIVE_RUN_ID,
            software_git_revision="D2B4-SHADOW",
            discovery_page_size=50,
            discovery_max_pages=2,
            reuse_latest_raw=False,
            now_fn=lambda: SHADOW_NOW,
        )
        evidence["live_result"] = result
        return live_raw_root / "runs" / result.manifest.run_id / "manifest.json"

    def persist_raw(run_id: str, manifest_path: Path):
        persisted = persist_canonical_raw_run(
            live_raw_root,
            run_id,
            backend,
        )
        evidence["persisted"] = persisted
        return persisted

    def replay_offline(
        run_id: str,
        plan,
        window,
    ) -> ReplayEvidence:
        restored = restore_canonical_raw_run(
            replay_raw_root,
            run_id,
            backend,
        )
        evidence["restored"] = restored

        shadow_config = _write_shadow_config(
            repo_root=repo_root,
            shadow_root=shadow_root,
            raw_root=replay_raw_root,
            release_catalog=release_catalog,
            replay_run_id=run_id,
        )
        evidence["config"] = shadow_config

        production = run_deck_ranking(
            base_dir=repo_root,
            config_path=shadow_config,
            output_dir=shadow_root / "production-output",
            run_scrape=True,
            run_core=True,
            run_mars=True,
            run_heatmap=False,
            run_report=False,
            configure_logs=False,
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
        bundle_dir = shadow_root / "bundle"
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)
        completed = plan["completed_set"]
        build_bundle(
            source_run=source_run,
            config_path=evidence["config"],
            bundle_dir=bundle_dir,
            set_code=completed["code"],
            set_name=completed["name"],
            acquired_on="2026-02-02",
            acquisition_manifest=acquisition_manifest,
            source_revision="D2B4-SHADOW",
            generated_at="2026-02-02T12:10:00+00:00",
        )
        evidence["bundle"] = bundle_dir
        return bundle_dir

    hooks = RolloverHooks(
        acquire_live=acquire_live,
        persist_raw=persist_raw,
        replay_offline=replay_offline,
        produce_bundle=produce_bundle,
        restore_prior_raw=restore_prior_raw,
    )

    initial_state = json.loads(staged_state.read_text(encoding="utf-8"))
    with RolloverLock(shadow_root / "rollover.lock"):
        result = run_rollover_transaction(
            entries=[SHADOW_COMPLETED, SHADOW_CURRENT],
            state=initial_state,
            release_catalog_path=release_catalog,
            hooks=hooks,
            work_dir=shadow_root / "work",
            readme_path=staged_readme,
            state_path=staged_state,
            target_dir=staged_target,
            generated_at="2026-02-02T12:10:00+00:00",
            dry_run=False,
        )

    if result.plan["reason"] != "new_current_set_detected":
        raise AssertionError("shadow did not detect a new current set")
    if result.plan["current_set"]["code"] != SHADOW_CURRENT.code:
        raise AssertionError("shadow current set derivation failed")
    if result.plan["completed_set"]["code"] != SHADOW_COMPLETED.code:
        raise AssertionError("shadow completed set derivation failed")
    if not result.published:
        raise AssertionError("shadow staged publication did not execute")
    if result.restore_failure_type != "FileNotFoundError":
        raise AssertionError("shadow cold-start restore behavior was not exercised")

    production = evidence["production"]
    mars_ranking = production.frames.get("mars_ranking")
    if mars_ranking is None or mars_ranking.empty:
        raise AssertionError("real MARS stage produced no ranking")
    if production.diagnostics.get("tournament_api_network_calls") != 0:
        raise AssertionError("production OFFLINE replay used network calls")

    _assert_public_privacy(evidence["bundle"])

    staged_manifest = json.loads(
        (staged_target / "manifest.json").read_text(encoding="utf-8")
    )
    if staged_manifest["set"]["code"] != SHADOW_COMPLETED.code:
        raise AssertionError("staged publication set mismatch")

    state_after = json.loads(staged_state.read_text(encoding="utf-8"))

    def should_not_run(*args, **kwargs):
        raise AssertionError("NOOP rerun invoked a rollover hook")

    noop_hooks = RolloverHooks(
        acquire_live=should_not_run,
        persist_raw=should_not_run,
        replay_offline=should_not_run,
        produce_bundle=should_not_run,
    )
    second = run_rollover_transaction(
        entries=[SHADOW_COMPLETED, SHADOW_CURRENT],
        state=state_after,
        release_catalog_path=release_catalog,
        hooks=noop_hooks,
        work_dir=shadow_root / "work-noop",
        readme_path=staged_readme,
        state_path=staged_state,
        target_dir=staged_target,
        generated_at="2026-02-02T12:20:00+00:00",
        dry_run=False,
    )
    if second.plan["action"] != "noop" or second.published:
        raise AssertionError("second shadow run was not idempotent")

    real_after = _snapshot(real_public_paths)
    if real_after != real_before:
        raise AssertionError(
            "D2B-4 shadow modified the real Latest Completed Meta"
        )

    live_manifest = json.loads(
        (
            live_raw_root
            / "runs"
            / SHADOW_LIVE_RUN_ID
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    offline_run_id = str(
        production.diagnostics["tournament_api_run_id"]
    )
    offline_diagnostics = json.loads(
        (
            replay_raw_root
            / "runs"
            / offline_run_id
            / "diagnostics.json"
        ).read_text(encoding="utf-8")
    )

    report = {
        "gate": "D2B-4",
        "verdict": "PASS",
        "simulated_transition": {
            "old_published_completed": SHADOW_OLDER["code"],
            "new_current": SHADOW_CURRENT.code,
            "new_completed": SHADOW_COMPLETED.code,
        },
        "plan_reason": result.plan["reason"],
        "canonical_source": live_manifest["source"],
        "release_window": {
            "start": live_manifest["scope"]["start"],
            "end": live_manifest["scope"]["end"],
            "catalog_version": live_manifest["scope"]["catalog_version"],
        },
        "live_run_id": SHADOW_LIVE_RUN_ID,
        "selected_tournaments": live_manifest["selection"]["included_count"],
        "participants": live_manifest["normalized"]["row_counts"]["participants"],
        "pairings": live_manifest["normalized"]["row_counts"]["pairings"],
        "comparable_matches": live_manifest["aggregation"]["comparable_matches"],
        "private_object_store": {
            "backend": "LocalObjectStoreBackend",
            "manifest_key": evidence["persisted"].manifest_key,
            "file_count": evidence["persisted"].file_count,
            "restored_for_replay": True,
        },
        "offline_replay": {
            "run_id": offline_run_id,
            "network_calls": offline_diagnostics["network_calls"],
        },
        "core_mars": {
            "mars_rows": int(len(mars_ranking)),
            "top_deck_id": str(mars_ranking.iloc[0]["Deck"]),
            "top_score_pct": float(mars_ranking.iloc[0]["Score_%"]),
        },
        "producer": {
            "bundle_validated": True,
            "contains_personal_data": False,
            "public_acquisition": staged_manifest["source"]["acquisition"],
        },
        "publication": {
            "target": str(staged_target),
            "shadow_only": True,
            "real_latest_meta_unchanged": True,
        },
        "cold_start_restore_failure": result.restore_failure_type,
        "second_run": {
            "action": second.plan["action"],
            "idempotent": True,
        },
    }
    _write_json(shadow_root / "shadow_report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the D2B-4 local completed-meta rollover shadow. "
            "All writes stay below outputs/d2b4-shadow by default."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("outputs/d2b4-shadow"),
        help="Dedicated shadow root. It is deleted/recreated at the start.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    original_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        report = run_shadow(repo_root, args.root)
    finally:
        os.chdir(original_cwd)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
