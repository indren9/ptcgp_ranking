"""Real job, transaction and config writer; synthetic offline dependencies only."""
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from scripts import automatic_completed_meta_rollover_job as job
from scripts.automatic_completed_meta_rollover import derive_release_window
from scripts.latest_completed_meta import build_publication_plan, read_catalog
from test_d2b_rollover_contract import _write_live_manifest, _write_release_catalog
from test_f08r10_output_preflight import STATE, directory_alias
from test_f08r12_pocket_outputs import snapshot as content_snapshot
from test_f08r14_pocket_cleanup import file_alias

NOW = "2026-09-03T12:00:00Z"
TEMPLATE = {"source": {"game": "POCKET", "extra": "preserved"},
            "saving": {"extra": True}, "paths": {"extra": "unchanged"},
            "mars": {"unchanged": [1, 2, 3]}}


class ProducerReached(Exception):
    pass


def snapshot(root):
    # Include special-file mode/rdev too; never open a FIFO or socket.
    contents = content_snapshot(root)
    return {name: (value, (root / name).lstat().st_mode,
                   (root / name).lstat().st_rdev)
            for name, value in contents.items()}


def setup_job(tmp_path, monkeypatch, root_form):
    repo, work, cwd = (tmp_path / n for n in ("C", "W", "outside"))
    for folder in ("public", ".github", "config", "data/reference", "Meta/TCG"):
        (repo / folder).mkdir(parents=True, exist_ok=True)
    work.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    (repo / "public/expansions_pocket_standard.csv").write_text(
        "code,name\nX1,Set One\nX2,Set Two\nX3,Set Three\n")
    state = {"published_completed_set": {"code": "X1", "name": "Set One"}}
    (repo / ".github/latest-completed-meta-state.json").write_text(json.dumps(state))
    (repo / "config/pocket.yaml").write_text(yaml.safe_dump(TEMPLATE))
    catalog = _write_release_catalog(repo / "data/reference/pocket_releases.json")
    plan = build_publication_plan(read_catalog(repo / "public/expansions_pocket_standard.csv"),
                                  state, generated_at=NOW)
    window = derive_release_window(plan, catalog)
    live = _write_live_manifest(work / "raw-live/runs/live-x2/manifest.json", window)
    for path in (repo / ".github" / STATE, repo / "public/tcg-live/latest-meta/extra",
                 repo / "Meta/TCG/extra"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"protected original")
    monkeypatch.setattr(job, "_git_revision", lambda p: "synthetic")
    acquire = Mock(return_value=SimpleNamespace(manifest=SimpleNamespace(run_id="live-x2")))
    persist = Mock(return_value=SimpleNamespace(manifest_key="synthetic"))
    # Successful restore dependency without incidental writes: negatives can
    # snapshot the entire fixture around run_job, including real lock cleanup.
    restore = Mock(return_value=None)
    pipeline = Mock(side_effect=AssertionError("pipeline after refusal"))
    producer = Mock(side_effect=AssertionError("producer after refusal"))
    for name, value in (("run_limitless_api_acquisition", acquire),
                        ("persist_canonical_raw_run", persist),
                        ("restore_canonical_raw_run", restore),
                        ("run_deck_ranking", pipeline), ("build_bundle", producer)):
        monkeypatch.setattr(job, name, value)
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("network forbidden")))
    roots = {"absolute": (repo, work), "relative": (Path("../C"), Path("../W")),
             "traversal": (Path("../C/../C"), Path("../W/../W"))}[root_form]
    invoke = lambda: job.run_job(repo_root=roots[0], work_root=roots[1],
                                 generated_at=NOW, backend_factory=lambda: object())
    return SimpleNamespace(repo=repo, work=work, config=work / "production_pocket.yaml",
        live=live, acquire=acquire, persist=persist, restore=restore,
        pipeline=pipeline, producer=producer, invoke=invoke)


def protected_path(env, namespace):
    return {"state": env.repo / ".github" / STATE,
            "public": env.repo / "public/tcg-live/latest-meta/extra",
            "cloud": env.repo / "Meta/TCG/extra"}[namespace]


