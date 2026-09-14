from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook
import pytest

from scripts.latest_completed_meta import PUBLIC_RANKING_COLUMNS
from storage.onedrive_publisher import (
    POCKET_ALLOWED_PATHS,
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
    plan = {"latest_completed_window": {"code": "CRI", "name": "Chaos Rising"}}
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
            game="PTCG",
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
    publication = publish_tcg_bundle(bundle_dir=bundle, plan=plan, meta_root=tmp_path / "canonical")
    target = tmp_path / "canonical" / "TCG"

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

    publish_tcg_bundle(bundle_dir=bundle, plan=plan, meta_root=tmp_path / "canonical")
    published = {path.relative_to(tmp_path / "canonical" / "TCG").as_posix() for path in (tmp_path / "canonical" / "TCG").rglob("*") if path.is_file()}

    assert published == {"ranking.csv", "heatmap.png", "manifest.json"}


def test_successful_publication_never_deletes_local_sources(tmp_path):
    result, source = _pocket_result(tmp_path, wildcard_rows=1)
    before = {path.relative_to(source): sha256_file(path) for path in source.rglob("*") if path.is_file()}

    publish_pocket_result(result, meta_root=tmp_path / "canonical")
    after = {path.relative_to(source): sha256_file(path) for path in source.rglob("*") if path.is_file()}

    assert after == before
