"""Gate D2 A–S: independently evidenced starts, explicit holes, offline preview."""
from copy import deepcopy
import html
import json
import socket

import pytest

from historical_boundaries.catalog import (
    LEDGER, CANDIDATE, PREVIEW, REQUIRED_EXPANSIONS, REQUIRED_ROTATIONS,
    C0_EXPECTATIONS, artifacts, assess, compile_catalog, compatibility_preview,
    project_observation, verify_observation,
)
from historical_rebuild.model import NotReady, load_windows
from official_release_observer.parsing import observe, seal, sha, CATEGORY, HOST, integrity
from official_release_observer.reconcile import revision
from official_release_observer.timeparse import extract_time


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("D2 checks must not access the network or execute a rebuild")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    from historical_rebuild.runner import Runner
    monkeypatch.setattr(Runner, "run", forbidden)


@pytest.fixture
def ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def synthetic_observation(temporal="October 22, 2026 - 10:00 AM PT", *,
                          name="Scarlet & Violet—Shrouded Fable", playable=None,
                          rotation=False, source_id=99990, body=None):
    """Synthetic DOM: grammar/authority tests, never proposed historical evidence."""
    esc = html.escape
    url = f"{HOST}/en-us/discussion/{source_id}/synthetic-offline-fixture"
    if body is None:
        body = (f"<p>We're excited to share that the new {esc(name)} expansion is available starting today!</p>"
                f"<p>{esc(temporal)}</p><ul><li>{esc(playable or name)} cards can be played in all game modes.</li>")
        if rotation:
            body += "<li>The 2026 Pokémon TCG Standard format goes into effect.</li>"
        body += "</ul>"
    markup = f'''<html><head><link rel="canonical" href="{url}"></head><body><h1>Fixture release</h1>
    <div id="Discussion_{source_id}" class="ItemDiscussion Role_Administrator Rank-Admin">
    <div class="DiscussionHeader"><span class="Author"><a class="Username" data-userid="123" href="{HOST}/en-us/profile/Fixture">Fixture</a></span>
    <span class="AuthorInfo"><span class="RoleTitle">Administrator</span></span>
    <div class="DiscussionMeta"><span class="Category"><a href="{CATEGORY}">Pokémon TCG Live News &amp; Announcements</a></span>
    <span class="DateCreated"><time datetime="2026-09-18T00:00:00Z"></time></span></div></div>
    <div class="Item-Body"><div class="Message userContent">{body}</div></div></div></body></html>'''
    return observe(url, markup, "2026-09-18T12:00:00Z")


def sample_entry(ledger, code="SFA"):
    return deepcopy(next(e for e in ledger["inventory"] if e["event_id"] == code))


def evaluate(ledger, originals):
    entry = sample_entry(ledger)
    records = [project_observation(o) for o in originals]
    entry["exact_evidence_ids"] = [o["observation_id"] for o in records]
    for record in records:
        verify_observation(record)
    return assess(entry, {o["observation_id"]: o for o in records})


def synthetic_candidate(rotation_time=None):
    rows = [("A", "expansion", "2020-01-01T17:00:00Z"),
            ("B", "expansion", "2020-02-01T17:00:00Z"),
            ("C", "expansion", "2020-03-01T17:00:00Z")]
    if rotation_time:
        rows.append(("R", "rotation", rotation_time))
    rows.sort(key=lambda x: x[2])
    return dict(schema_version=1, reviewed=False, boundaries=[
        dict(event_id=code, name="Synthetic " + code, kind=kind, start_utc=stamp,
             reasons=["standard_rotation" if kind == "rotation" else "expansion"],
             evidence=dict(url=HOST+"/en-us/discussion/99990/synthetic-offline-fixture", reviewed=False, exact_time=True))
        for code, kind, stamp in rows])