def assert_refused(tmp_path, monkeypatch, env, *, reaches_writer=True):
    write_text = Path.write_text
    writes = Mock(side_effect=AssertionError("config write before refusal"))
    def guard(path, *args, **kw):
        if path == env.config:
            return writes(path, *args, **kw)
        return write_text(path, *args, **kw)
    monkeypatch.setattr(Path, "write_text", guard)
    writer = Mock(wraps=job._write_production_config)
    monkeypatch.setattr(job, "_write_production_config", writer)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG|destination identity|regular file|multiply-linked"):
        env.invoke()
    assert snapshot(tmp_path) == before
    writes.assert_not_called()
    env.pipeline.assert_not_called()
    env.producer.assert_not_called()
    env.acquire.assert_called_once()
    env.persist.assert_called_once()
    if reaches_writer:
        assert env.restore.call_count == 1
        writer.assert_called_once()
    else:
        env.restore.assert_not_called()
        writer.assert_not_called()
    assert not (env.work / "transaction").exists()
    assert not (env.work / "rollover.lock").exists()


@pytest.mark.parametrize("root_form", ("absolute", "relative", "traversal"))
@pytest.mark.parametrize("namespace", ("state", "public", "cloud"))
@pytest.mark.parametrize("form", ("reverse", "reverse_missing", "hardlink", "forward", "forward_missing"))
def test_config_aliases_refused_without_mutation(tmp_path, monkeypatch, root_form, namespace, form):
    env = setup_job(tmp_path, monkeypatch, root_form)
    protected = protected_path(env, namespace)
    if form.startswith("reverse"):
        if form == "reverse":
            env.config.write_bytes(b"existing Pocket config")
        protected.unlink()
        file_alias(protected, env.config)
    elif form == "hardlink":
        try:
            os.link(protected, env.config)
        except OSError as exc:
            pytest.skip(f"Native hardlink unavailable: {exc}")
    else:
        if form == "forward_missing":
            protected.unlink()
        file_alias(env.config, protected)
    assert_refused(tmp_path, monkeypatch, env)


@pytest.mark.parametrize("root_form", ("absolute", "relative", "traversal"))
@pytest.mark.parametrize("namespace", ("public", "cloud"))
@pytest.mark.parametrize("form", ("reverse_directory", "forward_directory"))
def test_directory_aliases(tmp_path, monkeypatch, root_form, namespace, form):
    env = setup_job(tmp_path, monkeypatch, root_form)
    protected = protected_path(env, namespace)
    if form == "reverse_directory":
        env.config.write_bytes(b"original")
        directory_alias(protected.parent / "alias", env.work)
        # R16 correctly refuses earlier: raw-replay is within this alias.
        reaches_writer = False
    else:
        directory_alias(env.config, protected.parent)
        reaches_writer = True
    assert_refused(tmp_path, monkeypatch, env, reaches_writer=reaches_writer)


@pytest.mark.parametrize("root_form", ("absolute", "relative", "traversal"))
@pytest.mark.parametrize("namespace", ("public", "cloud"))
def test_reverse_artifact_through_directory_alias(tmp_path, monkeypatch, root_form, namespace):
    env = setup_job(tmp_path, monkeypatch, root_form)
    ordinary = tmp_path / "artifact-container"
    ordinary.mkdir()
    env.config.write_bytes(b"original config")
    file_alias(ordinary / "nested-artifact", env.config)
    directory_alias(protected_path(env, namespace).parent / "alias", ordinary)
    assert_refused(tmp_path, monkeypatch, env)


@pytest.mark.parametrize("root_form", ("absolute", "relative", "traversal"))
def test_unknown_hardlink_refused_conservatively(tmp_path, monkeypatch, root_form):
    env = setup_job(tmp_path, monkeypatch, root_form)
    env.config.write_bytes(b"ordinary but multiply linked")
    try:
        os.link(env.config, tmp_path / "unknown-alias")
    except OSError as exc:
        pytest.skip(f"Native hardlink unavailable: {exc}")
    assert_refused(tmp_path, monkeypatch, env)


