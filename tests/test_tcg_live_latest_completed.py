from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from scripts.tcg_live_latest_completed import (
    build_plan,
    read_json,
)


ROOT = Path(__file__).resolve().parents[1]

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


def test_frozen_state_resolves_to_noop():
    state = read_json(STATE)

    plan = build_plan(
        catalog_path=CATALOG,
        state=state,
        acquisition_started_at=STARTED,
    )

    assert plan["action"] == "noop"

    window = plan["latest_completed_window"]

    assert window["code"] == "CRI"
    assert window["name"] == "Chaos Rising Live window"

    assert window["start"] == (
        "2026-05-21T00:00:00Z"
    )

    assert window["end"] == (
        "2026-07-16T00:00:00Z"
    )


def test_missing_state_requires_publication():
    plan = build_plan(
        catalog_path=CATALOG,
        state=None,
        acquisition_started_at=STARTED,
    )

    assert plan["action"] == "publish_required"
    assert plan["reason"] == "no_published_state"
    assert (
        plan["state_after_publish"]
        ["published_completed_window"]
        ["code"]
        == "CRI"
    )


def test_previous_window_change_requires_publication():
    state = {
        "published_completed_window": {
            "code": "OLD",
        }
    }

    plan = build_plan(
        catalog_path=CATALOG,
        state=state,
        acquisition_started_at=STARTED,
    )

    assert plan["action"] == "publish_required"
    assert plan["reason"] == "latest_completed_changed"


def test_planner_is_tcg_live_only():
    state = read_json(STATE)

    plan = build_plan(
        catalog_path=CATALOG,
        state=state,
        acquisition_started_at=STARTED,
    )

    assert plan["game"] == "PTCG"
    assert plan["format"] == "STANDARD"
    assert plan["platform"] == "PTCGL"


def test_state_json_matches_frozen_window():
    payload = json.loads(
        STATE.read_text(encoding="utf-8")
    )

    window = payload[
        "published_completed_window"
    ]

    assert window == {
        "code": "CRI",
        "name": "Chaos Rising Live window",
        "start": "2026-05-21T00:00:00Z",
        "end": "2026-07-16T00:00:00Z",
        "catalog_version":
            "tcgl-expansion-boundaries-2026-09-04-v1",
    }
