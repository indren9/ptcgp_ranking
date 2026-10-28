from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook
import pytest

from scripts.tcg_live_publication_lock import (
    PublicationLockedError,
    publication_lock,
    publication_lock_path,
)
from scripts.latest_completed_meta import PUBLIC_RANKING_COLUMNS
from storage.onedrive_publisher import (
    POCKET_ALLOWED_PATHS,
    TCG_PUBLIC_PATHS,
    PublicationArtifact,
    PublicationError,
    canonical_game_root,
    publish_pocket_result,
    publish_tcg_bundle,
    publish_transaction,
    sha256_file,
)
from storage.paths import ProjectPaths
from storage.routing import ExpansionRef


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def _write_ranking(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PUBLIC_RANKING_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "Rank": "1",
                "Deck": "Test Deck",
                "Score_%": "70",
                "MAS_%": "70",
                "LB_%": "65",
                "BT_%": "75",
                "SE_%": "1",
                "N_eff": "100",
                "Opp_used": "1",
                "Opp_total": "1",
                "Coverage_%": "100",
            }
        )


def _pocket_result(tmp_path: Path, *, wildcard_rows: int = 0):
    output_root = tmp_path / "local" / "outputs"
    source_run = output_root / "POCKET" / "standard" / "B4a__Team_Rockets_Ambition"
    ranking = source_run / "rankings" / "mars" / "mars_ranking_latest.csv"
    heatmap = source_run / "matrices" / "heatmaps" / "wr_heatmap_latest.png"
    report = source_run / "reports" / "mars" / "mars_matchup_report_latest.xlsx"
    manifest = source_run / "run" / "run_manifest_latest.json"
    wildcard = source_run / "diagnostics" / "wildcards" / "wildcard_candidates_latest.csv"

    _write_ranking(ranking)
    heatmap.parent.mkdir(parents=True, exist_ok=True)
    heatmap.write_bytes(PNG)
    report.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.active["A1"] = "MARS"
    workbook.save(report)
    workbook.close()
    wildcard.parent.mkdir(parents=True, exist_ok=True)
    wildcard.write_text(
        "Rank,Deck\n" + ("1,Wildcard Deck\n" if wildcard_rows else ""),
        encoding="utf-8",
    )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "config_summary": {"source": {"game": "POCKET"}},
                "source_scope": ["POCKET", "standard"],
                "set": {"code": "B4a", "name": "Team Rockets Ambition"},
                "outputs": {
                    "ranking": str(ranking),
                    "heatmap": str(heatmap),
                    "report": str(report),
                },
            }
        ),
        encoding="utf-8",
    )

    paths = ProjectPaths(
        base=tmp_path / "local",
        output_root=output_root,
        outputs=output_root / "POCKET" / "standard",
        cache=tmp_path / "local" / "cache",
        logs=tmp_path / "local" / "logs",
    )
    result = SimpleNamespace(
        cfg={"source": {"game": "POCKET"}},
        paths=paths,
        expansion=ExpansionRef("B4a", "Team Rockets Ambition"),
        outputs={
            "mars_ranking": ranking,
            "heatmap_topN_latest": heatmap,
            "report_latest": report,
            "run_manifest": manifest,
            "wildcard_candidates": wildcard,
        },
    )
    return result, source_run


def _write_tcg_bundle(tmp_path: Path):
    bundle = tmp_path / "bundle"
    ranking = bundle / "ranking.csv"
    heatmap = bundle / "heatmap.png"
    fragment = bundle / "fragment.md"
    manifest = bundle / "manifest.json"
    _write_ranking(ranking)
    heatmap.write_bytes(PNG)
    fragment.write_text("TCG latest completed meta", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "game": "PTCG",
                "format": "standard",
                "set": {"code": "CRI", "name": "Chaos Rising"},
                "snapshot": {"game": {"name": "Pokémon TCG Live"}},
                "source": {"contains_personal_data": False},
                "outputs": {
                    "ranking": {"sha256": sha256_file(ranking)},
                    "heatmap": {"sha256": sha256_file(heatmap)},
                },
            }
        ),
        encoding="utf-8",
    )
    window = {
        "code": "CRI",
        "name": "Chaos Rising",
        "start": "2026-09-01T17:00:00Z",
        "end": "2026-10-01T17:00:00Z",
        "catalog_version": "test-v1",
    }
    plan = {
        "latest_completed_window": window,
        "state_before_publish": None,
        "state_after_publish": {
            "published_completed_window": window,
            "updated_at": "2026-10-23T08:00:00Z",
        },
    }
    return bundle, plan


