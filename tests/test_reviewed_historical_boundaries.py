"""R4: source-bound final approval and persisted D1 compatibility, offline."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import socket

import pytest

from historical_boundaries import catalog, reviewed
from historical_boundaries.human_adjudication import APPROVED_STARTS
from historical_rebuild.model import load_windows, utc

EXPECTED_CODES = "SVI PAL OBF MEW PAR PAF TEF TWM SFA SCR SSP PRE JTG DRI BLK WHT MEG PFL ASC POR CRI PBL 30C".split()


@pytest.fixture(autouse=True)
def no_network_research_or_rebuild(monkeypatch):
    import requests
    import urllib.request
    from historical_boundaries import targeted_fallback
    from historical_rebuild import __main__ as runner_cli
    from historical_rebuild.runner import Runner
    from historical_rebuild.production import ProductionBackend

    def forbidden(*args, **kwargs):
        raise AssertionError("R4 cannot access the network, reinterpret evidence or execute/publish a rebuild")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(targeted_fallback, "run_fallback", forbidden)
    monkeypatch.setattr(catalog, "compile_catalog", forbidden)
    monkeypatch.setattr(Runner, "run", forbidden)
    monkeypatch.setattr(runner_cli, "main", forbidden)
    monkeypatch.setattr(ProductionBackend, "__init__", forbidden)


@pytest.fixture
def sources():
    return dict(candidate_raw=catalog.CANDIDATE.read_bytes(),
                adjudications_raw=catalog.HUMAN_ADJUDICATIONS.read_bytes(),
                ledger_raw=catalog.LEDGER.read_bytes(),
                approval_raw=reviewed.APPROVAL.read_bytes())


def encode(value):
    return catalog.serialize(value).encode("utf-8")


def test_review_is_separate_and_changes_only_approved_flags(sources):
    original = deepcopy(sources)
    result = reviewed.build_reviewed(**sources)
    candidate = json.loads(sources["candidate_raw"])
    assert sources == original
    assert candidate["reviewed"] is False
    assert candidate["catalog_status"] == "COMPLETE_UNREVIEWED"
    assert result["reviewed"] is True
    assert result["schema_version"] == 1 and result["window_unit"] == "expansion"
    assert result["catalog_status"] == "COMPLETE_REVIEWED"
    assert [b["event_id"] for b in result["boundaries"]] == EXPECTED_CODES
    assert Counter(b["evidence"]["status"] for b in result["boundaries"]) == {
        "MACHINE_VERIFIED": 20, "HUMAN_ADJUDICATED": 3}
    for source, promoted in zip(candidate["boundaries"], result["boundaries"], strict=True):
        assert source["evidence"]["reviewed"] is False
        assert promoted["evidence"]["reviewed"] is True
        expected = deepcopy(source)
        expected["evidence"]["reviewed"] = True
        assert promoted == expected
        assert promoted["kind"] == "expansion" and promoted["reasons"] == ["expansion"]
        assert promoted["start_utc"] is not None
    assert "observation_cutoff_utc" not in result


def test_final_approval_metadata_and_all_source_digests(sources):
    result = reviewed.build_reviewed(**sources)
    review = result["review"]
    assert review["status"] == "HUMAN_APPROVED" and review["frozen"] is True
    assert review["gate"] == "GATE_1_11_D2_FINAL_CATALOG_REVIEW"
    assert review["approval_source"] == "Chat Madre/user"
    assert review["approval_statement"] == "Approvo il catalogo storico completo TCG Live."
    assert review["approval_policy_version"] == "tcg-live-frozen-catalog-1"
    assert review["source_revision"] == "a7de0ca47ed42ed14a6f00557bbb3ff3c0c95e31"
    for source, field in (("candidate_raw", "source_candidate_sha256"),
                          ("adjudications_raw", "human_adjudications_sha256"),
                          ("ledger_raw", "evidence_ledger_sha256")):
        assert review[field] == hashlib.sha256(sources[source]).hexdigest()
    assert result["generation"]["version"] == reviewed.GENERATOR_VERSION
    assert result["generation"]["parser_version"] == "live-release-2"


@pytest.mark.parametrize("code,start", APPROVED_STARTS.items())
def test_human_timing_and_machine_conflicts_remain_distinct(sources, code, start):
    result = reviewed.build_reviewed(**sources)
    boundary = next(b for b in result["boundaries"] if b["event_id"] == code)
    decision = next(d for d in json.loads(sources["adjudications_raw"])["decisions"] if d["event_id"] == code)
    assert boundary["start_utc"] == decision["approved_canonical_utc"] == start
    assert boundary["evidence"]["status"] == "HUMAN_ADJUDICATED"
    assert boundary["evidence"]["adjudication_reviewed"] is True
    assert boundary["evidence"]["adjudication_id"] == decision["adjudication_id"]
    assert boundary["evidence"]["machine_status"] == decision["machine_catalog_status"]
    assert decision["historical_pattern_used"] is False
    assert decision["limitless_used_for_time"] is False
    assert decision["conflicting_fields"]
    assert json.loads(sources["adjudications_raw"])["catalog_reviewed"] is False


@pytest.mark.parametrize("code", EXPECTED_CODES)
def test_changing_any_approved_timestamp_fails_generation(sources, code):
    candidate = json.loads(sources["candidate_raw"])
    next(b for b in candidate["boundaries"] if b["event_id"] == code)["start_utc"] = "2026-09-16T18:00:00Z"
    sources["candidate_raw"] = encode(candidate)
    with pytest.raises(ValueError, match="source candidate SHA-256"):
        reviewed.build_reviewed(**sources)


@pytest.mark.parametrize("source", ["candidate_raw", "adjudications_raw", "ledger_raw"])
def test_even_byte_only_source_changes_require_new_approval(sources, source):
    sources[source] += b"\n"
    with pytest.raises(ValueError, match="SHA-256"):
        reviewed.build_reviewed(**sources)


@pytest.mark.parametrize("field", ["source_candidate_sha256", "human_adjudications_sha256", "evidence_ledger_sha256"])
def test_changing_approval_digest_cannot_authorize_future_inputs(sources, field):
    approval = json.loads(sources["approval_raw"])
    approval[field] = "0" * 64
    sources["approval_raw"] = encode(approval)
    with pytest.raises(ValueError, match="final catalog approval SHA-256"):
        reviewed.build_reviewed(**sources)


def test_rebinding_changed_candidate_to_new_hash_still_fails(sources):
    candidate = json.loads(sources["candidate_raw"])
    candidate["boundaries"][0]["start_utc"] = "2023-03-30T18:00:00Z"
    sources["candidate_raw"] = encode(candidate)
    approval = json.loads(sources["approval_raw"])
    approval["source_candidate_sha256"] = hashlib.sha256(sources["candidate_raw"]).hexdigest()
    sources["approval_raw"] = encode(approval)
    with pytest.raises(ValueError, match="final catalog approval SHA-256"):
        reviewed.build_reviewed(**sources)


@pytest.mark.parametrize("code", APPROVED_STARTS)
@pytest.mark.parametrize("change", ["remove_flag", "deny", "remove_decision", "timestamp"])
def test_changed_register_or_missing_individual_approval_fails(sources, code, change):
    register = json.loads(sources["adjudications_raw"])
    decision = next(d for d in register["decisions"] if d["event_id"] == code)
    if change == "remove_flag":
        decision.pop("adjudication_reviewed")
    elif change == "deny":
        decision["adjudication_reviewed"] = False
    elif change == "remove_decision":
        register["decisions"].remove(decision)
    else:
        decision["approved_canonical_utc"] = "2024-01-25T18:00:00Z"
    sources["adjudications_raw"] = encode(register)
    with pytest.raises(ValueError, match="human adjudications SHA-256"):
        reviewed.build_reviewed(**sources)


@pytest.mark.parametrize("field", ["status", "gate", "approval_source", "approval_statement", "approval_policy_version", "frozen"])
def test_missing_final_approval_metadata_fails(sources, field):
    approval = json.loads(sources["approval_raw"])
    approval.pop(field)
    sources["approval_raw"] = encode(approval)
    with pytest.raises(ValueError, match="final catalog approval SHA-256"):
        reviewed.build_reviewed(**sources)


def test_missing_approval_file_fails_before_any_write(monkeypatch, tmp_path):
    monkeypatch.setattr(reviewed, "APPROVAL", tmp_path / "missing-approval.json")
    output = tmp_path / "reviewed.json"
    monkeypatch.setattr(reviewed, "REVIEWED", output)
    with pytest.raises(FileNotFoundError):
        reviewed.main(["--write"])
    assert not output.exists()


def test_d1_accepts_persisted_artifact_directly_and_groups_22_windows():
    raw = reviewed.REVIEWED.read_bytes()
    payload = json.loads(raw)
    before = deepcopy(payload)
    windows = load_windows(payload)
    assert payload == before and reviewed.REVIEWED.read_bytes() == raw
    assert len(windows) == len({b["start_utc"] for b in payload["boundaries"]}) == 22
    assert sum(len(w.events) for w in windows) == 23
    assert all(w.end_utc == following.start_utc for w, following in zip(windows, windows[1:]))
    assert all(w.end_utc is None or utc(w.end_utc) > utc(w.start_utc) for w in windows)
    joint = next(w for w in windows if w.start_utc == "2025-07-17T17:00:00Z")
    assert [e["event_id"] for e in joint.events] == ["BLK", "WHT"]
    assert joint.end_utc == "2025-09-25T17:00:00Z"
    assert all(e["kind"] == "expansion" for w in windows for e in (*w.events, *w.end_events))
    assert windows[-1].window_id == "30C"
    assert windows[-1].start_utc == "2026-09-15T17:00:00Z"
    assert windows[-1].end_utc is None
    assert all(w.observation_cutoff_utc is None for w in windows)
    assert reviewed.validate_persisted(reviewed.reviewed_bytes()) == windows


def test_offline_cli_deterministic_regeneration_and_immutable_output(monkeypatch, tmp_path):
    paths = [catalog.CANDIDATE, catalog.LEDGER, catalog.HUMAN_ADJUDICATIONS, reviewed.APPROVAL,
             catalog.BASE / "data/reference/ptcg_live_windows.json",
             catalog.BASE / "data/reference/pocket_releases.json",
             catalog.BASE / ".github/tcg-live-latest-completed-meta-state.json",
             catalog.BASE / "requirements.txt",
             catalog.BASE / ".github/workflows/update-expansion-catalog.yml"]
    before = {p: p.read_bytes() for p in paths}
    committed = reviewed.REVIEWED.read_bytes()
    output = tmp_path / "reviewed.json"
    monkeypatch.setattr(reviewed, "REVIEWED", output)
    assert reviewed.main(["--write"]) == 0
    assert output.read_bytes() == committed == reviewed.reviewed_bytes()
    mtime = output.stat().st_mtime_ns
    assert reviewed.main(["--write"]) == 0
    assert reviewed.main([]) == 0
    assert output.read_bytes() == committed and output.stat().st_mtime_ns == mtime
    assert all(p.read_bytes() == raw for p, raw in before.items())
    damaged = json.loads(committed)
    damaged["boundaries"][0]["evidence"]["reviewed"] = False
    output.write_bytes(encode(damaged))
    for args in ([], ["--write"]):
        with pytest.raises(ValueError, match="immutable"):
            reviewed.main(args)
        assert output.read_bytes() == encode(damaged)


def test_cli_has_no_production_path_override_or_rebuild_operations():
    for args in (["--output", "data/reference/ptcg_live_windows.json"], ["next"], ["all"], ["window", "SVI"]):
        with pytest.raises(SystemExit) as error:
            reviewed.main(args)
        assert error.value.code == 2
