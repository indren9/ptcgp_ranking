"""R10: synthetic offline destinations; refusal precedes all observable writes."""
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts import latest_completed_meta as pocket
from scripts import tcg_live_latest_completed_job as job
from scripts import tcg_live_latest_completed as planner


STATE = "tcg-live-latest-completed-meta-state.json"


def snapshot(root):
    result = {}
    for base, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(base) / name
            key = str(path.relative_to(root))
            if path.is_symlink():
                value = ("symlink", os.readlink(path))
            elif path.is_junction():
                value = ("junction", str(path.resolve()))
            elif path.is_file():
                stat = path.stat()
                value = ("file", path.read_bytes(), stat.st_dev, stat.st_ino)
            else:
                value = ("directory",)
            result[key] = value
        directories[:] = [name for name in directories
                          if not (Path(base) / name).is_junction()]
    return result


def directory_alias(link, target):
    if os.name == "nt":
        process = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        if process.returncode:
            pytest.skip(f"Native junction unavailable: {process.stderr}")
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
    if form in {"directory", "double"}:
        directory_alias(root / "bridge", github)
        if form == "double":
            directory_alias(root / "second", root / "bridge")
            return root / "second" / STATE
        return root / "bridge" / STATE
    if form == "reverse_directory":
        github.rename(root / "shared")
        directory_alias(github, root / "shared")
        return root / "shared" / STATE
    if form in {"public", "cloud"}:
        return root / ("public/tcg-live" if form == "public" else "Meta/TCG") / "new/plan.json"
    if form in {"loop", "missing_before_loop"}:
        directory_alias(root / "loop", root / "loop")
        if form == "missing_before_loop":
            return root / "missing/../loop/plan.json"
        return root / "loop/plan.json"
    alias = root / "ordinary.json"
    try:
        if form == "hardlink":
            os.link(state, alias)
        elif form == "reverse_file":
            if existing:
                state.rename(alias)
            state.symlink_to(alias)
        else:
            alias.symlink_to(state)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Native file link unavailable: {exc}")
    return alias


FORMS = ("direct", "directory", "double", "reverse_directory", "file",
         "reverse_file", "public", "cloud", "loop", "missing_before_loop")


def invoke(entrypoint, destination, root, monkeypatch):
    if entrypoint == "planner":
        return planner.main(["--output", str(destination)])
    monkeypatch.setattr(sys, "argv", ["tcg-job", "--work-root", str(root / "job-work"),
                                     "--report", str(destination)])
    return job.main()


@pytest.mark.parametrize("entrypoint", ("planner", "job"))
@pytest.mark.parametrize("absolute", (False, True))
@pytest.mark.parametrize("existing", (False, True))
@pytest.mark.parametrize("form", FORMS)
def test_protected_refusal_has_no_side_effects(
    tmp_path, monkeypatch, entrypoint, absolute, existing, form,
):
    monkeypatch.chdir(tmp_path)
    destination = protected_destination(tmp_path, form, existing)
    if not absolute:
        destination = destination.relative_to(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("input loading/planning/job execution preceded preflight")

    monkeypatch.setattr(planner, "read_json", forbidden)
    monkeypatch.setattr(planner, "build_plan", forbidden)
    monkeypatch.setattr(job, "run_job", forbidden)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG|destination identity"):
        invoke(entrypoint, destination, tmp_path, monkeypatch)
    assert calls == []
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("entrypoint", ("planner", "job"))
def test_hardlink_state_refused(tmp_path, monkeypatch, entrypoint):
    monkeypatch.chdir(tmp_path)
    destination = protected_destination(tmp_path, "hardlink", True)
    monkeypatch.setattr(job, "run_job", lambda **kw: pytest.fail("job started"))
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG"):
        invoke(entrypoint, destination, tmp_path, monkeypatch)
    assert snapshot(tmp_path) == before


def test_planner_final_writer_also_refuses(tmp_path):
    destination = protected_destination(tmp_path, "direct", False)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG"):
        planner._write_json(destination, {"arbitrary": True})
    assert snapshot(tmp_path) == before


def synthetic_catalog(root):
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps({
        "catalog_version": "synthetic-r10", "source": "synthetic offline fixture",
        "releases": [
            {"code": "OLD", "name": "Synthetic completed", "release_datetime": "2020-01-01T00:00:00Z",
             "next_release_datetime": "2020-02-01T00:00:00Z", "is_current": False},
            {"code": "NEW", "name": "Synthetic current", "release_datetime": "2020-02-01T00:00:00Z",
             "next_release_datetime": None, "is_current": True},
        ],
    }), encoding="utf-8")
    return catalog