def test_required_inventory_and_actual_reconciliation(ledger):
    candidate, entries, preview = compile_catalog(ledger)
    assert len(REQUIRED_EXPANSIONS) == 23
    assert {e["event_id"] for e in entries} == dict(REQUIRED_EXPANSIONS + REQUIRED_ROTATIONS).keys()
    assert {e["event_id"] for e in candidate["boundaries"]} == {e["event_id"] for e in entries}
    assert len({o["canonical_url"] for o in ledger["observations"] if o["time"]}) == 22
    assert sum(e["exact_time_verified"] for e in entries) == 21
    assert {e["event_id"] for e in entries if not e["start_utc"]} == {
        "PAF", "SSP", "PRE", "ROTATION_2023", "ROTATION_2024", "ROTATION_2025"}
    assert all(e["found_official"] and e["mapped"] and not e["missing"] for e in entries)
    assert {e["event_id"] for e in entries if e["conflict"]} == {"SSP", "PRE"}
    assert candidate["catalog_status"] == "INCOMPLETE_NOT_READY"
    assert preview["windows"] == [] and preview["status"] == "NOT_READY"
    assert ledger["coverage"]["editorial"]["inventory_complete"] is False
    expected = {code for code, _ in REQUIRED_EXPANSIONS}
    assert {e["event_id"] for e in ledger["coverage"]["forum"]["identities"]} == expected
    identity_rows = ledger["coverage"]["identity_only"]["discovered_identity_rows"]
    assert {e["code"] for e in identity_rows if not e["excluded"]} == expected
    assert {e["code"] for e in identity_rows if e["excluded"]} == {"MEE", "MEP"}


@pytest.mark.parametrize("code", ["PAF", "SFA", "ROTATION_2025"])
def test_missing_inventory_identity_cannot_silently_close_gap(ledger, code):
    ledger["inventory"] = [e for e in ledger["inventory"] if e["event_id"] != code]
    with pytest.raises(ValueError, match="missing/unexpected"):
        compile_catalog(ledger)


def test_unavailable_intermediate_source_keeps_placeholder(ledger):
    row = next(e for e in ledger["inventory"] if e["event_id"] == "PAL")
    row["exact_evidence_ids"] = []
    candidate, entries, preview = compile_catalog(ledger)
    result = next(e for e in entries if e["event_id"] == "PAL")
    assert result["status"] == "SOURCE_UNAVAILABLE" and result["missing"]
    assert next(b for b in candidate["boundaries"] if b["event_id"] == "PAL")["start_utc"] is None
    assert preview["windows"] == []


def test_all_accepted_times_are_c1_first_party_not_identity_index(ledger):
    _, entries, _ = compile_catalog(ledger)
    observations = {o["observation_id"]: o for o in ledger["observations"]}
    for entry in entries:
        if not entry["start_utc"]:
            continue
        for oid in entry["exact_evidence_ids"]:
            record = observations[oid]
            verify_observation(record)
            assert record["authority"] == "PASS"
            assert record["canonical_url"].startswith(HOST+"/en-us/discussion/")
            assert record["time"]["calculated_utc"] == entry["start_utc"]
            assert record["event_kind"] in {"expansion", "combined"}
        if entry["kind"] == "expansion":
            assert entry["identity"]["time_authority"] is False


def test_limitless_never_becomes_time_authority():
    record = project_observation(synthetic_observation())
    record["canonical_url"] = "https://limitlesstcg.com/cards/SFA"
    seal(record)
    with pytest.raises(ValueError, match="non-first-party"):
        verify_observation(record)


@pytest.mark.parametrize("text,utc,state", [
    ("October 22, 2026 - 10:00 AM PT", "2026-10-22T17:00:00Z", "MACHINE_VERIFIED"),
    ("January 22, 2026 - 10:00 AM PT", "2026-01-22T18:00:00Z", "MACHINE_VERIFIED"),
    ("July 22, 2026 - 9:30 AM PDT", "2026-07-22T16:30:00Z", "MACHINE_VERIFIED"),
    ("January 22, 2026 - 9:00 AM PST", "2026-01-22T17:00:00Z", "MACHINE_VERIFIED"),
    ("January 22, 2026 - 9:00 AM PDT", None, "CANONICAL_PENDING"),
    ("July 22, 2026 - 9:00 AM PST", None, "CANONICAL_PENDING"),
    ("October 22, 2026", None, "DATE_CONFIRMED"),
    ("October 22, 2026 - 10:00 AM CST", None, "CANONICAL_PENDING"),
    ("March 8, 2026 - 2:30 AM PT", None, "CANONICAL_PENDING"),
    ("November 1, 2026 - 1:30 AM PT", None, "CANONICAL_PENDING"),
    ("January 22, 2026 - 10:00 AM PT (17:00 UTC)", None, "OFFICIAL_CONFLICT"),
])
def test_source_clock_conversion_no_pattern_fallback(ledger, text, utc, state):
    result = evaluate(ledger, [synthetic_observation(text)])
    assert result["start_utc"] == utc
    assert result["status"] == state


