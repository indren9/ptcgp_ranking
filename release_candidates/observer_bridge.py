"""Versioned machine draft sidecar. Existing human Candidate/Evidence stay unchanged."""
from official_release_observer.parsing import integrity
from official_release_observer.reconcile import reconcile
from .model import Evidence, prepare_candidate


def machine_drafts(observations):
    """Timestamp verification does not depend on release-code mapping or human flags."""
    candidates = reconcile(observations)
    by_id = {o["observation_id"]: o for o in observations}
    return [dict(c, schema_version=1, authority_verified=False, identity_verified=False,
                 provenance=[{k: by_id[oid][k] for k in (
                     "observation_id", "verification_method", "authority_policy_version", "parser_version",
                     "source_adapter_version", "tzdb_version")} for oid in c["observation_ids"]],
                 canonical_mutation_allowed=False, structurally_promotable=False) for c in candidates]


def phase_b_draft(observation, mapping):
    """Reuse an externally reviewed mapping; machine evidence never becomes human-attested.

    Caller supplies code/key/name/review reference. This function does not load or
    change canonical catalogs, and deliberately emits only DISCOVERED candidates.
    """
    if not integrity(observation):
        raise ValueError("evidence integrity failed")
    if set(mapping) != {"release_code", "event_key", "release_name", "review_reference"}:
        raise ValueError("reviewed mapping schema required")
    if not all(isinstance(v, str) and v.strip() for v in mapping.values()) or mapping["release_name"] != observation["expansion"]:
        raise ValueError("mapping must bind exact observed expansion")
    if observation["state"] not in {"EXACT", "DATE_CONFIRMED"} or observation["authority"] != "PASS":
        raise ValueError("only verified source evidence can be bridged")
    event = "ptcg:ptcgl:standard:" + mapping["event_key"]
    t = observation["time"]
    local = t["local"]
    e = Evidence(event_reference=event, source_tier="B", source_url=observation["canonical_url"],
                 source_authority="pokemon", source_category="official_staff_announcement" if observation["author_id"] else "official_announcement",
                 source_author=observation["author_name"], retrieved_at=observation["retrieved_at"],
                 retained_excerpt=observation["excerpt"], evidence_digest=observation["excerpt_sha256"],
                 authority_verified=False, extracted_date=t["date"],
                 extracted_time=local[11:19] if local else None,
                 extracted_timezone=("UTC" if t["raw_zone"] == "UTC" else "America/Los_Angeles") if local else None,
                 effective_local_datetime=local,
                 conversion_rationale="MACHINE_POLICY sidecar " + observation["observation_id"] if local else None)
    kind = observation["event_kind"]
    return prepare_candidate(dict(game="PTCG", platform="PTCGL", format="STANDARD",
        release_code=mapping["release_code"], release_name=mapping["release_name"], event_key=mapping["event_key"],
        semantic_event_id=event, event_kind=kind, boundary_reasons=["full_expansion_availability"] +
        (["standard_rotation"] if kind == "combined" else []), candidate_state="DISCOVERED",
        evidence=[e.__dict__], identity_verified=False, identity_note="Supplied mapping reference: " + mapping["review_reference"],
        effective_date=t["date"], effective_local_datetime=local, source_timezone=e.extracted_timezone,
        effective_utc_datetime=t["calculated_utc"], missing_evidence=["Separate human authority/identity review and canonical parent required."]))