@pytest.mark.parametrize("existing", (False, True))
def test_ordinary_tcg_plan_output(tmp_path, monkeypatch, existing):
    monkeypatch.chdir(tmp_path)
    catalog = synthetic_catalog(tmp_path)
    output = tmp_path / "plans/new/plan.json"
    if existing:
        output.parent.mkdir(parents=True)
        output.write_bytes(b"old-plan")
    assert planner.main(["--catalog", str(catalog), "--state", "absent.json",
                         "--output", str(output)]) == 0
    plan = json.loads(output.read_text())
    assert plan["latest_completed_window"]["code"] == "OLD"
    assert plan["action"] == "publish_required"
    assert not list(tmp_path.rglob("*.lock"))


def test_real_job_noop_writes_ordinary_report_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalog = synthetic_catalog(tmp_path)
    config = tmp_path / "config/tcg.yaml"
    config.parent.mkdir()
    config.write_text(yaml.safe_dump({"source": {"game": "PTCG", "acquisition": "tournament_api", "tournament_api": {
        "release_catalog": str(catalog),
    }}}), encoding="utf-8")
    state = tmp_path / ".github" / STATE
    state.parent.mkdir()
    state.write_text(json.dumps(planner.build_plan(
        catalog_path=catalog, acquisition_started_at=datetime(2020, 3, 1, tzinfo=UTC),
    )["state_after_publish"]), encoding="utf-8")
    before = state.read_bytes()
    monkeypatch.setattr(job, "run_limitless_api_acquisition",
                        lambda **kw: pytest.fail("network acquisition started"))
    report = tmp_path / "reports/new/report.json"
    monkeypatch.setattr(sys, "argv", ["tcg-job", "--repo-root", str(tmp_path),
                                     "--work-root", str(tmp_path / "work"), "--report", str(report)])
    assert job.main() == 0
    assert json.loads(report.read_text()) == {
        "action": "noop", "reason": "latest_completed_already_published",
        "latest_completed_window": json.loads(state.read_text())["published_completed_window"],
        "network_calls": 0, "candidate_ready": False, "published": False,
    }
    assert state.read_bytes() == before


@pytest.mark.parametrize("existing", (False, True))
@pytest.mark.parametrize("flag", ("--output", "--github-output"))
@pytest.mark.parametrize("alias", (False, True))
def test_pocket_directory_refusal_before_first_write(tmp_path, monkeypatch, existing, flag, alias):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "plans/new/plan.json"
    actions = tmp_path / "actions-output"
    if existing:
        output.parent.mkdir(parents=True)
        output.write_bytes(b"unchanged-plan")
        actions.write_bytes(b"prior=value\n")
    directory = tmp_path / "directory"
    directory.mkdir()
    if alias:
        directory_alias(tmp_path / "alias", directory)
        directory = tmp_path / "alias"
    destinations = {"--output": output, "--github-output": actions}
    destinations[flag] = directory
    monkeypatch.setattr(pocket, "read_catalog", lambda *args: pytest.fail("input read before preflight"))
    before = snapshot(tmp_path)
    with pytest.raises(IsADirectoryError, match="Output destination is a directory"):
        pocket.main(["plan", "--output", str(destinations["--output"]),
                     "--github-output", str(destinations["--github-output"])])
    assert snapshot(tmp_path) == before


def test_pocket_plan_and_github_output_append(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("catalog.csv").write_text("code,name\nB3b,Old\nB4,Current\n", encoding="utf-8")
    actions = tmp_path / "actions-output"
    actions.write_text("prior=value\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(actions))
    assert pocket.main(["plan", "--catalog", "catalog.csv", "--output", "plans/plan.json",
                        "--github-output", os.environ["GITHUB_OUTPUT"]]) == 0
    assert json.loads(Path("plans/plan.json").read_text())["completed_set"]["code"] == "B3b"
    assert actions.read_text().startswith("prior=value\naction=publish\n")
