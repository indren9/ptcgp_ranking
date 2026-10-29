"""Synthetic offline QA of the production caller's raw-replay callback."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import automatic_completed_meta_rollover_job as job
from scripts.automatic_completed_meta_rollover import validate_exact_offline_replay
from test_f08r10_output_preflight import STATE, directory_alias
from test_f08r12_pocket_outputs import snapshot
from test_f08r14_pocket_cleanup import file_alias


def invoke_callback(monkeypatch, repo, work, *, positive=False):
    plan = {"action": "publish", "reason": "synthetic", "current_set": {},
            "completed_set": {"code": "X1", "name": "Synthetic"}}
    monkeypatch.setattr(job, "read_catalog", lambda p: [])
    monkeypatch.setattr(job, "read_json", lambda *a, **k: None)
    monkeypatch.setattr(job, "build_publication_plan", lambda *a, **k: plan)
    monkeypatch.setattr(job, "_git_revision", lambda p: "synthetic")
    monkeypatch.setattr(job, "RolloverLock", lambda p: nullcontext())
    for name in ("run_limitless_api_acquisition", "persist_canonical_raw_run", "build_bundle"):
        monkeypatch.setattr(job, name, Mock(side_effect=AssertionError("external action forbidden")))

    def transaction(**kw):
        # Exercise the real nested callback supplied by run_job. All external
        # acquisition/publication is replaced; no LIVE transaction is executed.
        replay = kw["hooks"].replay_offline("offline", plan, SimpleNamespace(completed_code="X1"))
        if not positive:
            pytest.fail("replay continued after unsafe cleanup")
        validate_exact_offline_replay(work.resolve() / "live-manifest.json", replay)
        return SimpleNamespace(plan=plan, published=False, run_id="offline",
            restored_prior_raw=False, restore_failure_type=None)
    monkeypatch.setattr(job, "run_rollover_transaction", transaction)
    return lambda: job.run_job(repo_root=repo, work_root=work,
        generated_at="2026-10-29T00:00:00Z", backend_factory=lambda: object())


@pytest.mark.parametrize("root_form", ("absolute", "relative", "traversal"))
@pytest.mark.parametrize("form", (
    "reverse", "reverse_missing", "reverse_child", "reverse_file", "cloud_reverse", "state_reverse",
    "hardlink_public", "hardlink_cloud", "hardlink_state", "forward_public",
    "forward_cloud", "forward_state", "raw_alias", "raw_alias_missing",
    "canonical_loop", "tree_loop",
))
def test_raw_callback_refuses_before_delete_restore_or_replay(
    tmp_path, monkeypatch, root_form, form,
):
    monkeypatch.chdir(tmp_path)
    repo, work = tmp_path / "C", tmp_path / "W"
    repo.mkdir()
    raw = work / "raw-replay"
    nested = raw / "runs/old/arbitrary/deep"
    nested.mkdir(parents=True)
    (raw / "runs/old/manifest.json").write_bytes(b"old replay manifest")
    extra = nested / "extra.bin"
    extra.write_bytes(b"arbitrary content outside expected restore files")
    public = repo / "public/tcg-live/latest-meta"
    public.parent.mkdir(parents=True)
    state = repo / ".github" / STATE
    state.parent.mkdir()
    cloud = repo / "Meta/TCG"
    cloud.parent.mkdir()
    if form in {"reverse", "reverse_missing", "reverse_child"}:
        if form == "reverse_missing":
            raw.rename(work / "old-raw")
        directory_alias(public, nested if form == "reverse_child" else raw)
    elif form == "cloud_reverse":
        directory_alias(cloud, nested)
    elif form in {"reverse_file", "state_reverse"}:
        public.mkdir()
        file_alias(state if form == "state_reverse" else public / "extra", extra)
    elif form.startswith("hardlink_") or form.startswith("forward_"):
        kind = form.split("_")[1]
        protected = state if kind == "state" else (public if kind == "public" else cloud) / "extra"
        protected.parent.mkdir(parents=True, exist_ok=True)
        if form.startswith("hardlink_"):
            try:
                os.link(extra, protected)
            except OSError as exc:
                pytest.skip(f"Native hardlink unavailable: {exc}")
        else:
            protected.write_bytes(b"protected external original")
            file_alias(raw / "protected-link", protected)
    elif form.startswith("raw_alias"):
        # Keep preparatory fixture changes outside the measured callback.
        raw.rename(work / "old-raw")
        if form == "raw_alias":
            public.mkdir()
            (public / "extra").write_bytes(b"protected")
        directory_alias(raw, public)
    else:
        loop = public if form == "canonical_loop" else raw / "loop"
        directory_alias(loop, loop)
    roots = {"absolute": (repo, work), "relative": (Path("C"), Path("W")),
             "traversal": (Path("C/../C"), Path("W/../W"))}[root_form]
    invoke = invoke_callback(monkeypatch, *roots)
    remove = Mock(side_effect=AssertionError("rmtree must not run"))
    restore = Mock(side_effect=AssertionError("restore must not run"))
    replay = Mock(side_effect=AssertionError("pipeline must not run"))
    monkeypatch.setattr(job.shutil, "rmtree", remove)
    monkeypatch.setattr(job, "restore_canonical_raw_run", restore)
    monkeypatch.setattr(job, "run_deck_ranking", replay)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG|destination identity"):
        invoke()
    remove.assert_not_called()
    restore.assert_not_called()
    replay.assert_not_called()
    job.build_bundle.assert_not_called()
    assert snapshot(tmp_path) == before  # bytes/hash, entries, links and inode identities


@pytest.mark.parametrize("existing", (False, True))
@pytest.mark.parametrize("relative", (False, True))
def test_ordinary_raw_cleanup_restore_and_validated_replay(tmp_path, monkeypatch, existing, relative):
    monkeypatch.chdir(tmp_path)
    repo, work = tmp_path / "C", tmp_path / "W"
    repo.mkdir()
    work.mkdir()
    raw = work / "raw-replay"
    if existing:
        (raw / "obsolete/deep").mkdir(parents=True)
        (raw / "obsolete/deep/stale.bin").write_bytes(b"stale")
    protected = repo / "public/tcg-live/latest-meta"
    protected.mkdir(parents=True)
    (protected / "extra").write_bytes(b"unchanged external TCG")
    before = snapshot(repo)
    roots = (Path("C"), Path("W")) if relative else (repo, work)
    invoke = invoke_callback(monkeypatch, *roots, positive=True)
    remove = Mock(wraps=job.shutil.rmtree)
    monkeypatch.setattr(job.shutil, "rmtree", remove)
    manifest = {"source": "Limitless Tournament API", "run_id": "offline",
                "scope": {"game": "POCKET"}, "selection": {}, "raw": {},
                "normalized": {"hashes": {}}, "contracts": {}, "aggregation": {}}
    (work / "live-manifest.json").write_text(json.dumps(manifest))

    def restore(destination, run_id, backend):
        assert destination == raw and run_id == "offline"
        assert not destination.exists()
        target = destination / "runs" / run_id
        target.mkdir(parents=True)
        (target / "manifest.json").write_text(json.dumps(manifest))
        (target / "diagnostics.json").write_text(json.dumps({
            "execution_mode": "offline", "replay_run_id": run_id, "network_calls": 0}))
        (target / "restored.bin").write_bytes(b"synthetic restored raw")
    restored = Mock(side_effect=restore)
    monkeypatch.setattr(job, "restore_canonical_raw_run", restored)
    def config(**kw):
        assert kw["raw_root"] == raw and kw["replay_run_id"] == "offline"
        return repo / "synthetic.yaml"
    monkeypatch.setattr(job, "_write_production_config", config)
    monkeypatch.setattr(job, "base_for_expansion", lambda *a: work / "source")
    def pipeline(**kw):
        assert not (raw / "obsolete").exists()
        assert (raw / "runs/offline/restored.bin").read_bytes() == b"synthetic restored raw"
        return SimpleNamespace(diagnostics={"tournament_api_run_id": "offline",
            "tournament_api_network_calls": 0}, paths=SimpleNamespace(outputs=work / "source"),
            expansion="X1")
    replay = Mock(side_effect=pipeline)
    monkeypatch.setattr(job, "run_deck_ranking", replay)
    result = invoke()
    if existing:
        remove.assert_called_once_with(raw)
    else:
        remove.assert_not_called()
    restored.assert_called_once()
    replay.assert_called_once()
    assert result["offline_network_calls"] == 0 and result["published"] is False
    assert snapshot(repo) == before


def test_preflight_precedes_even_exists_on_missing_raw(tmp_path, monkeypatch):
    repo, work = tmp_path / "C", tmp_path / "W"
    repo.mkdir()
    work.mkdir()
    invoke = invoke_callback(monkeypatch, repo, work)
    raw = work / "raw-replay"
    original_exists = Path.exists
    def exists(path):
        if path == raw:
            pytest.fail("raw exists checked before preflight rejection")
        return original_exists(path)
    monkeypatch.setattr(Path, "exists", exists)
    def reject(path, **kw):
        assert path == raw
        assert kw == {"cleanup_tree": True, "identity_roots": (repo,)}
        raise ValueError("synthetic preflight rejection")
    monkeypatch.setattr(job, "_check_uncoordinated_output_destination", reject)
    restore = Mock()
    monkeypatch.setattr(job, "restore_canonical_raw_run", restore)
    with pytest.raises(ValueError, match="synthetic preflight rejection"):
        invoke()
    restore.assert_not_called()
