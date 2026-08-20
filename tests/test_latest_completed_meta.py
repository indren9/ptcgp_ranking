from __future__ import annotations

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


def entries(*codes: str) -> list[CatalogEntry]:
    return [CatalogEntry(code=code, name=f"Set {code}") for code in codes]


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
    bundle.mkdir()
    (bundle / "fragment.md").write_text("## Latest completed meta — B3b\n", encoding="utf-8")
    (bundle / "heatmap.png").write_bytes(b"png-data")
    (bundle / "ranking.csv").write_text("rank,deck\n1,Example\n", encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "set": {"code": "B3b", "name": "Set B3b"}}),
        encoding="utf-8",
    )
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
    assert (target / "heatmap.png").read_bytes() == b"png-data"
    assert (target / "ranking.csv").read_text(encoding="utf-8") == "rank,deck\n1,Example\n"
    assert "Latest completed meta — B3b" in readme.read_text(encoding="utf-8")
    assert json.loads(state.read_text(encoding="utf-8"))["published_completed_set"]["code"] == "B3b"


def test_publish_rejects_bundle_for_a_different_set_before_writing(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "fragment.md").write_text("content", encoding="utf-8")
    (bundle / "heatmap.png").write_bytes(b"png-data")
    (bundle / "ranking.csv").write_text("rank,deck\n", encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"set": {"code": "B3a"}}), encoding="utf-8"
    )
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