def test_disagreeing_official_exact_sources_fail_closed(ledger):
    a = synthetic_observation()
    b = synthetic_observation("October 22, 2026 - 11:00 AM PT", source_id=99991)
    result = evaluate(ledger, [a, b])
    assert result["status"] == "OFFICIAL_CONFLICT"
    assert result["start_utc"] is None


def test_compatible_date_only_corroborates_without_creating_time(ledger):
    exact = synthetic_observation()
    date_only = synthetic_observation("October 22, 2026", source_id=99991)
    assert evaluate(ledger, [date_only])["start_utc"] is None
    assert evaluate(ledger, [exact, date_only])["start_utc"] == "2026-10-22T17:00:00Z"


def test_revision_preserves_predecessor_and_quarantines_material_change():
    a = synthetic_observation()
    before = deepcopy(a)
    b = synthetic_observation("October 22, 2026 - 11:00 AM PT")
    assert revision(a, b) == ("SOURCE_CHANGED", True)
    assert a == before and a["observation_id"] != b["observation_id"]


def test_unknown_mapping_preserves_exact_evidence_without_accepted_boundary(ledger):
    row = next(e for e in ledger["inventory"] if e["event_id"] == "SFA")
    row["identity"]["code"] = "UNPROVED"
    candidate, entries, preview = compile_catalog(ledger)
    result = next(e for e in entries if e["event_id"] == "SFA")
    assert result["status"] == "IDENTITY_UNMAPPED"
    assert result["machine_verified_utc"] == "2024-08-01T17:00:00Z"
    assert result["start_utc"] is None and preview["windows"] == []


def test_other_expansion_source_cannot_be_relabelled_as_missing_boundary(ledger):
    row = next(e for e in ledger["inventory"] if e["event_id"] == "PAF")
    other = sample_entry(ledger, "PAR")
    row.update(exact_evidence_ids=other["exact_evidence_ids"], official_source_expansion=other["official_source_expansion"])
    with pytest.raises(ValueError, match="does not name the catalog identity"):
        compile_catalog(ledger)


@pytest.mark.parametrize("code,expected", C0_EXPECTATIONS.items())
def test_independent_c0_regressions(ledger, code, expected):
    _, entries, _ = compile_catalog(ledger)
    assert next(e for e in entries if e["event_id"] == code)["start_utc"] == expected


def test_committed_artifacts_deterministic_unreviewed_no_cutoff(ledger):
    before = deepcopy(ledger)
    outputs = artifacts(ledger)
    assert outputs == artifacts(ledger)
    for path, text in outputs.items():
        assert path.read_text(encoding="utf-8") == text
    candidate = json.loads(outputs[CANDIDATE])
    assert candidate["reviewed"] is False
    assert all(b["evidence"]["reviewed"] is False for b in candidate["boundaries"])
    assert "observation_cutoff_utc" not in outputs[CANDIDATE]
    assert all("end_utc" not in b for b in candidate["boundaries"])
    assert candidate["boundaries"][-1]["event_id"] == "30C"
    assert ledger == before
    with pytest.raises(NotReady, match="reviewed=true"):
        load_windows(candidate)


def test_complete_synthetic_input_requires_in_memory_attestation_and_derives_ends():
    candidate = synthetic_candidate()
    before = deepcopy(candidate)
    with pytest.raises(NotReady):
        load_windows(candidate)
    preview = compatibility_preview(candidate, True)
    assert preview["d1_compatibility"] == "PASS_IN_MEMORY_ONLY"
    windows = preview["windows"]
    assert len(windows) == 3
    assert [w["end_utc"] for w in windows[:-1]] == [w["start_utc"] for w in windows[1:]]
    assert windows[-1]["end_utc"] is None
    assert windows[-1]["observation_cutoff_utc"] is None
    assert candidate == before


def test_missing_middle_boundary_never_derives_a_to_c():
    candidate = synthetic_candidate()
    candidate["boundaries"][1]["start_utc"] = None
    assert compatibility_preview(candidate, True)["windows"] == []
    assert compatibility_preview(candidate, True)["skipped_gaps"] is False


