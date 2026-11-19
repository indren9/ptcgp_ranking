"""Offline exercise of the production Pocket caller's actual cleanup hook."""
from contextlib import nullcontext
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import automatic_completed_meta_rollover_job as job
from scripts.latest_completed_meta import REQUIRED_BUNDLE_FILES
from test_f08r10_output_preflight import directory_alias
from test_f08r12_pocket_outputs import snapshot


def file_alias(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Native file symlink unavailable: {exc}")


def caller(monkeypatch, repo, work, *, positive=False):
    plan = {"action": "publish", "reason": "synthetic", "current_set": {},
            "completed_set": {"code": "X1", "name": "Synthetic"}}
    monkeypatch.setattr(job, "read_catalog", lambda p: [])
    monkeypatch.setattr(job, "read_json", lambda *a, **k: None)
    monkeypatch.setattr(job, "build_publication_plan", lambda *a, **k: plan)
    monkeypatch.setattr(job, "_git_revision", lambda p: "synthetic")
    # Isolate the hook; no LIVE acquisition, storage, publication or lock writes.
    monkeypatch.setattr(job, "RolloverLock", lambda p: nullcontext())
    for name in ("run_limitless_api_acquisition", "persist_canonical_raw_run"):
        monkeypatch.setattr(job, name, Mock(side_effect=AssertionError("LIVE forbidden")))
    if positive:
        monkeypatch.setattr(job, "restore_canonical_raw_run", lambda *a: None)
        monkeypatch.setattr(job, "_write_production_config", lambda **kw: repo / "synthetic.yaml")
        monkeypatch.setattr(job, "base_for_expansion", lambda *a: work / "source")
        monkeypatch.setattr(job, "run_deck_ranking", lambda **kw: SimpleNamespace(
            diagnostics={"tournament_api_run_id": "offline", "tournament_api_network_calls": 0},
            paths=SimpleNamespace(outputs=work / "source"), expansion="X1"))

    def transaction(**kw):
        hooks = kw["hooks"]
        if positive:
            hooks.replay_offline("offline", plan, SimpleNamespace(completed_code="X1"))
        bundle = hooks.produce_bundle(plan, work / "source", work / "manifest")
        assert bundle == work.resolve() / "bundle"
        return SimpleNamespace(plan=plan, published=False, run_id="offline",
            restored_prior_raw=False, restore_failure_type=None)
    monkeypatch.setattr(job, "run_rollover_transaction", transaction)
    return lambda: job.run_job(repo_root=repo, work_root=work,
        generated_at="2026-10-29T00:00:00Z", backend_factory=lambda: object())


@pytest.mark.parametrize("relative", (False, True))
@pytest.mark.parametrize("form", (
    "reverse", "reverse_child", "reverse_file", "hardlink", "forward",
    "cloud_reverse", "state_reverse", "canonical_loop", "tree_loop",
))
def test_caller_refuses_before_rmtree_and_preserves_entire_tree(
    tmp_path, monkeypatch, relative, form,
):
    monkeypatch.chdir(tmp_path)
    # Checkout C and work W are siblings: repo_root must be a known identity root.
    repo, work = tmp_path / "C", tmp_path / "W"
    repo.mkdir()
    bundle = work / "bundle"
    bundle.mkdir(parents=True)
    for name in REQUIRED_BUNDLE_FILES:
        (bundle / name).write_bytes(b"old standard artifact " + name.encode())
    nested = bundle / "obsolete/deep"
    nested.mkdir(parents=True)
    arbitrary = nested / "protected-extra.bin"
    arbitrary.write_bytes(b"arbitrary protected stale contents")
    public = repo / "public/tcg-live/latest-meta"
    public.parent.mkdir(parents=True)
    if form in {"reverse", "reverse_child"}:
        directory_alias(public, bundle if form == "reverse" else nested)
    elif form == "cloud_reverse":
        (repo / "Meta").mkdir()
        directory_alias(repo / "Meta/TCG", nested)
    elif form == "state_reverse":
        (repo / ".github").mkdir()
        file_alias(repo / ".github/tcg-live-latest-completed-meta-state.json", arbitrary)
    elif form == "forward":
        public.mkdir()
        (public / "extra").write_bytes(b"protected")
        directory_alias(bundle / "forward", public)
        # A forward directory link alone isn't traversed by rmtree. Put the
        # protected namespace's reverse reference inside the deleted tree too.
        file_alias(public / "reverse-extra", arbitrary)
    elif form in {"reverse_file", "hardlink"}:
        public.mkdir()
        try:
            if form == "hardlink":
                os.link(arbitrary, public / "unusual-name")
            else:
                (public / "unusual-name").symlink_to(arbitrary)
        except OSError as exc:
            pytest.skip(f"Native file link unavailable: {exc}")
    else:
        loop = public if form == "canonical_loop" else bundle / "loop"
        directory_alias(loop, loop)
    invoke = caller(monkeypatch, repo, work)
    build = Mock(side_effect=AssertionError("producer must not run"))
    remove = Mock(side_effect=AssertionError("rmtree must not run"))
    monkeypatch.setattr(job, "build_bundle", build)
    monkeypatch.setattr(job.shutil, "rmtree", remove)
    if relative:
        invoke = caller(monkeypatch, Path("C"), Path("W"))
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG|destination identity"):
        invoke()
    remove.assert_not_called()
    build.assert_not_called()
    assert snapshot(tmp_path) == before  # bytes, SHA-256, links, inodes, nlink, entries


@pytest.mark.parametrize("relative", (False, True))
def test_caller_removes_ordinary_stale_tree_then_builds_pocket(tmp_path, monkeypatch, relative):
    monkeypatch.chdir(tmp_path)
    repo, work = tmp_path / "C", tmp_path / "W"
    repo.mkdir()
    bundle = work / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "obsolete").mkdir()
    (bundle / "obsolete/stale.bin").write_bytes(b"stale")
    for name in REQUIRED_BUNDLE_FILES:
        (bundle / name).write_bytes(b"old Pocket")
    # Existing protected files outside the cleanup remain untouched.
    public = repo / "public/tcg-live/latest-meta"
    public.mkdir(parents=True)
    (public / "unusual-name").write_bytes(b"unchanged TCG")
    protected_before = snapshot(repo)
    remove = Mock(wraps=job.shutil.rmtree)
    monkeypatch.setattr(job.shutil, "rmtree", remove)
    def synthetic_build(**kw):
        assert not kw["bundle_dir"].exists()
        assert kw["set_code"] == "X1"
        kw["bundle_dir"].mkdir()
        for name in REQUIRED_BUNDLE_FILES:
            (kw["bundle_dir"] / name).write_bytes(b"new synthetic Pocket")
    monkeypatch.setattr(job, "build_bundle", synthetic_build)
    roots = (Path("C"), Path("W")) if relative else (repo, work)
    result = caller(monkeypatch, *roots, positive=True)()
    remove.assert_called_once_with(bundle)
    assert result["published"] is False
    assert set(p.name for p in bundle.iterdir()) == set(REQUIRED_BUNDLE_FILES)
    assert all(p.read_bytes() == b"new synthetic Pocket" for p in bundle.iterdir())
    assert snapshot(repo) == protected_before
