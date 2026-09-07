from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import yaml

from scripts.tcg_live_latest_completed_job import (
    _eligibility,
    _load_config,
    _scope,
    _write_offline_config,
    run_job,
)
from scripts.tcg_live_latest_completed import (
    build_plan,
    read_json,
)


ROOT = Path(__file__).resolve().parents[1]

CONFIG = ROOT / "config" / "tcg.yaml"

CATALOG = (
    ROOT
    / "data"
    / "reference"
    / "ptcg_live_windows.json"
)

STATE = (
    ROOT
    / ".github"
    / "tcg-live-latest-completed-meta-state.json"
)

STARTED = datetime(
    2026,
    9,
    6,
    10,
    0,
    tzinfo=UTC,
)


def test_job_noop_never_initializes_backend(
    tmp_path,
):
    called = False

    def backend_factory():
        nonlocal called
        called = True
        raise AssertionError(
            "backend must not be created on NOOP"
        )

    report = run_job(
        repo_root=ROOT,
        work_root=tmp_path,
        acquisition_started_at=STARTED,
        backend_factory=backend_factory,
    )

    assert report["action"] == "noop"
    assert report["network_calls"] == 0
    assert report["published"] is False
    assert called is False


def test_scope_matches_frozen_cri_window():
    cfg = _load_config(CONFIG)
    api = cfg["source"]["tournament_api"]

    plan = build_plan(
        catalog_path=CATALOG,
        state=read_json(STATE),
        acquisition_started_at=STARTED,
    )

    scope = _scope(plan, api)

    assert scope.policy_id == (
        "ptcg_tcgl_expansion_window_v1"
    )
    assert scope.game == "PTCG"
    assert scope.format == "STANDARD"
    assert scope.set_code == "CRI"
    assert scope.start_datetime.isoformat() == (
        "2026-05-21T00:00:00+00:00"
    )
    assert scope.end_datetime.isoformat() == (
        "2026-07-16T00:00:00+00:00"
    )


def test_eligibility_is_live_only():
    cfg = _load_config(CONFIG)
    api = cfg["source"]["tournament_api"]

    policy = _eligibility(api)

    assert policy.game == "PTCG"
    assert policy.allowed_formats == (
        "STANDARD",
    )
    assert policy.require_public is True
    assert policy.require_decklists is True
    assert policy.require_online is True
    assert policy.allowed_platforms == (
        "PTCGL",
    )


def test_offline_config_pins_exact_window(
    tmp_path,
):
    destination = tmp_path / "offline.yaml"

    _write_offline_config(
        source_config=CONFIG,
        destination=destination,
        raw_root=tmp_path / "raw",
        cache_root=tmp_path / "cache",
        output_root=tmp_path / "outputs",
        release_catalog=CATALOG,
        replay_run_id="run-123",
        completed_code="CRI",
    )

    payload = yaml.safe_load(
        destination.read_text(encoding="utf-8")
    )

    api = payload["source"]["tournament_api"]

    assert api["execution_mode"] == "offline"
    assert api["replay_run_id"] == "run-123"
    assert api["window"] == {
        "mode": "code",
        "code": "CRI",
    }
    assert api["cache_ttl_min"] == 0


def test_old_state_requests_candidate():
    old_state = {
        "published_completed_window": {
            "code": "OLD",
        }
    }

    plan = build_plan(
        catalog_path=CATALOG,
        state=old_state,
        acquisition_started_at=STARTED,
    )

    assert plan["action"] == "publish_required"
    assert (
        plan["latest_completed_window"]["code"]
        == "CRI"
    )
