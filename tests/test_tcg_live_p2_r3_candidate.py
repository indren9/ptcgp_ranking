"""WP3 TEST CANDIDATES ONLY — written in WP2, deliberately NOT EXECUTED.

Add these tests to an isolated checkout after WP3 test authorization.
End-to-end Tournament API fixtures, real observer receipts and hard-crash
recovery remain mandatory beyond this small suite.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from scripts.tcg_live_provisional_current import (
    ProvisionalGateError,
    staging_catalog,
)
from scripts.tcg_live_identity_bind import (
    exact_catalog_match,
    ProvisionalGateError as BindError,
)
from storage import tcg_private_guarded as guarded


def _catalog():
    return {
        "source": "Official TCG Live announcements",
        "catalog_version": "old-v1",
        "releases": [{
            "code": "DLR", "name": "Delta Reign Live window",
            "release_datetime": "2026-11-05T17:00:00Z",
            "next_release_datetime": None,
            "is_current": True,
            "source": "Official TCG Live announcements",
            "catalog_version": "old-v1",
        }],
    }


def _event():
    return {
        "temporary_code": "TMP-PTCGL-ABCDEF123456",
        "official_name": "Next Expansion",
        "start": "2027-01-01T17:00:00Z",
        "base_catalog_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
    }


def test_stage_is_sidecar_and_preserves_parent():
    parent = _catalog()
    frozen = json.dumps(parent, sort_keys=True)
    staged = staging_catalog(parent, _event())
    assert json.dumps(parent, sort_keys=True) == frozen
    assert staged["releases"][-2]["next_release_datetime"] == _event()["start"]
    assert staged["releases"][-1]["code"].startswith("TMP-PTCGL-")
    assert staged["staging_only"]["code_is_official"] is False
    assert staged["staging_only"]["never_publish"] is True


def test_staging_refuses_invalid_boundary_and_collision():
    event = _event()
    event["start"] = "2026-11-05T17:00:00Z"
    with pytest.raises(ProvisionalGateError):
        staging_catalog(_catalog(), event)
    event = _event()
    event["temporary_code"] = "DLR"
    with pytest.raises(ProvisionalGateError):
        staging_catalog(_catalog(), event)


def test_limitless_catalog_exact_name_code_only():
    html = '''<table><tr><td><a href="/cards/XYZ">Next Expansion XYZ</a></td></tr></table>'''
    assert exact_catalog_match(html, "Next Expansion")[0] == "XYZ"
    with pytest.raises(BindError):
        exact_catalog_match(html, "Unrelated Expansion")
    ambiguous = html + '<a href="/cards/ABC">Next Expansion ABC</a>'
    with pytest.raises(BindError):
        exact_catalog_match(ambiguous, "Next Expansion")


def test_private_publisher_failure_restores_prior_bundle(tmp_path, monkeypatch):
    target = tmp_path / "root" / "PTCG"
    target.mkdir(parents=True)
    previous = {}
    for name in guarded.FILES:
        previous[name] = ("previous-" + name).encode()
        (target / name).write_bytes(previous[name])
    src = tmp_path / "candidate"
    src.mkdir()
    for name in guarded.FILES:
        (src / name).write_bytes(("new-" + name).encode())
    monkeypatch.setattr(guarded, "canonical_game_root", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(guarded, "validate_bundle", lambda *_args, **_kwargs: {"game": "PTCG", "format": "standard"})
    monkeypatch.setattr(guarded, "_verified_private_manifest", lambda *_args, **_kwargs: {"game": "PTCG"})
    # This is a rollback-byte fixture, not a validated publication-plan test.
    monkeypatch.setattr(guarded, "_check_private_precondition", lambda **_kw: None)

    def inject(phase, name):
        if phase == "after_replace" and name == "ranking.csv":
            raise RuntimeError("simulated replace interruption")

    with pytest.raises(RuntimeError):
        guarded.publish_private_bundle_guarded(
            bundle_dir=src,
            plan={"latest_completed_window": {"code": "DLR"}},
            failure_injector=inject,
        )
    for name in guarded.FILES:
        assert (target / name).read_bytes() == previous[name]
    # Backups remain available for later verification; no auto-cleanup.
    ledger = target.parent / ".tcg-live-p2-private-history"
    assert len([p for p in ledger.iterdir() if p.is_dir()]) == 1


# Additional integration/failure scenarios remain WP3 scope:
# - certified observer candidate integrity and parser revisions
# - no API data / first eligible tournament / invalid platform / out-of-window
# - offline replay exact hash/IDs with zero network
# - TMP current stage never writes canonical outputs, public GitHub, OneDrive
# - mapping corrected after prior staged run; preserve old manifests
# - same-code changed timestamp blocks silent completed NOOP
# - hard crash between copy/replace and journal COMMITTED, explicit recovery
# - concurrent invocations / stale writer lock / consumer-side hash mismatch
