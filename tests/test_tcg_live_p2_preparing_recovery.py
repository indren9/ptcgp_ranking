"""WP3 isolated PREPARING regression. No LIVE, no OneDrive, no network."""
from __future__ import annotations
import json
import pytest
from storage import tcg_private_guarded as g


def _fixture(tmp_path, monkeypatch):
    target = tmp_path / "meta" / "PTCG"
    target.mkdir(parents=True)
    src = tmp_path / "candidate"
    src.mkdir()
    old = {f: ("old-" + f).encode() for f in g.FILES}
    for f in g.FILES:
        (target / f).write_bytes(old[f])
        (src / f).write_bytes(("new-" + f).encode())
    monkeypatch.setattr(g, "canonical_game_root", lambda *_a, **_k: target)
    monkeypatch.setattr(g, "validate_bundle", lambda *_a, **_k: {"game": "PTCG", "format": "standard"})
    monkeypatch.setattr(g, "_verified_private_manifest", lambda *_a, **_k: {"game": "PTCG"})
    # Legacy test uses deliberately invalid byte fixtures; precondition is
    # validated with real manifests in the IR-05/08 security suite.
    monkeypatch.setattr(g, "_check_private_precondition", lambda **_kw: None)
    return src, target, old


def _transaction(target):
    found = [p for p in g._ledger(target).iterdir() if p.is_dir()]
    assert len(found) == 1
    return found[0]


def _publish(src, callback=None):
    return g.publish_private_bundle_guarded(
        bundle_dir=src,
        plan={"latest_completed_window": {"code": "DLR"}},
        failure_injector=callback,
    )


def test_after_stage_exception_aborted_and_next_attempt(tmp_path, monkeypatch):
    src, target, old = _fixture(tmp_path, monkeypatch)
    def inject(phase, name):
        if phase == "after_stage" and name == "ranking.csv":
            raise RuntimeError("stage fault")
    with pytest.raises(RuntimeError, match="stage fault"):
        _publish(src, inject)
    txn = _transaction(target)
    assert json.loads((txn / "journal.json").read_text())["status"] == "ABORTED"
    assert not g._unresolved(g._ledger(target))
    assert all((target / f).read_bytes() == old[f] for f in g.FILES)
    assert _publish(src).status == "PASS"


def test_hard_crash_preparing_recovery(tmp_path, monkeypatch):
    src, target, old = _fixture(tmp_path, monkeypatch)
    class SimulatedCrash(BaseException):
        pass
    def inject(phase, name):
        if phase == "after_stage" and name == "ranking.csv":
            raise SimulatedCrash()
    with pytest.raises(SimulatedCrash):
        _publish(src, inject)
    txn = _transaction(target)
    assert json.loads((txn / "journal.json").read_text())["status"] == "PREPARING"
    assert g.recover_pending(transaction_id=txn.name) == "ABORTED"
    assert not g._unresolved(g._ledger(target))
    assert all((target / f).read_bytes() == old[f] for f in g.FILES)


def test_preparing_concurrent_change_blocks_recovery(tmp_path, monkeypatch):
    src, target, old = _fixture(tmp_path, monkeypatch)
    class SimulatedCrash(BaseException):
        pass
    def inject(phase, name):
        if phase == "after_stage" and name == "ranking.csv":
            raise SimulatedCrash()
    with pytest.raises(SimulatedCrash):
        _publish(src, inject)
    txn = _transaction(target)
    (target / "ranking.csv").write_bytes(b"modified externally")
    with pytest.raises(g.PrivatePublicationError, match="PREPARING target changed"):
        g.recover_pending(transaction_id=txn.name)
    assert g._unresolved(g._ledger(target)) == [txn]


def test_bootstrap_failure_cleans_unjournaled_directory(tmp_path, monkeypatch):
    src, target, old = _fixture(tmp_path, monkeypatch)
    original = g._write_json
    def fail_initial(path, payload):
        if payload.get("status") == "PREPARING":
            raise OSError("journal write failed")
        return original(path, payload)
    monkeypatch.setattr(g, "_write_json", fail_initial)
    with pytest.raises(OSError, match="journal write failed"):
        _publish(src)
    assert not g._unresolved(g._ledger(target))
    assert all((target / f).read_bytes() == old[f] for f in g.FILES)


def test_preparing_recovery_refuses_active_writer(tmp_path, monkeypatch):
    src, target, old = _fixture(tmp_path, monkeypatch)
    class SimulatedCrash(BaseException):
        pass
    def inject(phase, name):
        if phase == "after_stage" and name == "ranking.csv":
            raise SimulatedCrash()
    with pytest.raises(SimulatedCrash):
        _publish(src, inject)
    txn = _transaction(target)
    (g._ledger(target) / ".writer-lock").write_text("active")
    with pytest.raises(g.PrivatePublicationError, match="writer lock"):
        g.recover_pending(transaction_id=txn.name)
