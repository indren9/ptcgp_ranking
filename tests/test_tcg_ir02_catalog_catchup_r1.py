"""IR-02 R1 candidate: offline catch-up regression; no network, no production writes."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.tcg_live_latest_completed import build_plan, read_json
from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot,
    resolve_release,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/reference/ptcg_live_windows.json"
PUBLIC_STATE = ROOT / ".github/tcg-live-latest-completed-meta-state.json"

BOUNDARIES = (
    ("CRI", "2026-05-21T17:00:00Z"),
    ("PBL", "2026-07-16T17:00:00Z"),
    ("30C", "2026-09-15T17:00:00Z"),
    ("DLR", "2026-11-05T17:00:00Z"),
)


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_exact_reviewed_chain_is_contiguous():
    snapshot = load_release_catalog_snapshot(CATALOG)
    assert [r.code for r in snapshot.releases] == [x[0] for x in BOUNDARIES]
    for i, (code, stamp) in enumerate(BOUNDARIES):
        row = snapshot.releases[i]
        assert row.code == code and row.release_datetime == _utc(stamp)
        expected_end = _utc(BOUNDARIES[i + 1][1]) if i + 1 < len(BOUNDARIES) else None
        assert row.next_release_datetime == expected_end


def test_dlr_boundary_is_half_open_and_completed_moves_to_30c():
    snapshot = load_release_catalog_snapshot(CATALOG)
    boundary = _utc("2026-11-05T17:00:00Z")
    just_before = boundary - timedelta(microseconds=1)
    assert resolve_release(snapshot, mode="auto", acquisition_started_at=just_before).code == "30C"
    assert resolve_release(snapshot, mode="latest_completed", acquisition_started_at=just_before).code == "PBL"
    assert resolve_release(snapshot, mode="auto", acquisition_started_at=boundary).code == "DLR"
    assert resolve_release(snapshot, mode="latest_completed", acquisition_started_at=boundary).code == "30C"


def test_corrected_window_requires_republication_even_same_code():
    old = read_json(PUBLIC_STATE)
    assert old["published_completed_window"]["code"] == "CRI"
    plan = build_plan(catalog_path=CATALOG, state=old,
                      acquisition_started_at=_utc("2026-09-06T10:00:00Z"))
    assert plan["latest_completed_window"]["code"] == "CRI"
    assert plan["latest_completed_window"]["start"] == BOUNDARIES[0][1]
    assert plan["action"] == "publish_required"
    corrected = build_plan(catalog_path=CATALOG, state=plan["state_after_publish"],
                           acquisition_started_at=_utc("2026-09-06T10:00:00Z"))
    assert corrected["action"] == "noop"


def test_completed_as_of_november_26_is_30c_not_current_dlr():
    plan = build_plan(catalog_path=CATALOG,
                      acquisition_started_at=_utc("2026-11-26T09:00:00Z"))
    assert plan["latest_completed_window"]["code"] == "30C"
    assert plan["latest_completed_window"]["end"] == BOUNDARIES[3][1]
    assert plan["action"] == "publish_required"


@pytest.mark.parametrize("field,changed_value", [
    ("code", "OTHER"),
    ("name", "Synthetic changed name"),
    ("start", "2026-09-15T17:00:01Z"),
    ("end", "2026-11-05T17:00:01Z"),
    ("catalog_version", "synthetic-different-catalog-version"),
])
def test_any_single_completed_identity_change_requires_publication(field, changed_value):
    instant = _utc("2026-11-26T09:00:00Z")
    base = build_plan(catalog_path=CATALOG, state=None, acquisition_started_at=instant)
    expected = base["latest_completed_window"]
    assert expected["code"] == "30C"
    altered = dict(expected)
    altered[field] = changed_value
    assert altered[field] != expected[field]
    state = {"published_completed_window": altered}
    next_plan = build_plan(catalog_path=CATALOG, state=state, acquisition_started_at=instant)
    assert next_plan["action"] == "publish_required"
    assert next_plan["latest_completed_window"] == expected
    assert state["published_completed_window"] == altered
    assert build_plan(
        catalog_path=CATALOG,
        state=base["state_after_publish"],
        acquisition_started_at=instant,
    )["action"] == "noop"


@pytest.mark.parametrize("idx", [0, 1, 2], ids=["CRI", "PBL", "30C"])
@pytest.mark.parametrize("moment", ["midnight", "just_before", "at_1700"])
def test_all_pre_dlr_boundaries_inclusive_exclusive(idx, moment):
    snapshot = load_release_catalog_snapshot(CATALOG)
    boundary = _utc(BOUNDARIES[idx][1])
    instant = (
        boundary.replace(hour=0, minute=0, second=0)
        if moment == "midnight"
        else boundary - timedelta(microseconds=1)
        if moment == "just_before"
        else boundary
    )
    # No completed window exists before PBL starts; CRI itself is not complete.
    current_index = idx if moment == "at_1700" else idx - 1
    completed_index = current_index - 1
    if current_index < 0:
        with pytest.raises(ValueError, match="no released expansion"):
            resolve_release(snapshot, mode="auto", acquisition_started_at=instant)
    else:
        assert resolve_release(snapshot, mode="auto", acquisition_started_at=instant).code == BOUNDARIES[current_index][0]
    if completed_index < 0:
        with pytest.raises(ValueError, match="no completed release window"):
            resolve_release(snapshot, mode="latest_completed", acquisition_started_at=instant)
    else:
        assert resolve_release(snapshot, mode="latest_completed", acquisition_started_at=instant).code == BOUNDARIES[completed_index][0]