@pytest.mark.parametrize("root_form", ("absolute", "relative", "traversal"))
@pytest.mark.parametrize("existing", (False, True))
def test_writer_api_keeps_explicit_checkout_identity(tmp_path, monkeypatch, root_form, existing):
    env = setup_job(tmp_path, monkeypatch, root_form)
    if existing:
        env.config.write_bytes(b"original config")
    protected = protected_path(env, "state")
    protected.unlink()
    file_alias(protected, env.config)
    repo, work = {"absolute": (env.repo, env.work),
                  "relative": (Path("../C"), Path("../W")),
                  "traversal": (Path("../C/../C"), Path("../W/../W"))}[root_form]
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="protected TCG"):
        job._write_production_config(repo_root=repo, work_root=work,
            raw_root=work / "raw-replay", release_catalog=repo / "data/reference/pocket_releases.json",
            replay_run_id="live-x2", completed_code="X2")
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("form", ("directory", "fifo", "socket", "config_loop", "canonical_loop"))
def test_nonregular_and_ambiguous_targets(tmp_path, monkeypatch, form):
    env = setup_job(tmp_path, monkeypatch, "absolute")
    if form == "directory":
        env.config.mkdir()
    elif form == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("Native FIFO unavailable")
        os.mkfifo(env.config)
    elif form == "socket":
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("Native Unix socket unavailable")
        try:
            sock = socket.socket(socket.AF_UNIX)
        except OSError as exc:
            pytest.skip(f"Native Unix socket unavailable: {exc}")
        with sock:
            sock.bind(os.path.relpath(env.config))
            assert_refused(tmp_path, monkeypatch, env)
        return
    elif form == "config_loop":
        file_alias(env.config, env.config)
    else:
        protected = protected_path(env, "state")
        protected.unlink()
        file_alias(protected, protected)
    assert_refused(tmp_path, monkeypatch, env, reaches_writer=form != "canonical_loop")


@pytest.mark.parametrize("root_form", ("absolute", "relative", "traversal"))
@pytest.mark.parametrize("existing", (False, True))
def test_ordinary_config_real_writer_and_transaction_continue(tmp_path, monkeypatch, root_form, existing):
    env = setup_job(tmp_path, monkeypatch, root_form)
    if existing:
        env.config.write_bytes(b"obsolete Pocket config")
    protected_before = snapshot(env.repo)
    def restore(destination, run_id, backend):
        target = destination / "runs/offline-x2"
        target.mkdir(parents=True)
        manifest = json.loads(env.live.read_text())
        manifest["run_id"] = "offline-x2"
        (target / "manifest.json").write_text(json.dumps(manifest))
        (target / "diagnostics.json").write_text(json.dumps({
            "execution_mode": "offline", "replay_run_id": run_id, "network_calls": 0}))
        (target / "payload").write_bytes(b"synthetic restored raw")
    env.restore.side_effect = restore
    expected = yaml.safe_load((env.repo / "config/pocket.yaml").read_text())
    expected["source"].update(provider="limitless", game="POCKET", acquisition="tournament_api",
        format={"mode": "code", "code": "standard"}, tournament_api={
            "execution_mode": "offline", "replay_run_id": "live-x2",
            "raw_store_root": str(env.work / "raw-replay"),
            "cache_root": str(env.work / "cache-offline"),
            "release_catalog": str(env.repo / "data/reference/pocket_releases.json"),
            "cache_ttl_min": 0, "reuse_latest_raw": True})
    expected["scraping"] = {"set": {"mode": "code", "code": "X2"}}
    expected["saving"].update(output_profile="debug", include_time_when_changed=False,
                              filename_prefix_with_set=False)
    expected["paths"]["output_dir"] = str(env.work / "source-output")
    def pipeline(**kw):
        assert kw["config_path"] == env.config
        assert yaml.safe_load(env.config.read_text()) == expected
        assert env.config.read_text() == yaml.safe_dump(expected, sort_keys=False, allow_unicode=True)
        assert (env.work / "raw-replay/runs/offline-x2/payload").read_bytes() == b"synthetic restored raw"
        return SimpleNamespace(diagnostics={"tournament_api_run_id": "offline-x2",
            "tournament_api_network_calls": 0}, paths=SimpleNamespace(outputs=env.work / "source-output"),
            expansion="X2")
    env.pipeline.side_effect = pipeline
    env.producer.side_effect = ProducerReached
    with pytest.raises(ProducerReached):
        env.invoke()
    env.pipeline.assert_called_once()
    env.producer.assert_called_once()  # real replay validation passed before producer
    assert snapshot(env.repo) == protected_before
    assert not (env.work / "transaction").exists()
    assert not (env.work / "rollover.lock").exists()