def test_missing_pocket_target_publishes_and_preserves_scope(tmp_path):
    result, _ = _pocket_result(tmp_path)
    publication = publish_pocket_result(result, meta_root=tmp_path / "canonical")
    target = tmp_path / "canonical" / "POCKET" / "standard" / "B4a__Team_Rockets_Ambition"

    assert publication.status == "PASS"
    assert len(publication.published) == 4
    assert (target / "rankings" / "mars" / "mars_ranking_latest.csv").is_file()
    assert not (target / "POCKET").exists()


def test_identical_target_is_idempotent(tmp_path):
    result, _ = _pocket_result(tmp_path)
    publish_pocket_result(result, meta_root=tmp_path / "canonical")
    publication = publish_pocket_result(result, meta_root=tmp_path / "canonical")

    assert publication.published == ()
    assert set(publication.skipped_identical) == set(POCKET_ALLOWED_PATHS) - {
        "diagnostics/wildcards/wildcard_candidates_latest.csv"
    }


def test_changed_valid_target_is_atomically_replaced(tmp_path):
    result, _ = _pocket_result(tmp_path)
    target = tmp_path / "canonical" / "POCKET" / "standard" / "B4a__Team_Rockets_Ambition"
    ranking = target / "rankings" / "mars" / "mars_ranking_latest.csv"
    ranking.parent.mkdir(parents=True)
    ranking.write_text("old", encoding="utf-8")

    publication = publish_pocket_result(result, meta_root=tmp_path / "canonical")

    assert "rankings/mars/mars_ranking_latest.csv" in publication.published
    assert sha256_file(ranking) == publication.source_hashes["rankings/mars/mars_ranking_latest.csv"]


def test_manifest_is_canonical_operation_last(tmp_path):
    result, _ = _pocket_result(tmp_path)
    publication = publish_pocket_result(result, meta_root=tmp_path / "canonical")
    canonical_events = [event for event in publication.events if event.startswith("CANONICAL_VERIFIED")]

    assert canonical_events[-1] == "CANONICAL_VERIFIED:run/run_manifest_latest.json"


def test_source_temp_hash_mismatch_fails_before_canonical_mutation(tmp_path, monkeypatch):
    result, _ = _pocket_result(tmp_path)

    def corrupt_copy(source, destination):
        temp = destination.parent / f".{destination.name}.corrupt.tmp"
        temp.write_bytes(b"corrupt")
        return temp

    monkeypatch.setattr("storage.onedrive_publisher._copy_source_to_temp", corrupt_copy)

    with pytest.raises(PublicationError, match="Source/temp SHA-256 mismatch"):
        publish_pocket_result(result, meta_root=tmp_path / "canonical")
    assert not (tmp_path / "canonical" / "POCKET").exists() or not any(
        (tmp_path / "canonical" / "POCKET").rglob("run_manifest_latest.json")
    )


