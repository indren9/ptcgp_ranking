"""R12 offline output-boundary tests. All writes use synthetic temporary roots."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from scripts import automatic_completed_meta_rollover_job as job
from scripts import latest_completed_meta_producer as producer
from scripts.latest_completed_meta import REQUIRED_BUNDLE_FILES
from test_f08r10_output_preflight import (
    FORMS, STATE, directory_alias, protected_destination,
)


def snapshot(root):
    result = {}
    for base, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            p = Path(base) / name
            st = p.lstat()
            identity = (st.st_dev, st.st_ino, st.st_nlink)
            if p.is_symlink():
                value = ("symlink", os.readlink(p), identity)
            elif p.is_junction():
                value = ("junction", str(p.resolve()), identity)
            elif p.is_file():
                data = p.read_bytes()
                value = ("file", data, hashlib.sha256(data).hexdigest(), identity)
            else:
                value = ("directory", identity)
            result[str(p.relative_to(root))] = value
        directories[:] = [n for n in directories if not (Path(base) / n).is_junction()]
    return result


def invoke_job(destination, repo, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pocket-job", "--allow-public-write",
        "--repo-root", str(repo), "--work-root", str(repo / "work"),
        "--report", str(destination)])
    return job.main()


@pytest.mark.parametrize("form,existing", [(f, e) for f in (*FORMS, "hardlink")
    for e in (False, True) if f != "hardlink" or e])
@pytest.mark.parametrize("relative", (False, True))
def test_report_refused_before_job_even_noop(tmp_path, monkeypatch, form, existing, relative):
    monkeypatch.chdir(tmp_path)
    dest = protected_destination(tmp_path, form, existing)
    if relative:
        dest = dest.relative_to(tmp_path)
    calls = []
    def noop(**kw):
        calls.append(kw)
        return {"action": "noop"}
    monkeypatch.setattr(job, "run_job", noop)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG|destination identity|multiply-linked"):
        invoke_job(dest, tmp_path, monkeypatch)
    assert calls == []
    assert snapshot(tmp_path) == before


def noop_repo(repo):
    (repo / "public").mkdir(parents=True)
    (repo / ".github").mkdir()
    (repo / "public/expansions_pocket_standard.csv").write_text("code,name\nX1,Old\nX2,Current\n")
    (repo / ".github/latest-completed-meta-state.json").write_text(json.dumps({
        "published_completed_set": {"code": "X1", "name": "Old"}}))


def test_real_pocket_noop_and_relative_report_keep_original_cwd_semantics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    noop_repo(repo)
    monkeypatch.setattr(job, "S3ObjectStoreBackend", lambda *a, **k: pytest.fail("network backend"))
    (repo / "work").mkdir()
    before = snapshot(repo)
    assert invoke_job(Path("reports/new/result.json"), repo, monkeypatch) == 0
    report = json.loads((tmp_path / "reports/new/result.json").read_text())
    assert report["action"] == "noop"
    assert report["published"] is False
    assert snapshot(repo) == before
    assert Path.cwd() == tmp_path


def build(dest, root):
    return producer.build_bundle(source_run=root / "source", config_path=root / "pocket.yaml",
        bundle_dir=dest, set_code="X1", set_name="Synthetic", acquired_on="2020-01-01")


@pytest.mark.parametrize("entry", ("api", "cli"))
@pytest.mark.parametrize("namespace", ("public/tcg-live", "Meta/TCG", ".github/" + STATE))
@pytest.mark.parametrize("form", ("direct", "child", "traversal", "alias", "reverse"))
def test_bundle_namespace_refused_without_input_or_mkdir(tmp_path, monkeypatch, entry, namespace, form):
    monkeypatch.chdir(tmp_path)
    protected = tmp_path / namespace
    dest = protected
    if form == "child":
        dest = protected / "new/bundle"
    elif form == "traversal":
        dest = tmp_path / "missing/../" / namespace
    elif form in {"alias", "reverse"}:
        protected.mkdir(parents=True)
        alias = tmp_path / "ordinary"
        if form == "alias":
            directory_alias(alias, protected)
        else:
            protected.rename(alias)
            directory_alias(protected, alias)
        dest = alias / "new/bundle"
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG"):
        if entry == "api":
            build(dest, tmp_path)
        else:
            # Plan parsing is read-only; the actual API must still reject.
            monkeypatch.setattr(producer, "_load_plan", lambda p: ("X1", "Synthetic"))
            producer.main(["--plan", "unused", "--source-run", "missing", "--bundle", str(dest),
                           "--acquired-on", "2020-01-01"])
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("entry", ("bundle", "report"))
@pytest.mark.parametrize("artifact", REQUIRED_BUNDLE_FILES)
@pytest.mark.parametrize("protected_kind", ("state", "public", "cloud"))
@pytest.mark.parametrize("link_kind", ("file", "hardlink", "reverse_file"))
def test_each_bundle_artifact_alias_refused_before_any_other_output(
    tmp_path, monkeypatch, entry, artifact, protected_kind, link_kind,
):
    protected = (tmp_path / ".github" / STATE if protected_kind == "state" else
        tmp_path / ("public/tcg-live/latest-meta" if protected_kind == "public" else "Meta/TCG") / artifact)
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"protected original bytes")
    bundle = tmp_path / "ordinary/bundle"
    bundle.mkdir(parents=True)
    dest = bundle / artifact
    try:
        if link_kind == "hardlink":
            os.link(protected, dest)
        elif link_kind == "reverse_file":
            protected.rename(dest)
            protected.symlink_to(dest)
        else:
            dest.symlink_to(protected)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Native file link unavailable: {exc}")
    before = snapshot(tmp_path)
    monkeypatch.setattr(job, "run_job", lambda **kw: pytest.fail("job started"))
    with pytest.raises(ValueError, match="protected TCG|multiply-linked"):
        if entry == "bundle":
            build(bundle, tmp_path)
        else:
            invoke_job(dest, tmp_path, monkeypatch)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("artifact", REQUIRED_BUNDLE_FILES)
def test_bundle_ancestor_with_protected_reverse_artifact_refused(tmp_path, artifact):
    # The bundle is an ancestor of the protected namespace; one effective
    # artifact is the physical target of its canonical public file.
    public = tmp_path / "public/tcg-live/latest-meta"
    public.mkdir(parents=True)
    physical = tmp_path / artifact
    physical.write_bytes(b"protected via reverse alias")
    try:
        (public / artifact).symlink_to(physical)
    except OSError as exc:
        pytest.skip(f"Native symlink unavailable: {exc}")
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG|multiply-linked"):
        build(tmp_path, tmp_path)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("entry", ("api", "cli"))
@pytest.mark.parametrize("existing", (False, True))
def test_ordinary_pocket_bundle_generates_four_valid_outputs(tmp_path, monkeypatch, entry, existing):
    import pandas as pd
    import yaml
    import pipelines.deck_ranking as pipeline
    import reporting.plots as plots
    import matplotlib.pyplot as plt
    from test_latest_completed_meta_producer import sample_ranking
    from scripts.latest_completed_meta import validate_bundle

    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    ranking = sample_ranking()
    for relative, frame in (
        ("decklists/top_meta/top_meta_decklist_latest.csv", pd.DataFrame({"Deck": ["Pikachu", "Mewtwo"]})),
        ("matchups/raw/matchup_raw_latest.csv", pd.DataFrame({"Deck": ["Pikachu"]})),
        ("rankings/mars/mars_ranking_latest.csv", ranking),
        ("decklists/raw/decklist_raw_latest.csv", pd.DataFrame({"URL": [
            "https://example.invalid/?game=POCKET&format=standard&set=X1"]})),
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
    (tmp_path / "pocket.yaml").write_text(yaml.safe_dump({"source": {"game": "POCKET"}}))
    monkeypatch.setattr(pipeline, "_build_core_matrices", lambda **kw: (
        {"score_flat": ranking, "wr_matrix": ranking, "n_dir_matrix": ranking}, {},
        {"axis0_count": 2, "nan_filter": {"dropped_count": 0}}))
    monkeypatch.setattr(pipeline, "_run_mars_stage", lambda **kw: (
        {"mars_ranking": ranking}, {}, {"AUTO_K": {"K_used": 1}}))
    def heatmap(*a, **kw):
        figure, ax = plt.subplots()
        ax.plot([0, 1], [0, 1])
        return figure, ax, ranking
    monkeypatch.setattr(plots, "show_wr_heatmap", heatmap)
    bundle = tmp_path / "ordinary/new/bundle"
    if existing:
        bundle.mkdir(parents=True)
        for name in REQUIRED_BUNDLE_FILES:
            (bundle / name).write_bytes(b"old ordinary output")
    if entry == "api":
        manifest = build(bundle, tmp_path)
    else:
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"action": "publish", "completed_set": {"code": "X1", "name": "Synthetic"}}))
        assert producer.main(["--plan", str(plan), "--source-run", str(source),
            "--config", "pocket.yaml", "--bundle", str(bundle), "--acquired-on", "2020-01-01"]) == 0
        manifest = json.loads((bundle / "manifest.json").read_text())
    assert set(p.name for p in bundle.iterdir()) == set(REQUIRED_BUNDLE_FILES)
    assert manifest["game"] == "POCKET"
    assert manifest["outputs"]["ranking"]["sha256"] == hashlib.sha256((bundle / "ranking.csv").read_bytes()).hexdigest()
    assert manifest["outputs"]["heatmap"]["sha256"] == hashlib.sha256((bundle / "heatmap.png").read_bytes()).hexdigest()
    validate_bundle(bundle, {"action": "publish", "completed_set": {"code": "X1"}})


@pytest.mark.parametrize("entry", ("report", "bundle"))
def test_nonregular_destination_refused_before_side_effects(tmp_path, monkeypatch, entry):
    destination = tmp_path / "existing-directory"
    destination.mkdir()
    monkeypatch.setattr(job, "run_job", lambda **kw: pytest.fail("job started"))
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="regular file"):
        if entry == "report":
            invoke_job(destination, tmp_path, monkeypatch)
        else:
            (destination / "fragment.md").mkdir()
            before = snapshot(tmp_path)
            build(destination, tmp_path)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("entry", ("bundle", "report"))
def test_literal_tilde_component_matches_actual_writer_path(tmp_path, monkeypatch, entry):
    monkeypatch.chdir(tmp_path)
    protected = tmp_path / "public/tcg-live"
    protected.mkdir(parents=True)
    (tmp_path / "~").mkdir()
    directory_alias(tmp_path / "~/ordinary", protected)
    monkeypatch.setattr(job, "run_job", lambda **kw: pytest.fail("job started"))
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG"):
        if entry == "bundle":
            build(Path("~/ordinary/new"), tmp_path)
        else:
            invoke_job(Path("~/ordinary/report.json"), tmp_path, monkeypatch)
    assert snapshot(tmp_path) == before
