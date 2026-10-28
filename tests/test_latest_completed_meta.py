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
    main,
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


@pytest.mark.parametrize("via_cli", [False, True])
def test_f08r4_historical_publisher_denies_tcg_namespace_without_side_effects(tmp_path, via_cli):
    bundle = tmp_path / "bundle"
    write_bundle(bundle)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(build_publication_plan(entries("B3a", "B3b", "B4"), generated_at=NOW)),
        encoding="utf-8",
    )
    target = tmp_path / "public" / "tcg-live" / "latest-meta"
    target.mkdir(parents=True)
    for filename in ("heatmap.png", "ranking.csv", "manifest.json"):
        (target / filename).write_bytes(("old-" + filename).encode())
    readme = tmp_path / "README.md"
    readme.write_text(f"{README_START}\nold\n{README_END}\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text('{"old": true}\n', encoding="utf-8")
    tracked = [target / name for name in ("heatmap.png", "ranking.csv", "manifest.json")]
    tracked += [readme, state]
    before = {path: path.read_bytes() for path in tracked}

    with pytest.raises(ValueError, match="protected TCG public namespace"):
        if via_cli:
            main([
                "publish", "--bundle", str(bundle), "--plan", str(plan_path),
                "--target", str(target), "--readme", str(readme), "--state", str(state),
            ])
        else:
            publish_bundle(
                bundle_dir=bundle, plan_path=plan_path, target_dir=target,
                readme_path=readme, state_path=state,
            )

    assert {path: path.read_bytes() for path in tracked} == before
    assert sorted(p.name for p in target.iterdir()) == sorted(p.name for p in tracked[:3])


def test_f08r4_historical_publisher_denies_physical_alias_to_tcg(tmp_path):
    target = tmp_path / "public" / "tcg-live" / "latest-meta"
    target.mkdir(parents=True)
    alias = tmp_path / "other" / "pocket_target"
    alias.parent.mkdir()
    try:
        alias.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        import os
        import subprocess
        if os.name != "nt":
            pytest.skip(f"directory symlink unavailable: {exc}")
        junction = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True, text=True, check=False,
        )
        if junction.returncode:
            pytest.skip(f"Windows junction unavailable: {junction.stderr.strip()}")
        assert alias.is_junction()
    with pytest.raises(ValueError, match="protected TCG public namespace"):
        publish_bundle(
            bundle_dir=tmp_path / "unneeded", plan_path=tmp_path / "unneeded.json",
            readme_path=tmp_path / "README.md", state_path=tmp_path / "state.json",
            target_dir=alias,
        )
    assert list(target.iterdir()) == []
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("entrypoint", ("api", "cli", "rollback"))
@pytest.mark.parametrize("protected", ("state_existing", "state_missing", "readme_existing", "readme_missing", "bundle"))
def test_f08r6_legacy_all_destinations_fail_closed(tmp_path, entrypoint, protected):
    import acquisition  # Initialize the existing release-catalog import graph first.
    from scripts.automatic_completed_meta_rollover import publish_bundle_rollback_safe

    bundle = tmp_path / "bundle"
    write_bundle(bundle)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(build_publication_plan(entries("B3a", "B3b", "B4"), generated_at=NOW)),
        encoding="utf-8",
    )
    target = tmp_path / "public" / "latest-meta"
    target.mkdir(parents=True)
    for name in ("heatmap.png", "ranking.csv", "manifest.json"):
        (target / name).write_bytes(("old-" + name).encode("utf-8"))
    readme = tmp_path / "README.md"
    readme.write_text(f"{README_START}\nold\n{README_END}\n", encoding="utf-8")
    state = tmp_path / ".github" / "pocket-state.json"
    state.parent.mkdir()
    state.write_bytes(b"old-state")

    if protected.startswith("state"):
        state = tmp_path / ".github" / "tcg-live-latest-completed-meta-state.json"
    elif protected.startswith("readme"):
        readme = tmp_path / "public" / "tcg-live" / "README.md"
    else:
        target = tmp_path / "public" / "tcg-live" / "latest-meta"
    if protected.endswith("existing") or protected == "bundle":
        if protected == "bundle":
            target.mkdir(parents=True)
            for name in ("heatmap.png", "ranking.csv", "manifest.json"):
                (target / name).write_bytes(("protected-" + name).encode())
        elif protected.startswith("readme"):
            readme.parent.mkdir(parents=True)
            readme.write_text(f"{README_START}\nold\n{README_END}\n", encoding="utf-8")
        else:
            state.write_bytes(b"protected-state")

    existing = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*") if p.is_file()}
    kwargs = dict(bundle_dir=bundle, plan_path=plan_path, readme_path=readme,
                  state_path=state, target_dir=target)
    with pytest.raises(ValueError, match="protected TCG"):
        if entrypoint == "cli":
            main(["publish", "--bundle", str(bundle), "--plan", str(plan_path),
                  "--readme", str(readme), "--state", str(state), "--target", str(target)])
        elif entrypoint == "rollback":
            publish_bundle_rollback_safe(**kwargs)
        else:
            publish_bundle(**kwargs)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert after == existing


@pytest.mark.parametrize("protected", ("readme", "state"))
def test_f08r6_legacy_denies_linked_secondary_destinations(tmp_path, protected):
    import os
    import subprocess

    bundle = tmp_path / "bundle"
    write_bundle(bundle)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(build_publication_plan(entries("B3a", "B3b", "B4"), generated_at=NOW)),
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text(f"{README_START}\nold\n{README_END}\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_bytes(b"old-state")
    protected_dir = (tmp_path / "public" / "tcg-live") if protected == "readme" else (tmp_path / ".github")
    protected_dir.mkdir(parents=True)
    protected_file = protected_dir / ("README.md" if protected == "readme"
                                      else "tcg-live-latest-completed-meta-state.json")
    protected_file.write_bytes(readme.read_bytes() if protected == "readme" else b"tcg-state")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(protected_dir, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        if os.name != "nt":
            pytest.skip(f"symlink unavailable: {exc}")
        junction = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(protected_dir)],
                                  capture_output=True, text=True, check=False)
        if junction.returncode:
            pytest.skip(f"junction unavailable: {junction.stderr.strip()}")
        assert alias.is_junction()
    if protected == "readme":
        readme = alias / "README.md"
    else:
        state = alias / "tcg-live-latest-completed-meta-state.json"
    before = {p: p.read_bytes() for p in (protected_file,)}
    with pytest.raises(ValueError, match="protected TCG"):
        publish_bundle(bundle_dir=bundle, plan_path=plan_path,
                       readme_path=readme, state_path=state,
                       target_dir=tmp_path / "public" / "latest-meta")
    assert {p: p.read_bytes() for p in before} == before
    assert not (tmp_path / "public" / "latest-meta").exists()
