"""Isolated future-candidate fixtures; real canonical bytes are never fixture baselines."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import socket

import pytest

from release_candidates.model import (
    Candidate, Evidence, digest, event_identity, prepare_candidate, stable_json,
)
from release_candidates.validation import ParentSnapshot, validate_candidates
from release_candidates.reporting import report_json, report_markdown, write_review


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = [ROOT / p for p in (
    "data/reference/pocket_releases.json", "data/reference/ptcg_live_windows.json",
    ".github/tcg-live-latest-completed-meta-state.json", "requirements.txt",
    ".github/workflows/update-expansion-catalog.yml", "public/expansions_pocket_standard.csv",
)]


@pytest.fixture(autouse=True)
def canonical_and_state_integrity(monkeypatch):
    before = {path: path.read_bytes() for path in PROTECTED}
    def forbidden(*args, **kwargs):
        raise AssertionError("candidate infrastructure must not acquire or publish")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    yield
    assert {path: path.read_bytes() for path in PROTECTED} == before


@pytest.fixture
def parents():
    # Synthetic baselines, not a permanent content freeze of production files.
    return {game: ParentSnapshot.from_bytes(stable_json({
        "catalog_version": "fixture-v1", "releases": rows,
    }).encode()) for game, rows in (
        ("POCKET", [{"code": "B4a", "release_datetime": "2026-08-27T01:00:00Z"}]),
        ("PTCG", [{"code": "CRI", "release_datetime": "2026-05-21T00:00:00Z"},
                  {"code": "PBL", "release_datetime": "2026-07-16T00:00:00Z"}]),
    )}


def evidence(event, tier="B", day="2027-01-28", hour="17:00:00", zone="UTC", offset="+00:00"):
    authority, category, url = {
        "A": ("pokemon", "documented_api", "https://www.pokemon.com/fixture-api"),
        "B": ("pokemon", "official_announcement", "https://www.pokemon.com/fixture-announcement"),
        "C": ("limitless", "operational_metadata", "https://pocket.limitlesstcg.com/cards"),
        "D": ("third_party", "independent_report", "https://example.org/report"),
        "E": ("community", "community_observation", "https://example.org/community"),
    }[tier]
    excerpt = f"SYNTHETIC TEST EVIDENCE ONLY: {day} {hour} {zone}."
    return dict(event_reference=event, source_tier=tier, source_url=url,
                source_authority=authority, source_category=category,
                retrieved_at="2026-09-16T10:00:00Z", retained_excerpt=excerpt,
                evidence_digest=digest(excerpt.encode()), authority_verified=tier in {"A", "B"},
                authority_note="Synthetic fixture reviewer attestation" if tier in {"A", "B"} else None,
                documentation_url="https://www.pokemon.com/fixture-docs" if tier == "A" else None,
                extracted_date=day, extracted_time=hour, extracted_timezone=zone if hour else None,
                effective_local_datetime=f"{day}T{hour}{offset}" if hour else None,
                conversion_rationale="Explicit fixture offset, no historical timing inference" if hour else None)


def proposal(parents, *, game="POCKET", code="B5", state="CANONICAL_CANDIDATE",
             key=None, kind="expansion", day="2027-01-28", hour="17:00:00", tier="B"):
    platform = "POCKET" if game == "POCKET" else "PTCGL"
    key = key or f"release:{code.lower()}"
    event = event_identity(game, platform, "STANDARD", key)
    ev = evidence(event, tier, day, hour)
    return dict(game=game, platform=platform, format="STANDARD", release_code=code,
                release_name=f"Fixture {code}", event_key=key, semantic_event_id=event,
                event_kind=kind, boundary_reasons=(
                    ["standard_rotation"] if kind == "rotation" else
                    ["full_expansion_availability", "standard_rotation"] if kind == "combined" else
                    ["full_expansion_availability"]),
                candidate_state=state, evidence=[ev], identity_verified=True,
                identity_note="Fixture exact mapping, not a guessed alias",
                effective_date=day, effective_local_datetime=ev["effective_local_datetime"],
                source_timezone=ev["extracted_timezone"],
                effective_utc_datetime=f"{day}T{hour}Z" if hour else None,
                parent_canonical_version=parents[game].version,
                parent_canonical_digest=parents[game].sha256)


def result(payload, parents):
    return validate_candidates([prepare_candidate(payload)], parents)[0]


def test_pocket_a_discovery_without_official_evidence(parents):
    data = proposal(parents, state="DISCOVERED", tier="C", day=None, hour=None)
    data["identity_verified"] = False
    r = result(data, parents)
    assert r["candidate_state"] == "DISCOVERED"
    assert r["effective_utc_datetime"] is None
    assert not r["structurally_promotable"]
    assert "reviewed Tier A/B authority" in r["missing_evidence"]


def test_pocket_b_date_only_never_becomes_midnight(parents):
    data = proposal(parents, state="DATE_CONFIRMED", hour=None)
    r = result(data, parents)
    assert not r["validation_errors"]
    assert r["effective_utc_datetime"] is None
    assert not r["structurally_promotable"]
    data["candidate_state"] = "TIMESTAMP_CONFIRMED"
    assert result(data, parents)["validation_errors"]


def test_pocket_c_explicit_zone_conversion(parents):
    data = proposal(parents, state="TIMESTAMP_CONFIRMED")
    ev = evidence(data["semantic_event_id"], day="2027-01-28", hour="17:00:00",
                  zone="America/Los_Angeles", offset="-08:00")
    data.update(evidence=[ev], effective_local_datetime=ev["effective_local_datetime"],
                source_timezone=ev["extracted_timezone"], effective_utc_datetime="2027-01-29T01:00:00Z")
    r = result(data, parents)
    assert r["structurally_promotable"]
    assert not r["validation_errors"]


def test_pocket_d_operational_conflict_keeps_official_authority(parents):
    data = proposal(parents)
    data["evidence"].append(evidence(data["semantic_event_id"], tier="C", hour="06:00:00"))
    data["conflict_status"] = "OPERATIONAL_ADVISORY"
    r = result(data, parents)
    assert r["structurally_promotable"]
    assert r["effective_utc_datetime"].endswith("17:00:00Z")
    assert r["conflicts"]


@pytest.mark.parametrize("state", ["CONFLICT", "QUARANTINED", "TIMESTAMP_CONFIRMED"])
def test_pocket_e_disagreeing_authorities_never_promotable(parents, state):
    data = proposal(parents, state=state)
    data["evidence"].append(evidence(data["semantic_event_id"], hour="06:00:00"))
    data["conflict_status"] = "AUTHORITATIVE"
    r = result(data, parents)
    assert not r["structurally_promotable"]
    assert r["conflict_status"] == "AUTHORITATIVE"
    assert r["candidate_state"] in {"CONFLICT", "QUARANTINED"}
    assert r["submitted_candidate_state"] == state


def test_pocket_f_delay_new_revision_same_semantic_event(parents):
    old = prepare_candidate(proposal(parents, state="SUPERSEDED"))
    data = proposal(parents, day="2027-02-01")
    data["supersedes_revision"] = old.candidate_revision
    new = prepare_candidate(data)
    assert new.semantic_event_id == old.semantic_event_id
    assert new.candidate_revision != old.candidate_revision
    results = validate_candidates([new, old], parents)
    assert all(not r["validation_errors"] for r in results)
    assert sum(r["structurally_promotable"] for r in results) == 1


def test_pocket_g_future_approval_is_representation_not_activation(parents):
    draft = prepare_candidate(proposal(parents))
    data = draft.to_dict()
    data.update(candidate_state="CANONICAL_APPROVED", approval_reference="fixture-review-123")
    approved = Candidate.from_dict(data)
    assert approved.candidate_revision == draft.candidate_revision
    r = validate_candidates([approved], parents)[0]
    assert r["structurally_promotable"]
    assert not r["approval_is_activation"] and not r["canonical_mutation_allowed"]
    with pytest.raises(ValueError, match="cannot approve"):
        prepare_candidate(data)


def test_pocket_h_alias_not_guessed(parents):
    data = proposal(parents, code="UNEXPECTED_ALIAS", state="TIMESTAMP_CONFIRMED")
    data["identity_verified"] = False
    r = result(data, parents)
    assert r["release_code"] == "UNEXPECTED_ALIAS"
    assert not r["structurally_promotable"]
    assert any("mapping" in item for item in r["missing_evidence"])


@pytest.mark.parametrize("tier", ["C", "D", "E"])
def test_pocket_i_historical_lower_tier_is_not_future_precedent(parents, tier):
    original = (ROOT / "data/reference/pocket_releases.json").read_bytes()
    r = result(proposal(parents, tier=tier), parents)
    assert not r["structurally_promotable"]
    assert r["source_tier"] == tier
    assert (ROOT / "data/reference/pocket_releases.json").read_bytes() == original


@pytest.mark.parametrize("code,day", [("CRI", "2026-05-21"), ("PBL", "2026-07-16")])
def test_tcg_midnight_v1_to_exact_v2_is_candidate_only(parents, code, day):
    old = prepare_candidate(proposal(parents, game="PTCG", code=code, day=day,
                                     hour="00:00:00", state="SUPERSEDED", tier="C"))
    data = proposal(parents, game="PTCG", code=code, day=day)
    data["supersedes_revision"] = old.candidate_revision
    new = prepare_candidate(data)
    assert new.release_code == old.release_code
    assert new.semantic_event_id == old.semantic_event_id
    assert new.candidate_revision != old.candidate_revision
    assert old.effective_utc_datetime == f"{day}T00:00:00Z"
    assert new.effective_utc_datetime == f"{day}T17:00:00Z"
    results = validate_candidates([old, new], parents)
    assert all(not r["validation_errors"] for r in results)
    assert next(r for r in results if r["candidate_revision"] == new.candidate_revision)["structurally_promotable"]


@pytest.mark.parametrize("field", ["version", "sha256"])
def test_stale_parent_never_silently_rebased(parents, field):
    c = prepare_candidate(proposal(parents))
    changed = dict(parents)
    changed["POCKET"] = replace(parents["POCKET"], **{field: "changed"})
    r = validate_candidates([c], changed)[0]
    assert r["stale_parent"] and not r["structurally_promotable"]
    assert r["parent_canonical_digest"] == c.parent_canonical_digest


@pytest.mark.parametrize("field", ["parent_canonical_version", "parent_canonical_digest"])
def test_mature_parent_binding_required(parents, field):
    data = proposal(parents)
    data[field] = None
    assert result(data, parents)["validation_errors"]


def test_parent_digest_is_actual_bytes_not_only_version(parents):
    raw = stable_json({"catalog_version": "same", "releases": []}).encode()
    assert ParentSnapshot.from_bytes(raw).sha256 != ParentSnapshot.from_bytes(raw + b" ").sha256


def test_rotation_only_same_expansion_distinct_event(parents):
    expansion = prepare_candidate(proposal(parents))
    rotation = prepare_candidate(proposal(parents, kind="rotation", key="rotation:2027", day="2027-03-01"))
    assert rotation.semantic_event_id != expansion.semantic_event_id
    assert all(r["structurally_promotable"] for r in validate_candidates([rotation, expansion], parents))


def test_coincident_expansion_rotation_requires_single_composite(parents):
    expansion = prepare_candidate(proposal(parents))
    rotation = prepare_candidate(proposal(parents, kind="rotation", key="rotation:2027"))
    assert all(not r["structurally_promotable"] for r in validate_candidates([expansion, rotation], parents))
    composite = result(proposal(parents, kind="combined"), parents)
    assert composite["structurally_promotable"]
    assert len(composite["boundary_reasons"]) == 2


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None])
def test_strict_boolean_schema(parents, bad):
    data = proposal(parents)
    data["identity_verified"] = bad
    with pytest.raises(ValueError, match="boolean"):
        prepare_candidate(data)
    data = proposal(parents)
    data["evidence"][0]["authority_verified"] = bad
    with pytest.raises(ValueError, match="boolean"):
        prepare_candidate(data)


@pytest.mark.parametrize("mutation", [
    {"source_tier": "F"}, {"source_authority": "limitless"},
    {"source_category": "operational_metadata"}, {"source_url": "http://www.pokemon.com/x"},
    {"source_url": "https://pokemon.com.evil.example/x"},
    {"source_url": "https://user:password@www.pokemon.com/x"},
    {"source_url": "https://www.pokemon.com/x#fragment"},
    {"evidence_digest": "0" * 64}, {"retrieved_at": "2026-09-16T00:00:00"},
    {"effective_local_datetime": "2027-01-28T17:00:00"},
    {"extracted_timezone": "PST"}, {"extracted_time": "06:00:00"},
])
def test_invalid_evidence_rejected(parents, mutation):
    data = proposal(parents)
    data["evidence"][0].update(mutation)
    with pytest.raises(ValueError):
        prepare_candidate(data)


def test_url_is_not_proof_of_official_authority(parents):
    data = proposal(parents)
    data["evidence"][0]["authority_verified"] = False
    assert not result(data, parents)["structurally_promotable"]


def test_tier_a_documentation_and_staff_identity_required(parents):
    data = proposal(parents, tier="A")
    assert result(data, parents)["structurally_promotable"]
    data["evidence"][0]["documentation_url"] = None
    with pytest.raises(ValueError, match="documentation"):
        prepare_candidate(data)
    data = proposal(parents)
    data["evidence"][0]["source_category"] = "official_staff_announcement"
    data["evidence"][0]["source_url"] = "https://community.pokemon.com/en-us/discussion/fixture"
    with pytest.raises(ValueError, match="author"):
        prepare_candidate(data)


@pytest.mark.parametrize("field,value", [("game", "PHYSICAL"), ("platform", "PAPER"),
                                        ("format", "EXPANDED"), ("release_code", ""),
                                        ("candidate_state", "ACTIVE"), ("candidate_state", "COMPLETED")])
def test_unsupported_or_empty_schema(parents, field, value):
    data = proposal(parents)
    data[field] = value
    with pytest.raises(ValueError):
        prepare_candidate(data)


def test_revision_deterministic_and_tampering_rejected(parents):
    data = proposal(parents)
    one = prepare_candidate(data)
    two = prepare_candidate(dict(reversed(list(data.items()))))
    assert one == two
    assert Candidate.from_dict(one.to_dict()) == one
    changed = one.to_dict()
    changed["effective_utc_datetime"] = "2027-01-28T18:00:00Z"
    with pytest.raises(ValueError, match="revision"):
        Candidate.from_dict(changed)


def test_guessed_utc_rejected_even_with_resealed_revision(parents):
    data = proposal(parents, hour=None, state="TIMESTAMP_CONFIRMED")
    data["effective_utc_datetime"] = "2027-01-28T00:00:00Z"
    assert result(data, parents)["validation_errors"]


def test_casefold_collision_in_candidates_and_parent(parents):
    upper = prepare_candidate(proposal(parents, code="B5"))
    lower = prepare_candidate(proposal(parents, code="b5", key="release:other", day="2027-02-01"))
    assert all(not r["structurally_promotable"] for r in validate_candidates([upper, lower], parents))
    r = result(proposal(parents, code="b4A"), parents)
    assert any("casefold" in e for e in r["validation_errors"])


def test_duplicate_identity_without_revision_relationship_rejected(parents):
    first = prepare_candidate(proposal(parents))
    second = prepare_candidate(proposal(parents, day="2027-02-01"))
    assert all(not r["structurally_promotable"] for r in validate_candidates([first, second], parents))
    assert all(r["validation_errors"] for r in validate_candidates([first, first], parents))


def test_supersession_missing_and_wrong_event_rejected(parents):
    data = proposal(parents)
    data["supersedes_revision"] = "0" * 64
    assert result(data, parents)["validation_errors"]
    other = prepare_candidate(proposal(parents, code="B6", state="SUPERSEDED"))
    data["supersedes_revision"] = other.candidate_revision
    assert all(r["validation_errors"] for r in validate_candidates([prepare_candidate(data), other], parents))


def test_wrong_event_evidence_and_hidden_conflict_rejected(parents):
    data = proposal(parents)
    data["evidence"][0]["event_reference"] = "another:event"
    assert result(data, parents)["validation_errors"]
    data = proposal(parents)
    data["evidence"].append(evidence(data["semantic_event_id"], hour="00:00:00"))
    assert result(data, parents)["validation_errors"]


def test_dst_offsets_are_checked_not_guessed():
    ev = evidence("fixture", day="2027-03-14", hour="02:30:00", zone="America/Los_Angeles", offset="-08:00")
    with pytest.raises(ValueError, match="nonexistent"):
        Evidence.from_dict(ev)
    for offset, utc_hour in [("-07:00", "08"), ("-08:00", "09")]:
        ev = evidence("fixture", day="2027-11-07", hour="01:30:00", zone="America/Los_Angeles", offset=offset)
        assert Evidence.from_dict(ev).utc() == f"2027-11-07T{utc_hour}:30:00Z"


def test_reports_deterministic_complete_and_non_authoritative(parents, tmp_path, monkeypatch):
    c = prepare_candidate(proposal(parents))
    results = validate_candidates([c], parents)
    raw = report_json(results)
    assert raw == report_json(validate_candidates([c], parents))
    assert "NON_AUTHORITATIVE" in raw
    for key in ("candidate_state", "stale_parent", "structurally_promotable", "conflicts", "parent_canonical_digest"):
        assert key in report_markdown(results)
    import release_candidates.reporting as reporting
    monkeypatch.setattr(reporting, "__file__", str(tmp_path / "release_candidates" / "reporting.py"))
    paths = write_review(results)
    assert all(path.is_relative_to(tmp_path / "data/candidates/releases/reviews") for path in paths)
    assert paths[0].read_text(encoding="utf-8") == raw
    assert write_review(results) == paths
    paths[0].write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable"):
        write_review(results)


def test_storage_has_no_arbitrary_output_path():
    import inspect
    assert list(inspect.signature(write_review).parameters) == ["results"]


def test_offline_cli_no_publication(parents, tmp_path, monkeypatch, capsys):
    import release_candidates.__main__ as cli
    candidate = prepare_candidate(proposal(parents))
    input_path = tmp_path / "input.json"
    input_path.write_text(stable_json([candidate.to_dict()]), encoding="utf-8")
    # Real parent is intentionally different: CLI must report stale, not rebase.
    assert cli.main([str(input_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["candidates"][0]["stale_parent"]


def test_no_production_module_imports_candidate_package():
    for directory in ("pipelines", "sources", "cli", "core", "mars", "scripts"):
        for path in (ROOT / directory).rglob("*.py"):
            assert "release_candidates" not in path.read_text(encoding="utf-8")


def test_forum_url_requires_official_staff_category_and_review(parents):
    data = proposal(parents)
    ev = data["evidence"][0]
    ev["source_url"] = "https://community.pokemon.com/en-us/discussion/fixture"
    with pytest.raises(ValueError, match="staff category"):
        prepare_candidate(data)
    ev.update(source_category="official_staff_announcement", source_author="Fixture official administrator")
    assert result(data, parents)["structurally_promotable"]


def test_equivalent_instants_with_different_local_dates_do_not_conflict(parents):
    data = proposal(parents)
    data["evidence"].append(evidence(data["semantic_event_id"], day="2027-01-29", hour="02:00:00",
                                      zone="+09:00", offset="+09:00"))
    r = result(data, parents)
    assert r["structurally_promotable"] and r["conflict_status"] == "NONE"


def test_date_only_authorities_can_conflict_without_guessed_time(parents):
    data = proposal(parents, state="CONFLICT", hour=None)
    data["evidence"].append(evidence(data["semantic_event_id"], day="2027-01-29", hour=None))
    data["conflict_status"] = "AUTHORITATIVE"
    r = result(data, parents)
    assert r["effective_utc_datetime"] is None
    assert not r["structurally_promotable"] and r["conflicts"]


def test_approval_reference_and_complete_evidence_are_required(parents):
    c = prepare_candidate(proposal(parents))
    data = c.to_dict()
    data["candidate_state"] = "CANONICAL_APPROVED"
    with pytest.raises(ValueError, match="review reference"):
        Candidate.from_dict(data)
    early = prepare_candidate(proposal(parents, state="DATE_CONFIRMED", hour=None)).to_dict()
    early.update(candidate_state="CANONICAL_APPROVED", approval_reference="fixture-review")
    r = validate_candidates([Candidate.from_dict(early)], parents)[0]
    assert not r["structurally_promotable"] and r["validation_errors"]


def test_missing_parent_snapshot_is_not_promotable(parents):
    c = prepare_candidate(proposal(parents))
    r = validate_candidates([c], {})[0]
    assert not r["structurally_promotable"]
    assert "current canonical parent snapshot" in r["missing_evidence"]


def test_superseded_requires_successor_and_successor_requires_old_state(parents):
    old = prepare_candidate(proposal(parents, state="SUPERSEDED"))
    assert validate_candidates([old], parents)[0]["validation_errors"]
    new_data = proposal(parents, day="2027-02-01")
    new_data["supersedes_revision"] = old.candidate_revision
    new = prepare_candidate(new_data)
    old = replace(old, candidate_state="CANONICAL_CANDIDATE")
    assert all(r["validation_errors"] for r in validate_candidates([new, old], parents))


def test_unknown_fields_and_non_array_schema_rejected(parents):
    data = proposal(parents)
    data["promote_now"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        prepare_candidate(data)
    data = proposal(parents)
    data["boundary_reasons"] = "full_expansion_availability"
    with pytest.raises(ValueError, match="JSON array"):
        prepare_candidate(data)


def test_storage_rejects_redirected_candidate_root(parents, tmp_path, monkeypatch):
    import release_candidates.reporting as reporting
    monkeypatch.setattr(reporting, "__file__", str(tmp_path / "release_candidates" / "reporting.py"))
    original_resolve = Path.resolve
    def redirected(path, *args, **kwargs):
        if path.name == "reviews":
            return tmp_path / "data" / "reference"
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(ValueError, match="redirect"):
        write_review([result(proposal(parents), parents)])
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("field", ["release_code", "release_name", "event_key", "semantic_event_id"])
def test_required_identifiers_cannot_be_null(parents, field):
    data = proposal(parents)
    data[field] = None
    with pytest.raises(ValueError, match="cannot be null"):
        prepare_candidate(data)


def test_required_evidence_identity_cannot_be_null(parents):
    data = proposal(parents)
    data["evidence"][0]["event_reference"] = None
    with pytest.raises(ValueError, match="cannot be null"):
        prepare_candidate(data)
