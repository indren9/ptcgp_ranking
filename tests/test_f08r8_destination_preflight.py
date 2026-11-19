"""Offline R7 bypass reproductions: refusal must precede every mutation."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts.latest_completed_meta import main
from storage.onedrive_publisher import (
    PublicationArtifact, PublicationError, publish_transaction,
)


STATE = "tcg-live-latest-completed-meta-state.json"


def snapshot(root):
    # Includes directory entries and link identities: detects mkdir and staging
    # debris as well as byte changes, even when all final outputs are absent.
    return {
        str(p.relative_to(root)): (
            ("link", os.readlink(p)) if p.is_symlink()
            else ("file", p.read_bytes()) if p.is_file()
            else ("dir", None)
        )
        for p in root.rglob("*")
    }


def directory_alias(link, target):
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        if result.returncode:
            pytest.skip(f"Windows junction unavailable: {result.stderr}")
    else:
        link.symlink_to(target, target_is_directory=True)


def protected_destination(root, form, existing):
    github = root / ".github"
    github.mkdir()
    state = github / STATE
    if existing:
        state.write_bytes(b'{"protected":true}\n')
    if form == "direct":
        return state
    if form == "directory_alias":
        directory_alias(root / "bridge", github)
        return root / "bridge" / STATE
    if form == "double_alias":
        directory_alias(root / "bridge", github)
        directory_alias(root / "second", root / "bridge")
        return root / "second" / STATE
    if form == "reverse_directory_alias":
        shared = root / "shared"
        github.rename(shared)
        directory_alias(github, shared)
        return shared / STATE
    if form == "public":
        return root / "public" / "tcg-live" / "new" / "plan.json"
    if form == "cloud":
        return root / "Meta" / "TCG" / "new" / "plan.json"
    if form in {"loop", "missing_before_loop"}:
        directory_alias(root / "loop", root / "loop")
        if form == "missing_before_loop":
            return root / "missing" / ".." / "loop" / "plan.json"
        return root / "loop" / "plan.json"
    alias = root / "ordinary.json"
    try:
        if form == "hardlink":
            os.link(state, alias)
        else:
            if form == "reverse_file_alias":
                if existing:
                    state.rename(alias)
                state.symlink_to(alias)
            else:
                alias.symlink_to(state)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"File link unavailable: {exc}")
    return alias


FORMS = (
    "direct", "directory_alias", "double_alias", "reverse_directory_alias",
    "file_alias", "reverse_file_alias", "public", "cloud", "loop", "missing_before_loop",
)


@pytest.mark.parametrize("flag", ("--output", "--github-output"))
@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("existing", (False, True))
@pytest.mark.parametrize("form", FORMS)
def test_plan_refuses_before_either_output(tmp_path, monkeypatch, flag, absolute, existing, form):
    monkeypatch.chdir(tmp_path)
    catalog = tmp_path / "catalog.csv"
    catalog.write_text("code,name\nB3b,Old\nB4,Current\n", encoding="utf-8")
    protected = protected_destination(tmp_path, form, existing)
    output = tmp_path / "pocket" / "plan.json"
    actions = tmp_path / "actions-output"
    if existing:
        output.parent.mkdir()
        output.write_bytes(b"old-plan")
        actions.write_bytes(b"previous=value\n")
    destinations = {"--output": output, "--github-output": actions}
    destinations[flag] = protected
    argv = ["plan", "--catalog", str(catalog), "--state", "absent-pocket-state.json"]
    for key, path in destinations.items():
        argv += [key, str(path if absolute else path.relative_to(tmp_path))]
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="destination identity|protected TCG"):
        main(argv)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("flag", ("--output", "--github-output"))
def test_plan_single_output_option_refuses_without_default_write(tmp_path, monkeypatch, flag):
    monkeypatch.chdir(tmp_path)
    destination = protected_destination(tmp_path, "direct", True)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG"):
        main(["plan", flag, str(destination)])
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("flag", ("--output", "--github-output"))
def test_plan_denies_hardlink_state(tmp_path, monkeypatch, flag):
    monkeypatch.chdir(tmp_path)
    destination = protected_destination(tmp_path, "hardlink", True)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG"):
        main(["plan", flag, str(destination)])
    assert snapshot(tmp_path) == before


def transaction(source, destination, extra=False):
    items = [PublicationArtifact(source, destination.name, "manifest", True)]
    if extra:
        items.insert(0, PublicationArtifact(source, "innocent/data.json", "manifest"))
    return publish_transaction(
        game="POCKET", source_root=source.parent, target_root=destination.parent,
        artifacts=items, allowed_paths=[item.relative_path for item in items],
        manifest_relative_path=destination.name,
    )


@pytest.mark.parametrize("form,existing", (
    *((form, existing) for form in FORMS for existing in (False, True)),
    ("hardlink", True),
))
@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("extra", (False, True))
def test_generic_pocket_refuses_state_before_staging(tmp_path, monkeypatch, form, existing, absolute, extra):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source" / "manifest.json"
    source.parent.mkdir()
    source.write_text('{"outputs":{}}', encoding="utf-8")
    destination = protected_destination(tmp_path, form, existing)
    if not absolute:
        destination = destination.relative_to(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(PublicationError, match="TCG destination|destination identity"):
        transaction(source, destination, extra=extra)
    assert snapshot(tmp_path) == before


def test_planner_and_actions_positive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("catalog.csv").write_text("code,name\nB3b,Old\nB4,Current\n", encoding="utf-8")
    actions = tmp_path / "github_file_commands" / "set_output_123"
    actions.parent.mkdir()
    actions.write_text("prior=value\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(actions))
    assert main(["plan", "--catalog", "catalog.csv", "--output", "pocket/plan.json",
                 "--github-output", os.environ["GITHUB_OUTPUT"]]) == 0
    assert json.loads(Path("pocket/plan.json").read_text())["completed_set"]["code"] == "B3b"
    assert actions.read_text().startswith("prior=value\naction=")


def test_generic_pocket_checks_nonmanifest_destination(tmp_path):
    source = tmp_path / "source" / "manifest.json"
    source.parent.mkdir()
    source.write_text('{"outputs":{}}', encoding="utf-8")
    destination = protected_destination(tmp_path, "direct", True)
    before = snapshot(tmp_path)
    with pytest.raises(PublicationError, match="TCG destination"):
        publish_transaction(
            game="POCKET", source_root=source.parent, target_root=tmp_path,
            artifacts=(
                PublicationArtifact(source, ".github/" + STATE, "manifest"),
                PublicationArtifact(source, "pocket/manifest.json", "manifest", True),
            ),
            allowed_paths=(".github/" + STATE, "pocket/manifest.json"),
            manifest_relative_path="pocket/manifest.json",
        )
    assert destination.read_bytes() == b'{"protected":true}\n'
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("relative", (".github/pocket-state.json", "TCG/unrelated/pocket.json", "Meta/POCKET/manifest.json"))
def test_generic_pocket_positive(tmp_path, relative):
    source = tmp_path / "source" / "manifest.json"
    source.parent.mkdir()
    source.write_text('{"outputs":{}}', encoding="utf-8")
    destination = tmp_path / relative
    result = transaction(source, destination)
    assert result.status == "PASS"
    assert destination.read_bytes() == source.read_bytes()
    assert not list(tmp_path.rglob("*.tmp"))
