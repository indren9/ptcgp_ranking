"""Source evidence is not acceptance: reconcile all scoped official sources first."""
from .parsing import integrity


def reconcile(observations):
    if any(not integrity(o) for o in observations):
        raise ValueError("EVIDENCE_INTEGRITY_FAILED")
    observations = list({o["observation_id"]: o for o in observations}.values())
    identities_by_source = {o["canonical_url"]: o["provisional_identity"] for o in observations if o["provisional_identity"]}
    grouped = {}
    for o in observations:
        key = o["provisional_identity"] or identities_by_source.get(o["canonical_url"]) or "source:" + o["canonical_url"]
        grouped.setdefault(key, []).append(o)
    results = []
    for identity, rows in grouped.items():
        official = [o for o in rows if o["authority"] == "PASS"]
        exact = {o["time"]["calculated_utc"] for o in official if o["state"] == "EXACT"}
        dates = {o["time"]["date"] for o in official if o["state"] == "DATE_CONFIRMED"}
        # All approved time profiles have the same NA date context for these dates.
        conflict = (len(exact) > 1 or len(dates) > 1 or
                    any(o["state"] == "OFFICIAL_CONFLICT" for o in official) or
                    bool(dates and any(o["time"]["date"] not in dates for o in official if o["state"] == "EXACT")))
        uncertain = any(o["state"] not in {"EXACT", "DATE_CONFIRMED"} for o in rows)
        accepted = next(iter(exact)) if len(exact) == 1 and not conflict and not uncertain else None
        state = ("OFFICIAL_CONFLICT" if conflict else "MACHINE_VERIFIED" if accepted else
                 "DATE_CONFIRMED" if dates and not uncertain else "CANONICAL_PENDING")
        results.append(dict(provisional_identity=identity, expansion=rows[0]["expansion"], state=state,
                            accepted_exact_timestamp=accepted, canonical_status="CANONICAL_PENDING",
                            identity_status="IDENTITY_UNMAPPED", verification_method="MACHINE_POLICY",
                            corroboration="CORROBORATED" if accepted and len({o["canonical_url"] for o in official if o["state"] == "EXACT"}) > 1 else None,
                            observation_ids=[o["observation_id"] for o in rows],
                            reasons=sorted({o["state"] for o in rows if o["state"] not in {"EXACT", "DATE_CONFIRMED"}})))
    return results


def revision(previous, current):
    if not integrity(current) or previous and not integrity(previous):
        raise ValueError("EVIDENCE_INTEGRITY_FAILED")
    if previous is None:
        return "DISCOVERED", False
    if previous["observation_id"] == current["observation_id"]:
        return "UNCHANGED", False
    def material(o):
        return (o["authority"], o["provisional_identity"], o["event_kind"], o["state"],
                o["time"].get("calculated_utc") if o["time"] else None,
                o["time"].get("date") if o["time"] else None)
    return "SOURCE_CHANGED", material(previous) != material(current)
