from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from scripts.latest_completed_meta import (
    CatalogEntry,
    README_END,
    README_START,
    build_publication_plan,
    publish_bundle,
    replace_readme_block,
)


NOW = "2026-08-20T12:00:00+00:00"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
RANKING = (
    "Rank,Deck,Score_%,MAS_%,LB_%,BT_%,SE_%,N_eff,Opp_used,Opp_total,Coverage_%\n"
    "1,Example,60.0000,55.0000,50.0000,65.0000,2.0000,100,9,9,100.0000\n"
)


def entries(*codes: str) -> list[CatalogEntry]:
    return [CatalogEntry(code=code, name=f"Set {code}") for code in codes]


def write_bundle(bundle: Path, *, code: str = "B3b") -> None:
    bundle.mkdir()
    (bundle / "fragment.md").write_text("## Latest completed meta\n", encoding="utf-8")
    (bundle / "heatmap.png").write_bytes(PNG_1X1)
    (bundle / "ranking.csv").write_text(RANKING, encoding="utf-8")
    ranking_hash = hashlib.sha256((bundle / "ranking.csv").read_bytes()).hexdigest()
    heatmap_hash = hashlib.sha256((bundle / "heatmap.png").read_bytes()).hexdigest()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "set": {"code": code, "name": f"Set {code}"},
                "source": {"contains_personal_data": False},
                "outputs": {
                    "ranking": {"sha256": ranking_hash},
                    "heatmap": {"sha256": heatmap_hash},
                },
            }
        ),
        encoding="utf-8",
    )


def test_initial_plan_selects_penultimate_catalog_entry():
    plan = build_publication_plan(entries("B3a", "B3b", "B4"), generated_at=NOW)

    assert plan["action"] == "publish"
    assert plan["reason"] == "initial_previous_set"
    assert plan["current_set"]["code"] == "B4"
    assert plan["completed_set"]["code"] == "B3b"


def test_plan_is_noop_when_same_completed_set_is_already_public():
    state = {"published_completed_set": {"code": "B3b", "name": "Set B3b"}}

    plan = build_publication_plan(entries("B3a", "B3b", "B4"), state, generated_at=NOW)

    assert plan["action"] == "noop"
    assert plan["reason"] == "completed_set_already_published"


def test_new_catalog_tail_selects_the_new_previous_set():
    state = {"published_completed_set": {"code": "B3a", "name": "Set B3a"}}

    plan = build_publication_plan(entries("B3a", "B3b", "B4"), state, generated_at=NOW)

    assert plan["action"] == "publish"
    assert plan["reason"] == "new_current_set_detected"
    assert plan["completed_set"]["code"] == "B3b"


def test_historical_insert_does_not_republish_when_tail_is_unchanged():
    state = {"published_completed_set": {"code": "B3b", "name": "Set B3b"}}

    plan = build_publication_plan(entries("A4b", "B3a", "B3b", "B4"), state, generated_at=NOW)

    assert plan["action"] == "noop"


def test_replace_readme_block_preserves_boundaries():
    original = f"before\n{README_START}\nold\n{README_END}\nafter\n"

    updated = replace_readme_block(original, "## Latest completed meta\n\nNew content")

    assert updated == (
        f"before\n{README_START}\n\n## Latest completed meta\n\nNew content\n\n{README_END}\nafter\n"
    )


def test_publish_bundle_replaces_only_latest_snapshot_and_records_state(tmp_path: Path):
    bundle = tmp_path / "bundle"
    target = tmp_path / "public" / "latest-meta"
    write_bundle(bundle)
    plan_path = tmp_path / "plan.json"
    plan = build_publication_plan(entries("B3a", "B3b", "B4"), generated_at=NOW)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(f"top\n{README_START}\n{README_END}\nbottom\n", encoding="utf-8")
    state = tmp_path / ".github" / "state.json"

    result = publish_bundle(
        bundle_dir=bundle,
        plan_path=plan_path,
        readme_path=readme,
        state_path=state,
        target_dir=target,
    )

    assert result["published_set"]["code"] == "B3b"
    assert (target / "heatmap.png").read_bytes() == PNG_1X1
    assert (target / "ranking.csv").read_text(encoding="utf-8") == RANKING
    assert "Latest completed meta" in readme.read_text(encoding="utf-8")
    assert json.loads(state.read_text(encoding="utf-8"))["published_completed_set"]["code"] == "B3b"


def test_publish_rejects_bundle_for_a_different_set_before_writing(tmp_path: Path):
    bundle = tmp_path / "bundle"
    write_bundle(bundle, code="B3a")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(build_publication_plan(entries("B3a", "B3b", "B4"), generated_at=NOW)),
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text(f"{README_START}\n{README_END}\n", encoding="utf-8")
    target = tmp_path / "public" / "latest-meta"

    with pytest.raises(ValueError, match="does not match"):
        publish_bundle(
            bundle_dir=bundle,
            plan_path=plan_path,
            readme_path=readme,
            state_path=tmp_path / "state.json",
            target_dir=target,
        )

    assert not target.exists()


def test_publish_rejects_ranking_whose_hash_does_not_match_manifest(tmp_path: Path):
    bundle = tmp_path / "bundle"
    write_bundle(bundle)
    (bundle / "ranking.csv").write_text(RANKING.replace("Example", "Changed"), encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(build_publication_plan(entries("B3a", "B3b", "B4"), generated_at=NOW)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash does not match"):
        publish_bundle(
            bundle_dir=bundle,
            plan_path=plan_path,
            readme_path=tmp_path / "README.md",
            state_path=tmp_path / "state.json",
            target_dir=tmp_path / "public" / "latest-meta",
            dry_run=True,
        )