def test_incomplete_independent_coverage_prevents_preview_even_when_all_times_exist():
    preview = compatibility_preview(synthetic_candidate(), False)
    assert preview["windows"] == []
    assert preview["d1_compatibility"] == "SCHEMA_COMPATIBLE_COVERAGE_INCOMPLETE"


@pytest.mark.parametrize("timestamp,length", [("2020-02-01T17:00:00Z", 3), ("2020-02-15T17:00:00Z", 4)])
def test_coincident_and_distinct_rotation_windows(timestamp, length):
    preview = compatibility_preview(synthetic_candidate(timestamp), True)
    assert len(preview["windows"]) == length
    window = next(w for w in preview["windows"] if w["start_utc"] == timestamp)
    if length == 3:
        assert {e["kind"] for e in window["events"]} == {"expansion", "rotation"}
        assert {r for e in window["events"] for r in e["reasons"]} == {"expansion", "standard_rotation"}
    for w in preview["windows"]:
        assert w["end_utc"] is None or w["start_utc"] < w["end_utc"]


def test_real_coincident_events_and_unproved_rotations(ledger):
    candidate, entries, _ = compile_catalog(ledger)
    by_id = {b["event_id"]: b for b in candidate["boundaries"]}
    assert by_id["POR"]["start_utc"] == by_id["ROTATION_2026"]["start_utc"] == C0_EXPECTATIONS["POR"]
    assert by_id["BLK"]["start_utc"] == by_id["WHT"]["start_utc"] == "2025-07-17T17:00:00Z"
    assert all(by_id[f"ROTATION_{y}"]["start_utc"] is None for y in (2023, 2024, 2025))
    row = next(e for e in ledger["inventory"] if e["event_id"] == "ROTATION_2026")
    record = next(o for o in ledger["observations"] if o["observation_id"] in row["exact_evidence_ids"])
    assert record["rotation_binding_excerpt"] == "The 2026 Pokémon TCG Standard format goes into effect."


def test_rotation_cannot_borrow_expansion_or_trials_clock(ledger):
    row = next(e for e in ledger["inventory"] if e["event_id"] == "ROTATION_2025")
    expansion = sample_entry(ledger, "JTG")
    row.update(exact_evidence_ids=expansion["exact_evidence_ids"], official_source_expansion=expansion["official_source_expansion"])
    with pytest.raises(ValueError, match="rotation is not explicitly bound"):
        compile_catalog(ledger)


def test_projection_is_minimal_separately_sealed_and_keeps_provenance():
    original = synthetic_observation()
    before = deepcopy(original)
    projected = project_observation(original)
    verify_observation(projected)
    assert original == before
    assert projected["origin_observation_id"] == original["observation_id"]
    assert projected["origin_excerpt_sha256"] == original["excerpt_sha256"]
    assert projected["observation_id"] != original["observation_id"]
    assert projected["excerpt"] == original["time"]["raw_local"]
    assert integrity(projected)
    projected["time"]["calculated_utc"] = "2026-10-22T00:00:00Z"
    seal(projected)
    with pytest.raises(ValueError, match="conversion/tzdb"):
        verify_observation(projected)


def test_em_dash_whitespace_equivalence_preserves_original_excerpt():
    record = synthetic_observation(playable="Scarlet & Violet— Shrouded Fable")
    assert record["state"] == "EXACT"
    assert record["expansion"] == "Scarlet & Violet—Shrouded Fable"
    assert "Scarlet & Violet— Shrouded Fable cards" in record["excerpt"]
    assert record["excerpt_sha256"] == sha(record["excerpt"])


@pytest.mark.parametrize("wrong_name", ["Scarlet & Violet—Stellar Crown", "Scarlet & Violet-Shrouded Fable", "Shrouded Fable"])
def test_name_normalization_does_not_merge_different_identity(wrong_name):
    assert synthetic_observation(playable=wrong_name)["state"] == "EVENT_AMBIGUOUS"


def test_offline_cli_checks_real_artifacts(capsys):
    from historical_boundaries.__main__ import main
    assert main([]) == 0
    assert "reviewed=false" in capsys.readouterr().out
