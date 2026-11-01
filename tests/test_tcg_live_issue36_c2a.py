"""Issue #36 C.2A: canonical 17:00 boundaries, no deployment or network."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import socket

import pytest

from acquisition.scope import EligibilityPolicy
from acquisition.selection import select_tournaments
from scripts.tcg_live_candidate_receipt import candidate_identity, load_verified_candidate
from scripts.tcg_live_latest_completed import build_plan, read_json
from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot, resolve_release, scope_for_release,
)
import test_tcg_live_issue36_b3 as b3

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data/reference/ptcg_live_windows.json"
POST_PROPOSAL = ROOT / "data/candidates/releases/tcg_live_windows_issue36_c2a_post_dlr_proposal.json"
STATE = ROOT / ".github/tcg-live-latest-completed-meta-state.json"
VERSION = "tcgl-expansion-boundaries-2026-11-01-issue36-c2a-pre-dlr-v1"
MICROSECOND = timedelta(microseconds=1)


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("C.2A tests must remain offline")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def post_catalog(tmp_path):
    # Explicit extraction is TEST-ONLY, never a runtime activation route.
    envelope = json.loads(POST_PROPOSAL.read_text(encoding="utf-8"))
    path = tmp_path / "synthetic-post-dlr.json"
    path.write_text(json.dumps(envelope["proposed_catalog"]), encoding="utf-8")
    return path


def test_canonical_pre_dlr_chain_and_provenance():
    payload = json.loads(CANONICAL.read_text(encoding="utf-8"))
    catalog = load_release_catalog_snapshot(CANONICAL)
    assert catalog.catalog_version == VERSION
    assert [r.code for r in catalog.releases] == ["CRI", "PBL", "30C"]
    assert [r.release_datetime for r in catalog.releases] == [
        dt("2026-05-21T17:00:00Z"), dt("2026-07-16T17:00:00Z"),
        dt("2026-09-15T17:00:00Z"),
    ]
    assert [r.code for r in catalog.releases if r.is_current] == ["30C"]
    assert catalog.releases[-1].next_release_datetime is None
    for left, right in zip(catalog.releases, catalog.releases[1:]):
        assert left.next_release_datetime == right.release_datetime
    provenance = payload["candidate_provenance"]
    assert provenance["c1_archive_sha256"] == "c06d855104a4348821cb9e37e391288d1c29f12ee690431201b23d2575d71099"
    assert provenance["supporting_approved_historical_catalog"]["unchanged"] is True
    assert {e["code"] for e in provenance["evidence"]} == {"CRI", "PBL", "30C"}
    assert all(e["source_url"].startswith("https://community.pokemon.com/")
               for e in provenance["evidence"])
    assert payload["boundary_policy"] == {
        "version": "ptcg-live-v1", "full_expansion_availability": True,
        "standard_rotation": True, "early_individual_card_availability": False,
    }


@pytest.mark.parametrize("when", [
    "2026-11-01T18:00:00Z", "2026-11-05T00:00:00Z",
    "2026-11-05T16:59:59.999999Z", "2026-11-05T17:00:00Z",
    "2026-11-05T17:00:00.000001Z", "2026-11-06T18:00:00Z",
])
def test_pre_dlr_does_not_automatically_activate_future_release(when):
    catalog = load_release_catalog_snapshot(CANONICAL)
    assert resolve_release(catalog, mode="auto", acquisition_started_at=dt(when)).code == "30C"
    assert build_plan(catalog_path=CANONICAL, acquisition_started_at=dt(when))[
        "latest_completed_window"]["code"] == "PBL"


def test_post_dlr_envelope_is_rejected_by_unmodified_runtime_loader():
    envelope = json.loads(POST_PROPOSAL.read_text(encoding="utf-8"))
    assert "releases" not in envelope
    assert envelope["runtime_loadable"] is False
    assert envelope["automatic_activation"] is False
    assert envelope["authorization_required"] is True
    assert envelope["earliest_deployment_utc"] == "2026-11-05T17:00:00Z"
    proposed = envelope["proposed_catalog"]
    # Preserve C.1 payload semantics and identity; metadata is not enforcement.
    encoded = (json.dumps(proposed, ensure_ascii=False, indent=2) + "\n").encode()
    assert hashlib.sha256(encoded).hexdigest() == envelope["c1_payload_sha256"]
    with pytest.raises(ValueError, match="requires a releases array"):
        load_release_catalog_snapshot(POST_PROPOSAL)
    with pytest.raises(ValueError, match="requires a releases array"):
        build_plan(catalog_path=POST_PROPOSAL, acquisition_started_at=dt("2026-11-01T18:00:00Z"))


@pytest.mark.parametrize("code", ["CRI", "PBL", "30C", "DLR"])
def test_tournament_membership_at_midnight_and_microsecond_edges(post_catalog, code):
    catalog = load_release_catalog_snapshot(post_catalog)
    release = next(r for r in catalog.releases if r.code == code)
    scope = scope_for_release(
        release, acquisition_started_at=dt("2026-11-06T18:00:00Z"),
        game="PTCG", format="STANDARD", policy_id="ptcg_tcgl_expansion_window_v1",
    )
    start, end = scope.start_datetime, scope.end_datetime
    times = {
        "start-midnight": start.replace(hour=0), "before-start": start - MICROSECOND,
        "at-start": start, "after-start": start + MICROSECOND,
        "end-midnight": end.replace(hour=0), "before-end": end - MICROSECOND,
        "at-end": end, "after-end": end + MICROSECOND,
    }
    records = [
        dict(tournament_id=key, date=value, game="PTCG", format="STANDARD",
             is_public=True, is_online=True, platform="PTCGL", decklists=True)
        for key, value in times.items()
    ]
    policy = EligibilityPolicy(
        policy_id="ptcg_live_standard_v1", game="PTCG",
        allowed_formats=("STANDARD",), require_online=True, allowed_platforms=("PTCGL",),
    )
    result = select_tournaments(records, scope=scope, eligibility=policy)
    assert result.tournament_ids == ("after-start", "at-start", "before-end", "end-midnight")
    assert result.exclusion_counts["outside_window"] == 4
    assert select_tournaments(reversed(records), scope=scope, eligibility=policy) == result


@pytest.mark.parametrize("boundary,before,after", [
    ("2026-07-16T17:00:00Z", None, "CRI"),
    ("2026-09-15T17:00:00Z", "CRI", "PBL"),
])
def test_pre_dlr_planner_midnight_and_exact_completed_boundary(boundary, before, after):
    edge = dt(boundary)
    for when in [edge.replace(hour=0), edge - MICROSECOND]:
        if before is None:
            with pytest.raises(ValueError, match="no completed release"):
                build_plan(catalog_path=CANONICAL, acquisition_started_at=when)
        else:
            assert build_plan(catalog_path=CANONICAL, acquisition_started_at=when)[
                "latest_completed_window"]["code"] == before
    for when in [edge, edge + MICROSECOND]:
        assert build_plan(catalog_path=CANONICAL, acquisition_started_at=when)[
            "latest_completed_window"]["code"] == after


@pytest.mark.parametrize("delta,expected", [(-1, "PBL"), (0, "30C"), (1, "30C")])
def test_synthetic_post_dlr_planner_boundary(post_catalog, delta, expected):
    when = dt("2026-11-05T17:00:00Z") + delta * MICROSECOND
    assert build_plan(catalog_path=post_catalog, acquisition_started_at=when)[
        "latest_completed_window"]["code"] == expected


def test_preserved_public_state_is_stale_and_planning_does_not_write():
    before = STATE.read_bytes()
    state = read_json(STATE)
    for when, code in [("2026-09-06T10:00:00Z", "CRI"), ("2026-11-01T18:00:00Z", "PBL")]:
        plan = build_plan(catalog_path=CANONICAL, state=state, acquisition_started_at=dt(when))
        assert plan["action"] == "publish_required"
        assert plan["latest_completed_window"]["code"] == code
        assert STATE.read_bytes() == before
        fresh = build_plan(catalog_path=CANONICAL, state=plan["state_after_publish"],
                           acquisition_started_at=dt(when))
        assert fresh["action"] == "noop"


@pytest.mark.parametrize("key", ["code", "name", "start", "end", "catalog_version"])
def test_canonical_receipt_rejects_each_changed_window_identity(tmp_path, monkeypatch, key):
    monkeypatch.setattr(b3, "CANDIDATE", CANONICAL)
    env = b3._fixture(tmp_path)
    b3._persist(env)
    window = dict(env["identity"]["window"])
    window[key] = "different"
    changed = candidate_identity(window=window, config_path=env["config"],
                                 catalog_path=env["catalog"], revision="synthetic-revision")
    assert changed != env["identity"]
    assert load_verified_candidate(work_root=tmp_path, identity=changed) is None
    assert load_verified_candidate(work_root=tmp_path, identity=env["identity"])["network_calls"] == 0


def test_canonical_catalog_bytes_invalidate_receipt_even_without_semantic_change(tmp_path, monkeypatch):
    monkeypatch.setattr(b3, "CANDIDATE", CANONICAL)
    env = b3._fixture(tmp_path)
    receipt = b3._persist(env)
    receipt_before = receipt.read_bytes()
    env["catalog"].write_bytes(env["catalog"].read_bytes() + b"\n")
    changed = candidate_identity(
        window=env["identity"]["window"], config_path=env["config"],
        catalog_path=env["catalog"], revision="synthetic-revision",
    )
    assert changed != env["identity"]
    assert load_verified_candidate(work_root=tmp_path, identity=changed) is None
    assert receipt.read_bytes() == receipt_before
