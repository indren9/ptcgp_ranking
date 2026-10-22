"""Issue #36 B3: synthetic, network-free planner and candidate receipt QA."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import scripts.tcg_live_latest_completed_job as job
from scripts.tcg_live_candidate_receipt import (
    REQUIRED_OUTPUTS,
    _receipt_path,
    candidate_identity,
    candidate_lock,
    candidate_workspace,
    load_verified_candidate,
    save_verified_candidate,
)
from scripts.tcg_live_latest_completed import build_plan, read_json


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = (
    ROOT / "data" / "candidates" / "releases"
    / "tcg_live_windows_issue36_b1_candidate.json"
)
OLD_STATE = ROOT / ".github" / "tcg-live-latest-completed-meta-state.json"
WHEN = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _plan(state=None, when=WHEN):
    return build_plan(
        catalog_path=CANDIDATE,
        state=state,
        acquisition_started_at=when,
    )


def _fixture(
    tmp_path, *, window=None, revision="synthetic-revision",
    identity_override=None,
):
    config = tmp_path / "config.yaml"
    config.write_text("effective: tcg\n", encoding="utf-8")
    catalog = tmp_path / "release.json"
    catalog.write_bytes(CANDIDATE.read_bytes())
    window = window or _plan()["latest_completed_window"]
    identity = identity_override or candidate_identity(
        window=window,
        config_path=config,
        catalog_path=catalog,
        revision=revision,
    )
    transaction_root = candidate_workspace(
        work_root=tmp_path, identity=identity
    )
    source = (
        transaction_root / "source-output" / "TCG" / "PBL__Pitch_Black"
    )
    source.mkdir(parents=True)
    outputs = {}
    for key in REQUIRED_OUTPUTS:
        file = source / (key + ".json" if key == "run_manifest" else key + ".csv")
        file.write_text(
            "{}\n" if key == "run_manifest" else "deck,count\na,1\n",
            encoding="utf-8",
        )
        outputs[key] = file

    live = transaction_root / "raw-live" / "runs" / "run-1" / "manifest.json"
    replay = transaction_root / "raw-replay" / "runs" / "run-1" / "manifest.json"
    for f in (live, replay):
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"run_id": "run-1"}) + "\n", encoding="utf-8")

    return dict(
        work_root=tmp_path,
        identity=identity,
        config=config,
        catalog=catalog,
        source_run=source,
        outputs=outputs,
        live_manifest=live,
        replay_manifest=replay,
        run_id="run-1",
        object_manifest_key="canonical/raw/run-1/manifest.json",
        mars_rows=1,
    )


def _persist(env):
    return save_verified_candidate(**{
        k: env[k] for k in (
            "work_root", "identity", "source_run", "outputs",
            "live_manifest", "replay_manifest", "run_id",
            "object_manifest_key", "mars_rows",
        )
    })


def test_candidate_latest_completed_and_half_open_release_boundaries():
    previous = read_json(OLD_STATE)
    plan = _plan(previous)
    assert plan["action"] == "publish_required"
    assert plan["latest_completed_window"] == {
        "code": "PBL",
        "name": "Pitch Black Live window",
        "start": "2026-07-16T17:00:00Z",
        "end": "2026-09-15T17:00:00Z",
        "catalog_version": (
            "tcgl-expansion-boundaries-2026-10-21-issue36-b1-candidate-v2"
        ),
    }
    from sources.limitless.tournament_api.release_catalog import (
        load_release_catalog_snapshot,
        resolve_release,
    )
    catalog = load_release_catalog_snapshot(CANDIDATE)
    assert [x.code for x in catalog.releases] == ["CRI", "PBL", "30C"]
    # Midnight is not a full TCG Live expansion-availability boundary.
    for timestamp in (
        datetime(2026, 5, 21, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 16, 16, 59, tzinfo=UTC),
    ):
        with pytest.raises(ValueError, match="no completed release"):
            resolve_release(
                catalog, mode="latest_completed",
                acquisition_started_at=timestamp,
            )
    for timestamp, expected in (
        (datetime(2026, 7, 16, 17, 0, tzinfo=UTC), "CRI"),
        (datetime(2026, 9, 15, 0, 0, tzinfo=UTC), "CRI"),
        (datetime(2026, 9, 15, 16, 59, tzinfo=UTC), "CRI"),
        (datetime(2026, 9, 15, 17, 0, tzinfo=UTC), "PBL"),
    ):
        assert resolve_release(
            catalog, mode="latest_completed",
            acquisition_started_at=timestamp,
        ).code == expected
    assert catalog.releases[-1].next_release_datetime is None


def test_planner_requires_full_window_identity_including_catalog_version():
    current = _plan()["latest_completed_window"]
    for changed_key in ("name", "start", "end", "catalog_version"):
        stale = dict(current)
        stale[changed_key] = "outdated-value"
        plan = _plan({"published_completed_window": stale})
        assert plan["action"] == "publish_required"
        assert plan["reason"] == "latest_completed_changed"
    same = _plan({"published_completed_window": dict(current)})
    assert same["action"] == "noop"
    assert same["reason"] == "latest_completed_already_published"


def test_receipt_reuses_exact_manifest_and_outputs_without_network(tmp_path):
    env = _fixture(tmp_path)
    _persist(env)
    reused = load_verified_candidate(
        work_root=tmp_path, identity=env["identity"]
    )
    assert reused["candidate_reused"] is True
    assert reused["candidate_ready"] is True
    assert reused["network_calls"] == 0
    assert reused["published"] is False
    assert reused["canonical_live_run_id"] == "run-1"


@pytest.mark.parametrize("input_changed", ["config", "catalog", "revision", "window"])
def test_receipt_invalidated_by_dependency_change(tmp_path, input_changed):
    env = _fixture(tmp_path)
    _persist(env)
    window = dict(env["identity"]["window"])
    revision = "synthetic-revision"
    if input_changed == "config":
        env["config"].write_text("effective: altered\n", encoding="utf-8")
    elif input_changed == "catalog":
        env["catalog"].write_text("{}\n", encoding="utf-8")
    elif input_changed == "revision":
        revision = "new-revision"
    else:
        window["end"] = "2026-09-16T17:00:00Z"
    new_identity = candidate_identity(
        window=window, config_path=env["config"],
        catalog_path=env["catalog"], revision=revision,
    )
    assert load_verified_candidate(
        work_root=tmp_path, identity=new_identity
    ) is None


@pytest.mark.parametrize("bad_file", ["mars_ranking", "live_manifest", "replay_manifest"])
def test_changed_artifacts_fail_closed(tmp_path, bad_file):
    env = _fixture(tmp_path)
    _persist(env)
    if bad_file in env["outputs"]:
        target = env["outputs"][bad_file]
    else:
        target = env[bad_file]
    target.write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="receipt invalid"):
        load_verified_candidate(work_root=tmp_path, identity=env["identity"])


@pytest.mark.parametrize("corruption", ["truncated", "missing_file", "escape"])
def test_corrupt_receipts_fail_closed(tmp_path, corruption):
    env = _fixture(tmp_path)
    receipt_path = _persist(env)
    obj = json.loads(receipt_path.read_text(encoding="utf-8"))
    if corruption == "truncated":
        receipt_path.write_text("{", encoding="utf-8")
    else:
        if corruption == "missing_file":
            obj["files"].pop("n_dir_matrix")
        else:
            obj["files"]["mars_ranking"]["path"] = "../outside.csv"
        receipt_path.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(RuntimeError, match="receipt invalid"):
        load_verified_candidate(work_root=tmp_path, identity=env["identity"])


def test_candidate_missing_required_outputs_never_commits_receipt(tmp_path):
    env = _fixture(tmp_path)
    env["outputs"].pop("n_dir_matrix")
    with pytest.raises(ValueError, match="missing required"):
        _persist(env)
    assert not _receipt_path(tmp_path, env["identity"]).exists()


def test_candidate_lock_excludes_overlap_and_cleans_after_normal_failure(tmp_path):
    env = _fixture(tmp_path)
    identity = env["identity"]
    with candidate_lock(work_root=tmp_path, identity=identity):
        with pytest.raises(RuntimeError, match="locked"):
            with candidate_lock(work_root=tmp_path, identity=identity):
                pytest.fail("second lock entered")
    with pytest.raises(AssertionError):
        with candidate_lock(work_root=tmp_path, identity=identity):
            raise AssertionError("synthetic failure")
    with candidate_lock(work_root=tmp_path, identity=identity):
        pass


def test_candidate_stale_lock_fails_closed_without_network(tmp_path):
    env = _fixture(tmp_path)
    locked = _receipt_path(tmp_path, env["identity"]).with_suffix(".lock")
    locked.parent.mkdir(parents=True)
    locked.write_text("synthetic stale lock", encoding="utf-8")
    with pytest.raises(RuntimeError, match="locked"):
        with candidate_lock(work_root=tmp_path, identity=env["identity"]):
            pytest.fail("crash lock entered")
    assert locked.read_text(encoding="utf-8") == "synthetic stale lock"


def test_job_reads_receipt_before_creating_backend_or_running_live(tmp_path, monkeypatch):
    # Local-only integration: intercept every network/acquisition entry point.
    state = tmp_path / "old.json"
    state.write_text(
        json.dumps({"published_completed_window": {"code": "OLD"}}),
        encoding="utf-8",
    )
    target_plan = build_plan(
        catalog_path=ROOT / "data/reference/ptcg_live_windows.json",
        state=read_json(state),
        acquisition_started_at=datetime(2026, 9, 6, 10, tzinfo=UTC),
    )
    revision = "integration-synthetic"
    # Real job hashes shipped YAML/catalog, so build files in its namespace.
    identity = candidate_identity(
        window=target_plan["latest_completed_window"],
        config_path=ROOT / "config" / "tcg.yaml",
        catalog_path=ROOT / "data/reference/ptcg_live_windows.json",
        revision=revision,
    )
    env = _fixture(
        tmp_path, window=target_plan["latest_completed_window"],
        revision=revision, identity_override=identity,
    )
    _persist(env)
    monkeypatch.setattr(job, "_git_revision", lambda _: revision)

    def forbidden(*args, **kwargs):
        raise AssertionError("LIVE/backend must not execute on receipt reuse")

    monkeypatch.setattr(job, "run_limitless_api_acquisition", forbidden)
    report = job.run_job(
        repo_root=ROOT, work_root=tmp_path, state_path=state,
        acquisition_started_at=datetime(2026, 9, 6, 10, tzinfo=UTC),
        backend_factory=forbidden,
    )
    assert report["candidate_reused"] is True
    assert report["network_calls"] == 0
    assert report["published"] is False


def test_job_publish_does_not_reuse_unpublished_receipt(tmp_path, monkeypatch):
    state = tmp_path / "old.json"
    state.write_text(
        json.dumps({"published_completed_window": {"code": "OLD"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(job, "_git_revision", lambda _: "integration-synthetic")

    def forbidden(*args, **kwargs):
        raise AssertionError("LIVE must be separately authorized")

    monkeypatch.setattr(job, "run_limitless_api_acquisition", forbidden)
    with pytest.raises(AssertionError, match="LIVE must be"):
        job.run_job(
            repo_root=ROOT, work_root=tmp_path, state_path=state,
            acquisition_started_at=datetime(2026, 9, 6, 10, tzinfo=UTC),
            backend_factory=forbidden, publish=True,
        )


def test_b3r1_distinct_identities_have_disjoint_candidate_and_publisher_roots(tmp_path):
    first = _fixture(tmp_path, revision="revision-a")
    second = _fixture(tmp_path, revision="revision-b")
    a = candidate_workspace(work_root=tmp_path, identity=first["identity"])
    b = candidate_workspace(work_root=tmp_path, identity=second["identity"])
    publisher = candidate_workspace(
        work_root=tmp_path, identity=first["identity"], publish=True
    )
    assert len({a, b, publisher}) == 3
    assert not a.is_relative_to(b)
    assert not b.is_relative_to(a)
    assert publisher.parent.name == "publication-workspaces"
    assert a.parent.name == b.parent.name == "candidate-workspaces"
    assert all(path.is_relative_to(a) for path in (
        first["live_manifest"], first["replay_manifest"],
        first["source_run"], *first["outputs"].values(),
    ))
    assert all(path.is_relative_to(b) for path in (
        second["live_manifest"], second["replay_manifest"],
        second["source_run"], *second["outputs"].values(),
    ))


def test_b3r1_sequential_candidates_preserve_earlier_receipt_and_artifact_hashes(
    tmp_path,
):
    first = _fixture(tmp_path, revision="revision-a")
    receipt_a = _persist(first)
    original_a = receipt_a.read_bytes()
    original_output = first["outputs"]["mars_ranking"].read_bytes()

    # A different config/revision causes another legitimate run, including
    # the destructive cleanup/rebuild that used to clobber the common replay.
    second = _fixture(tmp_path, revision="revision-b")
    root_b = candidate_workspace(work_root=tmp_path, identity=second["identity"])
    replay_b = root_b / "raw-replay"
    shutil.rmtree(replay_b)
    second["replay_manifest"].parent.mkdir(parents=True)
    second["replay_manifest"].write_text(
        json.dumps({"run_id": "run-1"}) + "\n", encoding="utf-8"
    )
    second["outputs"]["mars_ranking"].write_text("other candidate", encoding="utf-8")
    _persist(second)
    assert load_verified_candidate(
        work_root=tmp_path, identity=second["identity"]
    )["candidate_ready"] is True

    assert first["outputs"]["mars_ranking"].read_bytes() == original_output
    assert receipt_a.read_bytes() == original_a
    assert first["replay_manifest"].exists()
    assert load_verified_candidate(
        work_root=tmp_path, identity=first["identity"]
    )["candidate_reused"] is True


def test_b3r1_same_code_different_boundaries_cannot_mutate_existing_candidate(
    tmp_path,
):
    first = _fixture(tmp_path)
    receipt = _persist(first)
    changed_window = dict(first["identity"]["window"])
    changed_window["start"] = "2026-07-16T18:00:00Z"
    second = _fixture(tmp_path, window=changed_window)
    assert candidate_workspace(
        work_root=tmp_path, identity=first["identity"]
    ) != candidate_workspace(work_root=tmp_path, identity=second["identity"])
    assert receipt.exists()
    assert load_verified_candidate(
        work_root=tmp_path, identity=first["identity"]
    )["canonical_live_run_id"] == "run-1"


def test_b3r1_receipt_refuses_foreign_identity_workspace(tmp_path):
    first = _fixture(tmp_path, revision="revision-a")
    second = _fixture(tmp_path, revision="revision-b")
    first["outputs"] = second["outputs"]
    with pytest.raises(ValueError, match="not inside its source run"):
        _persist(first)
    assert not _receipt_path(tmp_path, first["identity"]).exists()


def test_b3r1_independent_processes_do_not_collide_in_shared_work_root(tmp_path):
    env_a = _fixture(tmp_path, revision="process-a")
    env_b = _fixture(tmp_path, revision="process-b")
    child = (
        "import json, sys, time; from pathlib import Path; "
        "from scripts.tcg_live_candidate_receipt import candidate_lock, candidate_workspace; "
        "root=Path(sys.argv[1]); identity=json.loads(sys.argv[2]); "
        "tag=sys.argv[3]; "
        "w=candidate_workspace(work_root=root, identity=identity); "
        "w.mkdir(parents=True, exist_ok=True); "
        "p=w/'raw-replay'/'marker.txt'; p.parent.mkdir(parents=True, exist_ok=True); "
        "ctx=candidate_lock(work_root=root, identity=identity); "
        "ctx.__enter__(); "
        "p.write_text(tag, encoding='utf-8'); "
        "time.sleep(0.20); "
        "assert p.read_text(encoding='utf-8') == tag; "
        "ctx.__exit__(None, None, None)"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(tmp_path),
             json.dumps(env["identity"]), label],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for env, label in ((env_a, "a"), (env_b, "b"))
    ]
    for process in processes:
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            pytest.fail("synthetic concurrent candidate process timed out")
        assert process.returncode == 0, stderr or stdout
    assert candidate_workspace(
        work_root=tmp_path, identity=env_a["identity"]
    ) != candidate_workspace(work_root=tmp_path, identity=env_b["identity"])