def test_unexpected_artifact_fails_closed(tmp_path):
    source = tmp_path / "source"
    unexpected = source / "raw-live" / "secret.json"
    manifest = source / "manifest.json"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_text("{}", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(PublicationError, match="Unexpected artifact"):
        publish_transaction(
            game="POCKET",
            source_root=source,
            target_root=tmp_path / "target",
            artifacts=(
                PublicationArtifact(unexpected, "raw-live/secret.json", "manifest"),
                PublicationArtifact(manifest, "manifest.json", "manifest", True),
            ),
            allowed_paths=("manifest.json",),
            manifest_relative_path="manifest.json",
        )


def test_header_only_wildcard_is_not_published(tmp_path):
    result, _ = _pocket_result(tmp_path, wildcard_rows=0)
    publication = publish_pocket_result(result, meta_root=tmp_path / "canonical")

    assert "diagnostics/wildcards/wildcard_candidates_latest.csv" not in publication.source_hashes
    assert not any((tmp_path / "canonical").rglob("wildcard_candidates_latest.csv"))


def test_meaningful_wildcard_is_published(tmp_path):
    result, _ = _pocket_result(tmp_path, wildcard_rows=1)
    publication = publish_pocket_result(result, meta_root=tmp_path / "canonical")

    assert "diagnostics/wildcards/wildcard_candidates_latest.csv" in publication.published


def test_injected_failure_before_manifest_keeps_previous_manifest(tmp_path):
    result, source = _pocket_result(tmp_path)
    meta_root = tmp_path / "canonical"
    target = meta_root / "POCKET" / "standard" / "B4a__Team_Rockets_Ambition"
    old_manifest = target / "run" / "run_manifest_latest.json"
    old_manifest.parent.mkdir(parents=True)
    old_manifest.write_text('{"commit":"previous"}', encoding="utf-8")

    def fail_after_first_replace(stage, artifact):
        if stage == "after_canonical_replace" and not artifact.manifest:
            raise RuntimeError("injected pre-manifest failure")

    with pytest.raises(PublicationError, match="injected pre-manifest failure"):
        publish_pocket_result(result, meta_root=meta_root, failure_injector=fail_after_first_replace)

    assert old_manifest.read_text(encoding="utf-8") == '{"commit":"previous"}'
    assert all(path.is_file() for path in source.rglob("*.*"))


def test_alias_mapping_is_exact(tmp_path):
    assert canonical_game_root("POCKET", meta_root=tmp_path).name == "POCKET"
    assert canonical_game_root("PTCG", meta_root=tmp_path).name == "TCG"
    with pytest.raises(PublicationError):
        canonical_game_root("TCG", meta_root=tmp_path)


def test_tcg_bundle_uses_actual_three_file_contract(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    target = tmp_path / "canonical" / "TCG"
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
        onedrive_target=target,
    ) as guard:
        publication = publish_tcg_bundle(
            bundle_dir=bundle, plan=plan, meta_root=tmp_path / "canonical",
            _lease=guard,
        )
        guard.mark_clean()

    assert set(publication.published) == {"ranking.csv", "heatmap.png", "manifest.json"}
    assert {path.name for path in target.iterdir()} == {"ranking.csv", "heatmap.png", "manifest.json"}
    assert publication.events[-1] == "CANONICAL_VERIFIED:manifest.json"


def test_raw_cache_replay_and_state_are_never_published(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    for name in ("raw-live", "raw-replay", "cache", "state.json", "latest-report.json"):
        path = bundle / name
        if "." in name:
            path.write_text("private", encoding="utf-8")
        else:
            path.mkdir()
            (path / "private.json").write_text("private", encoding="utf-8")

    remote = tmp_path / "canonical" / "TCG"
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
        onedrive_target=remote,
    ) as guard:
        publish_tcg_bundle(
            bundle_dir=bundle, plan=plan, meta_root=tmp_path / "canonical",
            _lease=guard,
        )
        guard.mark_clean()
    published = {path.relative_to(tmp_path / "canonical" / "TCG").as_posix() for path in (tmp_path / "canonical" / "TCG").rglob("*") if path.is_file()}

    assert published == {"ranking.csv", "heatmap.png", "manifest.json"}


def test_successful_publication_never_deletes_local_sources(tmp_path):
    result, source = _pocket_result(tmp_path, wildcard_rows=1)
    before = {path.relative_to(source): sha256_file(path) for path in source.rglob("*") if path.is_file()}

    publish_pocket_result(result, meta_root=tmp_path / "canonical")
    after = {path.relative_to(source): sha256_file(path) for path in source.rglob("*") if path.is_file()}

    assert after == before


def test_f08_tcg_direct_onedrive_writer_collides_with_job_lease(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    destination_root = tmp_path / "canonical"
    remote = destination_root / "TCG"
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
        onedrive_target=remote,
    ) as guard:
        with pytest.raises(PublicationLockedError):
            publish_tcg_bundle(
                bundle_dir=bundle, plan=plan, meta_root=destination_root
            )
        assert not (remote / "manifest.json").exists()
        # The coordinator can publish under the already-held lease.
        result = publish_tcg_bundle(
            bundle_dir=bundle, plan=plan, meta_root=destination_root,
            _lease=guard,
        )
        assert result.status == "PASS"
        guard.mark_clean()
    assert not remote.with_name(remote.name + ".publication.lock").exists()


def test_f08_tcg_direct_onedrive_partial_failure_keeps_lock(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    destination_root = tmp_path / "canonical"
    remote = destination_root / "TCG"

    def interrupt(stage, artifact):
        if stage == "after_canonical_replace" and not artifact.manifest:
            raise RuntimeError("synthetic interrupted OneDrive writer")

    state = tmp_path / "state.json"
    local_target = tmp_path / "local-public"
    with pytest.raises(PublicationError, match="synthetic interrupted"):
        with publication_lock(
            state_path=state, target_dir=local_target, onedrive_target=remote
        ) as guard:
            publish_tcg_bundle(
                bundle_dir=bundle, plan=plan, meta_root=destination_root,
                failure_injector=interrupt, _lease=guard,
            )
            guard.mark_clean()
    lock = remote.with_name(remote.name + ".publication.lock")
    assert lock.exists()
    assert publication_lock_path(state).exists()
    with pytest.raises(PublicationLockedError):
        with publication_lock(
            state_path=state, target_dir=local_target, onedrive_target=remote
        ):
            pytest.fail("failed transaction was retried")


def test_f08r1_generic_tcg_writer_requires_full_lease_before_mutation(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    target = tmp_path / "canonical" / "TCG"
    artifacts = (
        PublicationArtifact(bundle / "ranking.csv", "ranking.csv", "ranking_csv"),
        PublicationArtifact(bundle / "heatmap.png", "heatmap.png", "png"),
        PublicationArtifact(bundle / "manifest.json", "manifest.json", "manifest", True),
    )
    params = dict(
        game="PTCG", source_root=bundle, target_root=target,
        artifacts=artifacts, allowed_paths=TCG_PUBLIC_PATHS,
        manifest_relative_path="manifest.json",
        publication_plan=plan,
    )
    with pytest.raises(PublicationError, match="coordinated lease"):
        publish_transaction(**params)
    assert not target.exists()

    # Even the wrong full lease is rejected, not treated as a generic bypass.
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
        onedrive_target=tmp_path / "other-remote",
    ) as wrong:
        with pytest.raises(PublicationLockedError):
            publish_transaction(**params, publication_lease=wrong)
        assert not target.exists()
        wrong.mark_clean()

    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
        onedrive_target=target,
    ) as guard:
        result = publish_transaction(**params, publication_lease=guard)
        assert result.status == "PASS"
        guard.mark_clean()
    assert (target / "manifest.json").exists()


def test_f08r1_direct_cloud_without_coordinator_never_writes(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    root = tmp_path / "canonical"
    with pytest.raises(PublicationLockedError, match="coordinated"):
        publish_tcg_bundle(bundle_dir=bundle, plan=plan, meta_root=root)
    assert not (root / "TCG" / "manifest.json").exists()


def test_f08r2_cloud_bundle_b_and_local_bundle_a_fail_under_same_plan(tmp_path):
    """Identical plan and lease must never permit different public bytes."""
    from scripts.tcg_live_publication import publish_tcg_live_bundle

    cloud_bundle, plan = _write_tcg_bundle(tmp_path / "cloud")
    local_bundle, _ = _write_tcg_bundle(tmp_path / "local")
    ranking = cloud_bundle / "ranking.csv"
    ranking.write_bytes(ranking.read_bytes().replace(b"Test Deck", b"Cloud Deck"))
    manifest = json.loads((cloud_bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs"]["ranking"]["sha256"] = sha256_file(ranking)
    (cloud_bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    root = tmp_path / "canonical"
    remote = root / "TCG"
    state = tmp_path / "state.json"
    local_target = tmp_path / "public-local"
    with publication_lock(
        state_path=state, target_dir=local_target, onedrive_target=remote
    ) as guard:
        result = publish_tcg_bundle(
            bundle_dir=cloud_bundle, plan=plan, meta_root=root, _lease=guard
        )
        assert result.status == "PASS"
        with pytest.raises(PublicationLockedError, match="identical public bundle"):
            publish_tcg_live_bundle(
                bundle_dir=local_bundle, plan=plan, state_path=state,
                target_dir=local_target, _lease=guard,
            )
    # Cloud may have changed, so transaction locks persist for manual recovery.
    assert (remote / "manifest.json").is_file()
    assert not state.exists()
    assert not local_target.exists()
    assert publication_lock_path(state).exists()


def test_f08r2_generic_tcg_bundle_manifest_release_mismatch_is_rejected(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["set"]["name"] = "Another release with same code"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "canonical" / "TCG"
    artifacts = (
        PublicationArtifact(bundle / "ranking.csv", "ranking.csv", "ranking_csv"),
        PublicationArtifact(bundle / "heatmap.png", "heatmap.png", "png"),
        PublicationArtifact(bundle / "manifest.json", "manifest.json", "manifest", True),
    )
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-target",
        onedrive_target=target,
    ) as guard:
        with pytest.raises(PublicationError, match="snapshot validation"):
            publish_transaction(
                game="PTCG", source_root=bundle, target_root=target,
                artifacts=artifacts, allowed_paths=TCG_PUBLIC_PATHS,
                manifest_relative_path="manifest.json",
                publication_lease=guard, publication_plan=plan,
            )
        assert not target.exists()
        guard.mark_clean()


def test_f08r2_pocket_game_cannot_publish_into_tcg_destination(tmp_path):
    result, source_root = _pocket_result(tmp_path)
    target = tmp_path / "canonical" / "TCG"
    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        publish_transaction(
            game="POCKET", source_root=source_root,
            target_root=target,
            artifacts=(
                PublicationArtifact(
                    result.outputs["mars_ranking"],
                    "rankings/mars/mars_ranking_latest.csv", "ranking_csv",
                ),
                PublicationArtifact(
                    result.outputs["run_manifest"],
                    "run/run_manifest_latest.json", "manifest", True,
                ),
            ),
            allowed_paths=POCKET_ALLOWED_PATHS,
            manifest_relative_path="run/run_manifest_latest.json",
        )
    assert not target.exists()


def test_f08r2_lease_rejects_plan_with_wrong_published_window(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    wrong = json.loads(json.dumps(plan))
    wrong["state_after_publish"]["published_completed_window"]["code"] = "PBL"
    remote = tmp_path / "canonical" / "TCG"
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "public",
        onedrive_target=remote,
    ) as guard:
        with pytest.raises(ValueError, match="state window does not match"):
            publish_tcg_bundle(
                bundle_dir=bundle, plan=wrong,
                meta_root=tmp_path / "canonical", _lease=guard,
            )
        assert not remote.exists()
        guard.mark_clean()


def test_f08r3_pocket_relative_path_into_tcg_is_denied(tmp_path):
    result, source_root = _pocket_result(tmp_path)
    target = tmp_path / "canonical" / "Meta"
    bad = "TCG/ranking.csv"
    manifest = "TCG/manifest.json"
    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        publish_transaction(
            game="POCKET", source_root=source_root, target_root=target,
            artifacts=(
                PublicationArtifact(result.outputs["mars_ranking"], bad, "ranking_csv"),
                PublicationArtifact(result.outputs["run_manifest"], manifest, "manifest", True),
            ),
            allowed_paths=(bad, manifest),
            manifest_relative_path=manifest,
        )
    assert not (target / bad).exists()
    assert not (target / manifest).exists()


def test_f08r3_tcg_artifacts_from_nested_subdir_not_validated_root(tmp_path):
    import shutil
    root, plan = _write_tcg_bundle(tmp_path)
    nested = root / "other-artifacts"
    nested.mkdir()
    for name in ("ranking.csv", "heatmap.png", "manifest.json"):
        shutil.copy2(root / name, nested / name)
    target = tmp_path / "Meta" / "TCG"
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
        onedrive_target=target,
    ) as guard:
        with pytest.raises(PublicationError, match="exact validated bundle root"):
            publish_transaction(
                game="PTCG", source_root=root, target_root=target,
                artifacts=(
                    PublicationArtifact(nested / "ranking.csv", "ranking.csv", "ranking_csv"),
                    PublicationArtifact(nested / "heatmap.png", "heatmap.png", "png"),
                    PublicationArtifact(nested / "manifest.json", "manifest.json", "manifest", True),
                ),
                allowed_paths=TCG_PUBLIC_PATHS,
                manifest_relative_path="manifest.json",
                publication_lease=guard, publication_plan=plan,
            )
        assert not target.exists()
        guard.mark_clean()


def test_f08r3_generic_tcg_uses_validated_immutable_bytes_when_source_changes(
    tmp_path, monkeypatch
):
    import storage.onedrive_publisher as publisher
    root, plan = _write_tcg_bundle(tmp_path)
    original = (root / "ranking.csv").read_bytes()
    original_manifest = (root / "manifest.json").read_bytes()
    real_freeze = publisher.validated_tcg_snapshot

    def mutate_after_freeze(bundle_dir, checked_plan):
        frozen = real_freeze(bundle_dir, checked_plan)
        (bundle_dir / "ranking.csv").write_bytes(b"post-validation-changed")
        (bundle_dir / "manifest.json").write_bytes(b'{"game":"POCKET"}')
        return frozen

    monkeypatch.setattr(publisher, "validated_tcg_snapshot", mutate_after_freeze)
    target = tmp_path / "Meta" / "TCG"
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
        onedrive_target=target,
    ) as guard:
        response = publish_tcg_bundle(
            bundle_dir=root, plan=plan, meta_root=tmp_path / "Meta", _lease=guard
        )
        assert response.status == "PASS"
        guard.mark_clean()
    assert (target / "ranking.csv").read_bytes() == original
    assert (target / "manifest.json").read_bytes() == original_manifest


@pytest.mark.parametrize("missing", ("code", "name", "start", "end", "catalog_version"))
def test_f08r3_rejects_incomplete_but_equal_window(tmp_path, missing):
    import copy
    _, plan = _write_tcg_bundle(tmp_path)
    truncated = copy.deepcopy(plan)
    del truncated["latest_completed_window"][missing]
    # This fixture intentionally aliases the same window in the next state;
    # the one deletion makes BOTH mappings equally incomplete.
    with publication_lock(
        state_path=tmp_path / "state.json",
        target_dir=tmp_path / "local-public",
    ) as guard:
        with pytest.raises(ValueError, match="five identity fields"):
            guard.assert_plan(truncated)
        guard.mark_clean()



def _f08r4_directory_alias(link: Path, destination: Path) -> None:
    """Use a Windows junction when needed; POSIX exercises directory symlinks."""
    import os
    import subprocess

    link.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(destination)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            pytest.skip(f"Windows junction unavailable: {result.stderr.strip()}")
        assert link.is_junction()
    else:
        try:
            link.symlink_to(destination, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"POSIX directory symlink unavailable: {exc}")
        assert link.is_symlink()


def _f08r4_pocket_attempt(result, source_root, target, ranking, manifest):
    return publish_transaction(
        game="POCKET", source_root=source_root, target_root=target,
        artifacts=(
            PublicationArtifact(result.outputs["mars_ranking"], ranking, "ranking_csv"),
            PublicationArtifact(result.outputs["run_manifest"], manifest, "manifest", True),
        ),
        allowed_paths=(ranking, manifest), manifest_relative_path=manifest,
    )


def test_f08r4_pocket_denies_lexical_tcg_junction_to_shared(tmp_path):
    result, source = _pocket_result(tmp_path)
    meta = tmp_path / "Meta"
    shared = meta / "shared"
    (shared / "manifest.json").parent.mkdir(parents=True)
    (shared / "manifest.json").write_bytes(b"existing-shared")
    _f08r4_directory_alias(meta / "TCG", shared)

    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        _f08r4_pocket_attempt(
            result, source, meta / "TCG", "ranking.csv", "manifest.json",
        )
    assert (shared / "manifest.json").read_bytes() == b"existing-shared"
    assert not (shared / "ranking.csv").exists()


def test_f08r4_pocket_denies_physically_resolved_tcg_alias(tmp_path):
    result, source = _pocket_result(tmp_path)
    meta = tmp_path / "Meta"
    protected = meta / "TCG"
    _f08r4_directory_alias(meta / "shared", protected)

    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        _f08r4_pocket_attempt(
            result, source, meta / "shared", "ranking.csv", "manifest.json",
        )
    assert not (protected / "ranking.csv").exists()
    assert not (protected / "manifest.json").exists()


@pytest.mark.parametrize("linked_relative", ["ranking", "manifest"])
def test_f08r4_pocket_denies_linked_effective_artifact_or_manifest(
    tmp_path, linked_relative
):
    result, source = _pocket_result(tmp_path)
    meta = tmp_path / "Meta"
    target = meta / "POCKET"
    protected = meta / "TCG"
    directory = "rankings" if linked_relative == "ranking" else "run"
    _f08r4_directory_alias(target / directory, protected)
    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        _f08r4_pocket_attempt(
            result, source, target, "rankings/ranking.csv", "run/manifest.json",
        )
    assert not (protected / "ranking.csv").exists()
    assert not (protected / "manifest.json").exists()
    assert not (target / "rankings" / "ranking.csv").exists()
    assert not (target / "run" / "manifest.json").exists()


def test_f08r4_legitimate_pocket_under_unrelated_tcg_ancestor(tmp_path):
    result, _ = _pocket_result(tmp_path)
    root = tmp_path / "TCG" / "unrelated" / "Meta"
    published = publish_pocket_result(result, meta_root=root)
    target = root / "POCKET" / "standard" / "B4a__Team_Rockets_Ambition"
    assert published.status == "PASS"
    assert (target / "rankings/mars/mars_ranking_latest.csv").is_file()
    assert (target / "run/run_manifest_latest.json").is_file()


def test_f08r4_authorized_tcg_rejects_aliased_tcg_root_even_with_lease(tmp_path):
    bundle, plan = _write_tcg_bundle(tmp_path)
    meta = tmp_path / "Meta"
    physical = meta / "shared" / "TCG"
    _f08r4_directory_alias(meta / "TCG", physical)
    state = tmp_path / "state.json"
    with publication_lock(
        state_path=state, target_dir=tmp_path / "local-public",
        onedrive_target=meta / "TCG",
    ) as guard:
        with pytest.raises(PublicationError, match="unaliased canonical TCG"):
            publish_tcg_bundle(
                bundle_dir=bundle, plan=plan, meta_root=meta, _lease=guard,
            )
        guard.mark_clean()
    assert list(physical.iterdir()) == []
    assert not state.exists()



def test_f08r4_pocket_rejects_shared_physical_destination_of_tcg_junction(tmp_path):
    result, source = _pocket_result(tmp_path)
    meta = tmp_path / "Meta"
    shared = meta / "shared"
    _f08r4_directory_alias(meta / "TCG", shared)
    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        _f08r4_pocket_attempt(
            result, source, shared, "ranking.csv", "manifest.json",
        )
    assert not (shared / "ranking.csv").exists()
    assert not (shared / "manifest.json").exists()


def test_f08r4_pocket_allows_unaliased_meta_shared(tmp_path):
    result, source = _pocket_result(tmp_path)
    meta = tmp_path / "Meta"
    (meta / "TCG").mkdir(parents=True)
    shared = meta / "shared"
    response = _f08r4_pocket_attempt(
        result, source, shared, "ranking.csv", "manifest.json",
    )
    assert response.status == "PASS"
    assert (shared / "ranking.csv").is_file()
    assert (shared / "manifest.json").is_file()
    assert list((meta / "TCG").iterdir()) == []


@pytest.mark.parametrize("nested_absent", (False, True))
def test_f08r6_pocket_rejects_double_alias_of_physical_meta_tcg(tmp_path, nested_absent):
    import hashlib

    result, source = _pocket_result(tmp_path)
    vault = tmp_path / "vault"
    meta = vault / "Meta"
    shared = meta / "shared"
    shared.mkdir(parents=True)
    existing = shared / "manifest.json"
    existing.write_bytes(b"protected-existing")
    _f08r4_directory_alias(meta / "TCG", shared)
    _f08r4_directory_alias(vault / "bridge", meta)
    target = vault / "bridge" / "shared"
    if nested_absent:
        target = target / "future" / "nested"
    relative_ranking = "ranking.csv"
    relative_manifest = "manifest.json"
    before = hashlib.sha256(existing.read_bytes()).hexdigest()
    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        _f08r4_pocket_attempt(
            result, source, target, relative_ranking, relative_manifest,
        )
    assert hashlib.sha256(existing.read_bytes()).hexdigest() == before
    assert not (shared / "ranking.csv").exists()
    assert not (shared / "future").exists()


def test_f08r6_pocket_denies_physical_meta_ancestor_when_lexical_is_unrelated(tmp_path):
    result, source = _pocket_result(tmp_path)
    meta = tmp_path / "vault" / "Meta"
    shared = meta / "shared"
    _f08r4_directory_alias(meta / "TCG", shared)
    bridge = tmp_path / "vault" / "bridge"
    _f08r4_directory_alias(bridge, meta)
    # The declared target contains no Meta component, but resolves under Meta.
    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        _f08r4_pocket_attempt(
            result, source, bridge / "shared", "ranking.csv", "manifest.json",
        )
    assert list(shared.iterdir()) == []


def test_f08r6_unrelated_tcg_ancestor_still_allows_pocket(tmp_path):
    result, source = _pocket_result(tmp_path)
    root = tmp_path / "TCG" / "unrelated" / "Meta"
    target = root / "POCKET"
    published = _f08r4_pocket_attempt(
        result, source, target, "ranking.csv", "manifest.json",
    )
    assert published.status == "PASS"
    assert (target / "ranking.csv").is_file()
    assert (target / "manifest.json").is_file()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows directory symlink QA")
def test_f08r6_windows_real_symlink_not_just_junction(tmp_path):
    result, source = _pocket_result(tmp_path)
    meta = tmp_path / "Meta"
    meta.mkdir()
    bridge = tmp_path / "bridge"
    try:
        bridge.symlink_to(meta, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink privilege unavailable: {exc}")
    assert bridge.is_symlink() and not bridge.is_junction()
    shared = meta / "shared"
    _f08r4_directory_alias(meta / "TCG", shared)
    with pytest.raises(PublicationError, match="POCKET cannot target a TCG"):
        _f08r4_pocket_attempt(
            result, source, bridge / "shared", "ranking.csv", "manifest.json",
        )
    assert list(shared.iterdir()) == []
