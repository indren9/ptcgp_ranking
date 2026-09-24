"""R3: explicit individual human approval never becomes machine/catalog review."""
from collections import Counter
from copy import deepcopy
import json
import socket

import pytest

from historical_boundaries import catalog
from historical_boundaries.human_adjudication import (
    APPROVED_STARTS, APPROVAL_GATE, APPROVAL_SOURCE, POLICY_VERSION, decision_record,
)
from historical_rebuild.model import NotReady, load_windows
from official_release_observer.parsing import seal, sha, stable
from official_release_observer.timeparse import extract_time


@pytest.fixture(autouse=True)
def no_network_fallback_publication_or_rebuild(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("R3 may not acquire, use Limitless fallback, publish or execute MARS")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    from historical_rebuild.runner import Runner
    from historical_boundaries import targeted_fallback
    from historical_boundaries import __main__ as cli
    monkeypatch.setattr(Runner, "run", forbidden)
    monkeypatch.setattr(targeted_fallback, "run_fallback", forbidden)
    monkeypatch.setattr(cli, "atomic_write", forbidden)


@pytest.fixture
def ledger():
    return json.loads(catalog.LEDGER.read_text(encoding="utf-8"))


@pytest.fixture
def approvals():
    return catalog.load_human_adjudications()


def by_code(entries):
    return {e["event_id"]: e for e in entries}


@pytest.mark.parametrize("code,start", APPROVED_STARTS.items())
def test_exact_start_requires_explicit_human_layer(ledger, approvals, code, start):
    machine = by_code(catalog.compile_catalog(ledger)[1])[code]
    candidate, entries, _ = catalog.compile_catalog(ledger, approvals)
    entry = by_code(entries)[code]
    assert machine["start_utc"] is None
    assert machine["status"] == ("CANONICAL_PENDING" if code == "PAF" else "OFFICIAL_CONFLICT")
    assert entry["start_utc"] == start and entry["status"] == "HUMAN_ADJUDICATED"
    assert entry["machine_status"] == machine["status"]
    assert entry["machine_verified_utc"] is None and entry["exact_time_verified"] is False
    assert entry["observer_states"] == machine["observer_states"]
    evidence = by_code(candidate["boundaries"])[code]["evidence"]
    assert evidence["authority_class"] == "HUMAN_ADJUDICATED"
    assert evidence["adjudication_reviewed"] is True and evidence["reviewed"] is False


@pytest.mark.parametrize("code", APPROVED_STARTS)
@pytest.mark.parametrize("remove", ["decision", "flag", "false"])
def test_removing_individual_approval_restores_blocked_state(ledger, approvals, code, remove):
    row = by_code(approvals["decisions"])[code]
    if remove == "decision":
        approvals["decisions"].remove(row)
    elif remove == "flag":
        row.pop("adjudication_reviewed")
    else:
        row["adjudication_reviewed"] = False
    candidate, entries, preview = catalog.compile_catalog(ledger, approvals)
    assert by_code(entries)[code]["start_utc"] is None
    assert by_code(entries)[code]["status"] == ("CANONICAL_PENDING" if code == "PAF" else "OFFICIAL_CONFLICT")
    assert sum(b["start_utc"] is None for b in candidate["boundaries"]) == 1
    assert candidate["catalog_status"] == "INCOMPLETE_NOT_READY"
    assert preview["windows"] == [] and preview["d1_compatibility"] == "BLOCKED_BY_UNRESOLVED_BOUNDARY"


def test_empty_register_is_exactly_machine_only_result(ledger, approvals):
    approvals["decisions"] = []
    assert catalog.compile_catalog(ledger, approvals) == catalog.compile_catalog(ledger)


def test_c1_records_and_all_predecessor_evidence_are_unchanged(ledger, approvals):
    before = deepcopy(ledger)
    source_bytes = catalog.LEDGER.read_bytes()
    r2 = json.loads((catalog.DATA / "tcg_live_r2_adjudication.json").read_text(encoding="utf-8"))
    assert sha(stable(ledger)) == r2["research"]["ledger_sha256"]
    catalog.compile_catalog(ledger, approvals)
    for row in approvals["decisions"]:
        c1 = extract_time(row["original_source_expression"])
        assert c1["state"] == row["c1_machine_state"]
        assert c1["calculated_utc"] is None
    assert ledger == before and catalog.LEDGER.read_bytes() == source_bytes


@pytest.mark.parametrize("field,value", [
    ("authorization_basis", "HISTORICAL_PATTERN"), ("historical_pattern_used", True),
    ("limitless_used_for_time", True), ("approval_source", "Limitless"),
    ("approval_gate", "GATE_1_11_D2_R2"), ("policy_version", "explicit-UTC-always-wins"),
    ("approved_canonical_utc", "2024-11-07T18:00:00Z"),
    ("approved_canonical_utc", "2024-11-07T17:35:00Z"),
    ("status", "MACHINE_VERIFIED"), ("c1_machine_state", "EXACT"),
    ("competing_first_party_exact_utc_found", True),
])
def test_patterns_limitless_and_unapproved_changes_cannot_authorize(ledger, approvals, field, value):
    row = by_code(approvals["decisions"])["SSP"]
    row[field] = value
    # Even a re-sealed document must still match the actual approved policy.
    row["adjudication_id"] = sha(stable({k: v for k, v in row.items()
                                         if k not in {"adjudication_id", "adjudication_reviewed"}}))
    with pytest.raises(ValueError, match="provenance/authorization"):
        catalog.compile_catalog(ledger, approvals)


def test_record_construction_does_not_infer_approval(ledger):
    for code in APPROVED_STARTS:
        assert decision_record(ledger, code)["adjudication_reviewed"] is False
    with pytest.raises(ValueError, match="restricted"):
        decision_record(ledger, "TEF")


def test_pre_keeps_calendar_conflict_and_uses_independent_date(ledger, approvals):
    row = by_code(approvals["decisions"])["PRE"]
    assert "2024" in row["original_source_expression"]
    assert row["approved_canonical_utc"] == "2025-01-16T17:00:00Z"
    assert row["corroborating_official_sources"][0]["confirmed_date"] == "2025-01-16"
    assert "/13604/" in row["corroborating_official_sources"][0]["url"]
    assert "Parade" in row["rationale"]
    assert row["hypothetical_pacific_diagnostic"]["authority"] is False
    assert row["hypothetical_pacific_diagnostic"]["result"]["calculated_utc"] == "2025-01-16T18:00:00Z"
    assert row["local_clock_diagnostic"]["calculated_utc"] is None


def test_competing_first_party_exact_time_invalidates_existing_approval(ledger, approvals):
    # Synthetic opposing official observation, not acquired historical evidence.
    # Isolate SSP so the unrelated PAF ledger-fingerprint check does not stop
    # first; this test specifically exercises the competing-source guard.
    approvals["decisions"] = [by_code(approvals["decisions"])["SSP"]]
    entry = by_code(ledger["inventory"])["SSP"]
    original = by_code(approvals["decisions"])["SSP"]["source_observation_id"]
    other = deepcopy(next(o for o in ledger["observations"] if o["observation_id"] == original))
    other["canonical_url"] = "https://community.pokemon.com/en-us/discussion/99996/synthetic-competing-release"
    other["time"] = extract_time("November 7, 2024 - 10:00 AM PT (18:00 UTC)")
    other["state"] = other["time"]["state"]
    other["tzdb_version"] = other["time"]["tzdb_version"]
    other["excerpt"] = other["time"]["raw_local"]
    other["excerpt_sha256"] = sha(other["excerpt"])
    seal(other)
    ledger["observations"].append(other)
    entry["exact_evidence_ids"].append(other["observation_id"])
    with pytest.raises(ValueError, match="competing first-party"):
        catalog.compile_catalog(ledger, approvals)


def test_resolved_counts_and_complete_contiguous_timeline(ledger, approvals):
    candidate, entries, preview = catalog.compile_catalog(ledger, approvals)
    expansions = [e for e in entries if e["kind"] == "expansion"]
    assert len(expansions) == 23 and all(e["start_utc"] for e in expansions)
    assert Counter(e["status"] for e in expansions) == {"MACHINE_VERIFIED": 20, "HUMAN_ADJUDICATED": 3}
    starts = sorted({e["start_utc"] for e in expansions})
    windows = preview["windows"]
    assert len(starts) == len(windows) == 22
    assert [w["start_utc"] for w in windows] == starts
    for a, b in zip(windows, windows[1:]):
        assert a["start_utc"] < a["end_utc"] == b["start_utc"]
    assert candidate["catalog_status"] == "COMPLETE_UNREVIEWED"
    frozen = ledger["resolution_research"]["frozen_expansion_starts"]
    assert {e["event_id"]: e["start_utc"] for e in expansions if e["event_id"] in frozen} == frozen


def test_joint_release_rotations_and_open_window(ledger, approvals):
    candidate, entries, preview = catalog.compile_catalog(ledger, approvals)
    windows = preview["windows"]
    joint = [w for w in windows if {e["event_id"] for e in w["events"]} == {"BLK", "WHT"}]
    assert len(joint) == 1 and joint[0]["start_utc"] == "2025-07-17T17:00:00Z"
    assert joint[0]["end_utc"] > joint[0]["start_utc"]
    rotations = by_code([e for e in entries if e["kind"] == "rotation"])
    assert len(rotations) == 4 and not set(rotations) & set(by_code(candidate["boundaries"]))
    assert rotations["ROTATION_2025"]["status"] == "DATE_CONFIRMED"
    assert rotations["ROTATION_2026"]["coincident_expansion_ids"] == ["POR"]
    assert all(e["kind"] == "expansion" for w in windows for e in w["events"])
    assert windows[-1]["window_id"] == "30C"
    assert windows[-1]["start_utc"] == "2026-09-15T17:00:00Z"
    assert windows[-1]["end_utc"] is None and windows[-1]["observation_cutoff_utc"] is None


def test_d1_loader_attestation_only_in_memory(ledger, approvals, monkeypatch):
    calls = []
    def actual_loader(payload):
        calls.append(deepcopy(payload))
        return load_windows(payload)
    monkeypatch.setattr(catalog, "load_windows", actual_loader)
    before = deepcopy((ledger, approvals))
    candidate, _, preview = catalog.compile_catalog(ledger, approvals)
    assert len(calls) == 1 and calls[0]["reviewed"] is True
    assert all(b["evidence"]["reviewed"] is True for b in calls[0]["boundaries"])
    assert candidate["reviewed"] is False and preview["reviewed"] is False
    assert all(b["evidence"]["reviewed"] is False for b in candidate["boundaries"])
    assert all(e["evidence"]["reviewed"] is False for w in preview["windows"] for e in (*w["events"], *w["end_events"]))
    assert preview["d1_compatibility"] == "PASS_IN_MEMORY_ONLY"
    assert (ledger, approvals) == before
    with pytest.raises(NotReady, match="reviewed=true"):
        load_windows(candidate)
    only_top_reviewed = deepcopy(candidate)
    only_top_reviewed["reviewed"] = True
    with pytest.raises(NotReady, match="reviewed exact"):
        load_windows(only_top_reviewed)


def test_individual_provenance_complete_and_approved(ledger, approvals):
    assert approvals["catalog_reviewed"] is False
    fields = {"original_source_url", "original_source_expression", "c1_machine_state",
        "local_clock_diagnostic", "explicit_utc", "conflicting_fields", "corroborating_official_sources",
        "policy_version", "approved_canonical_utc", "rationale", "approval_source", "approval_gate"}
    for row in approvals["decisions"]:
        assert fields <= row.keys() and row["adjudication_reviewed"] is True
        assert row["approval_source"] == APPROVAL_SOURCE
        assert row["approval_gate"] == APPROVAL_GATE and row["policy_version"] == POLICY_VERSION
        assert row["limitless_used_for_time"] is False and row["historical_pattern_used"] is False


@pytest.mark.parametrize("mutation", ["catalog_reviewed", "non_boolean", "duplicate", "other_code"])
def test_approval_register_fails_closed(ledger, approvals, mutation):
    if mutation == "catalog_reviewed":
        approvals["catalog_reviewed"] = True
    elif mutation == "non_boolean":
        approvals["decisions"][0]["adjudication_reviewed"] = 1
    elif mutation == "duplicate":
        approvals["decisions"].append(deepcopy(approvals["decisions"][0]))
    else:
        approvals["decisions"][0]["event_id"] = "TEF"
    with pytest.raises(ValueError):
        catalog.compile_catalog(ledger, approvals)


def test_persisted_artifacts_and_cli_are_offline_unreviewed(ledger, approvals, capsys):
    from historical_boundaries.__main__ import main
    for path, text in catalog.artifacts(ledger, approvals).items():
        assert path.read_text(encoding="utf-8") == text
    assert main([]) == 0
    assert "reviewed=false; no rebuild executed" in capsys.readouterr().out
