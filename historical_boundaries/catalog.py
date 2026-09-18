"""Offline catalog reconciliation around the C1 observer and D1 boundary loader.

Unknown expansion starts remain null. Rotations are audited metadata and never
split the expansion windows or supply their missing timestamps.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from historical_rebuild.model import load_windows, NotReady
from official_release_observer import AUTHORITY_POLICY_VERSION, PARSER_VERSION, SOURCE_ADAPTER_VERSION
from official_release_observer.parsing import integrity, normalized, safe_url, seal, sha
from official_release_observer.reconcile import reconcile
from official_release_observer.timeparse import extract_time
from .adjudication import verify_statements, render_adjudications

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data/candidates/releases"
LEDGER = DATA / "tcg_live_historical_boundary_evidence.json"
CANDIDATE = DATA / "tcg_live_historical_boundaries_candidate.json"
PREVIEW = DATA / "tcg_live_historical_window_preview.json"
REVIEW = BASE / "docs/tcg-live-historical-boundary-review.md"

# Names/identities only. Order is independently reconciled against the official
# forum inventory and card identity index; it is never a source of release times.
# Two full expansions (BLK/WHT) share an explicitly announced release instant.
REQUIRED_EXPANSIONS = (
    ("SVI", "Scarlet & Violet"), ("PAL", "Paldea Evolved"), ("OBF", "Obsidian Flames"),
    ("MEW", "151"), ("PAR", "Paradox Rift"), ("PAF", "Paldean Fates"),
    ("TEF", "Temporal Forces"), ("TWM", "Twilight Masquerade"), ("SFA", "Shrouded Fable"),
    ("SCR", "Stellar Crown"), ("SSP", "Surging Sparks"), ("PRE", "Prismatic Evolutions"),
    ("JTG", "Journey Together"), ("DRI", "Destined Rivals"), ("BLK", "Black Bolt"),
    ("WHT", "White Flare"), ("MEG", "Mega Evolution"), ("PFL", "Phantasmal Flames"),
    ("ASC", "Ascended Heroes"), ("POR", "Perfect Order"), ("CRI", "Chaos Rising"),
    ("PBL", "Pitch Black"), ("30C", "30th Celebration"),
)
REQUIRED_ROTATIONS = tuple((f"ROTATION_{year}", f"Standard rotation {year}") for year in range(2023, 2027))
C0_EXPECTATIONS = {
    "SVI": "2023-03-30T17:00:00Z", "MEG": "2025-09-25T17:00:00Z",
    "PFL": "2025-11-13T17:00:00Z", "ASC": "2026-01-29T17:00:00Z",
    "POR": "2026-03-26T17:00:00Z", "CRI": "2026-05-21T17:00:00Z",
    "PBL": "2026-07-16T17:00:00Z", "30C": "2026-09-15T17:00:00Z",
}


def identity_name(name):
    value = re.sub(r"\s*—\s*", "—", normalized(name))
    value = re.sub(r"^(?:Pokémon TCG:\s*|Scarlet & Violet—|Mega Evolution—)", "", value)
    # The identity index labels the official Scarlet & Violet—151 set as
    # Pokémon 151. This exact alias is identity-only, never temporal evidence.
    return {"pokémon 151": "151"}.get(value.casefold(), value.casefold())


def project_observation(observation, *, context_excerpt="", rotation_binding_excerpt=""):
    """Minimize a verified C1 record without misrepresenting the original digest.

    The original observation ID/full selected-excerpt digest remain references.
    The projection is separately sealed, so C1 integrity/reconciliation can still
    run offline. Full captures and original observations stay in ignored storage.
    """
    if not integrity(observation):
        raise ValueError("invalid original observer evidence")
    if len(context_excerpt.split()) > 25:
        raise ValueError("context excerpt must be minimal (at most 25 words)")
    result = deepcopy(observation)
    result["origin_observation_id"] = observation["observation_id"]
    result["origin_excerpt_sha256"] = observation["excerpt_sha256"]
    result["projection_policy"] = "historical-ledger-minimal-1"
    binding = re.fullmatch(r"The (\d{4}) Pokémon TCG Standard format goes into effect\.", rotation_binding_excerpt)
    if rotation_binding_excerpt and (not binding or observation["event_kind"] != "combined"):
        raise ValueError("rotation binding requires the explicit combined-event proposition")
    result["rotation_binding_excerpt"] = rotation_binding_excerpt
    result["bound_rotation_years"] = [int(binding[1])] if binding else []
    # A time expression is a factual excerpt, not a full announcement body.
    result["excerpt"] = observation["time"]["raw_local"] if observation.get("time") else context_excerpt
    result["excerpt_sha256"] = sha(result["excerpt"])
    result["human_reviewed"] = False
    return seal(result)


def verify_observation(record):
    if not integrity(record) or record.get("human_reviewed") is not False:
        raise ValueError("evidence integrity/review flag failure")
    if record.get("projection_policy") != "historical-ledger-minimal-1":
        raise ValueError("unsupported evidence projection")
    for field in ("content_digest", "metadata_fingerprint", "origin_observation_id", "origin_excerpt_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", record.get(field, "")):
            raise ValueError("missing evidence digest: " + field)
    if (record["authority_policy_version"], record["parser_version"], record["source_adapter_version"]) != (
        AUTHORITY_POLICY_VERSION, PARSER_VERSION, SOURCE_ADAPTER_VERSION
    ):
        raise ValueError("stale observer policy/parser/adapter version")
    time = record.get("time")
    if time and extract_time(time["raw_local"]) != time:
        raise ValueError("time evidence does not match the frozen C1 conversion/tzdb")
    if time and (record["state"] != time["state"] or
                 time["tzdb_version"] and record["tzdb_version"] != time["tzdb_version"]):
        raise ValueError("observer state/tzdb differs from time evidence")
    if not safe_url(record["canonical_url"]):
        raise ValueError("non-first-party timestamp source")
    binding = re.fullmatch(r"The (\d{4}) Pokémon TCG Standard format goes into effect\.", record.get("rotation_binding_excerpt", ""))
    if record.get("bound_rotation_years") != ([int(binding[1])] if binding else []):
        raise ValueError("rotation year differs from explicit source proposition")


def assess(entry, observations):
    ids = entry.get("exact_evidence_ids", [])
    evidence = [observations[oid] for oid in ids]
    expected_name = entry.get("official_source_expansion")
    for record in evidence:
        if record["expansion"] != expected_name:
            raise ValueError("evidence/expansion identity mismatch: " + entry["event_id"])
        if record["event_kind"] not in {"expansion", "combined"}:
            raise ValueError("source is not a full-expansion availability event")
        if entry["kind"] == "expansion" and identity_name(entry["name"]) not in {
            identity_name(part) for part in record["expansion"].split(" and ")
        }:
            raise ValueError("source expansion does not name the catalog identity")
        if entry["kind"] == "rotation" and (
            record["event_kind"] != "combined" or entry["rotation_year"] not in record.get("bound_rotation_years", [])
        ):
            raise ValueError("rotation is not explicitly bound to the expansion instant")
    results = reconcile(evidence) if evidence else []
    if len(results) > 1:
        raise ValueError("multiple unreviewed identities in one event")
    result = results[0] if results else {}
    verified = result.get("accepted_exact_timestamp")
    state = result.get("state", "CANONICAL_PENDING")
    if not evidence and not entry.get("context_evidence_ids"):
        state = "SOURCE_UNAVAILABLE"
    if any(o["authority"] != "PASS" for o in evidence):
        state = "AUTHORITY_UNVERIFIED"
    confirmed_date = verified[:10] if verified else None
    date_metadata = entry.get("rotation_date_evidence")
    if date_metadata:
        if entry["kind"] != "rotation":
            raise ValueError("rotation date metadata cannot authorize an expansion")
        source = observations[date_metadata["source_observation_id"]]
        year = str(entry["rotation_year"])
        raw_date = date_metadata["raw_date"]
        # The year is explicitly retained from the official letter title. This
        # is date-only context, never a clock inferred from a nearby event.
        if (source["authority"] != "PASS" or raw_date not in source["excerpt"]
                or not re.search(r"\b" + year + r"\b", source["title"] or "")
                or date_metadata["year_context"] != source["title"]):
            raise ValueError("rotation date lacks official source/year context")
        parsed = extract_time(raw_date + ", " + year)
        if parsed["state"] != "DATE_CONFIRMED" or parsed["date"] != date_metadata["date"]:
            raise ValueError("rotation date metadata must remain date-only")
        confirmed_date = parsed["date"]
        if state == "CANONICAL_PENDING":
            state = "DATE_CONFIRMED"
    mapping = entry.get("identity", {})
    mapped = mapping.get("status") == "MAPPED"
    if entry["kind"] == "expansion":
        mapped = (mapped and mapping.get("code") == entry["event_id"]
                  and mapping.get("source") == "https://limitlesstcg.com/cards"
                  and mapping.get("identity_url") == "https://limitlesstcg.com/cards/" + entry["event_id"]
                  and identity_name(mapping.get("source_name", "")) == identity_name(entry["name"])
                  and bool(re.fullmatch(r"[a-f0-9]{64}", mapping.get("source_sha256", "")))
                  and mapping.get("time_authority") is False)
    if not mapped:
        state = "IDENTITY_UNMAPPED"
    return dict(status=state, machine_verified_utc=verified, confirmed_date=confirmed_date,
                start_utc=verified if mapped and state == "MACHINE_VERIFIED" else None,
                found_official=bool(evidence or entry.get("context_evidence_ids")),
                exact_time_verified=bool(verified), mapped=mapped,
                conflict=result.get("state") == "OFFICIAL_CONFLICT", missing=not bool(evidence or entry.get("context_evidence_ids")),
                observer_states=sorted({o["state"] for o in evidence}),
                corroboration=result.get("corroboration"))


def compile_catalog(ledger):
    if ledger.get("schema_version") != 1 or ledger.get("reviewed") is not False:
        raise ValueError("ledger must remain explicitly unreviewed")
    records = ledger["observations"]
    for observation in records:
        verify_observation(observation)
    observations = {o["observation_id"]: o for o in records}
    if len(observations) != len(records):
        raise ValueError("duplicate observation ID")
    verify_statements(ledger, observations)
    inventory = ledger["inventory"]
    by_id = {e["event_id"]: e for e in inventory}
    if len(by_id) != len(inventory):
        raise ValueError("duplicate inventory identity")
    required = dict(REQUIRED_EXPANSIONS + REQUIRED_ROTATIONS)
    if set(by_id) != set(required):
        raise ValueError("inventory has missing/unexpected required identities: " + str(sorted(set(by_id) ^ set(required))))
    entries = []
    for event_id, name in REQUIRED_EXPANSIONS + REQUIRED_ROTATIONS:
        item = deepcopy(by_id[event_id])
        if item["name"] != name or item["kind"] != ("rotation" if event_id.startswith("ROTATION_") else "expansion"):
            raise ValueError("inventory identity/kind mismatch")
        for oid in item.get("exact_evidence_ids", []) + item.get("context_evidence_ids", []):
            if oid not in observations:
                raise ValueError("missing evidence reference")
        item.update(assess(item, observations))
        entries.append(item)
    by_id = {e["event_id"]: e for e in entries}
    boundaries = []
    # D2-R1: one MARS per expansion release window. Rotations remain in the
    # ledger/review; neither unknown nor distinct rotation times split windows.
    for event_id, _ in REQUIRED_EXPANSIONS:
        entry = by_id[event_id]
        ids = entry.get("exact_evidence_ids") or entry.get("context_evidence_ids") or []
        source = observations[ids[0]]["canonical_url"] if ids else entry.get("attempted_source_url", "")
        boundaries.append(dict(event_id=entry["event_id"], name=entry["name"], kind="expansion",
            start_utc=entry["start_utc"], reasons=["expansion"],
            evidence=dict(url=source, reviewed=False, exact_time=bool(entry["start_utc"]))))
    if all(b["start_utc"] for b in boundaries):
        boundaries.sort(key=lambda b: (b["start_utc"], b["event_id"]))
    for entry in entries:
        if entry["kind"] == "rotation":
            entry["coincident_expansion_ids"] = [b["event_id"] for b in boundaries
                if entry["start_utc"] and b["start_utc"] == entry["start_utc"]]
    # Editorial discovery limitations remain visible in the audit; readiness
    # follows the required expansion identities and their accepted starts.
    complete = all(by_id[code]["status"] == "MACHINE_VERIFIED" for code, _ in REQUIRED_EXPANSIONS)
    candidate = dict(schema_version=1, reviewed=False, boundaries=boundaries,
                     window_unit="expansion",
                     catalog_status="COMPLETE_UNREVIEWED" if complete else "INCOMPLETE_NOT_READY")
    preview = compatibility_preview(candidate, complete)
    return candidate, entries, preview


def compatibility_preview(candidate, complete):
    # Even hypothetical review cannot excuse a missing boundary. The persisted
    # candidate is never mutated, and no reviewed file is emitted.
    hypothetical = deepcopy(candidate)
    if any(b["kind"] != "expansion" or b["reasons"] != ["expansion"] for b in hypothetical["boundaries"]):
        raise ValueError("D2-R1 runner input must contain expansion events only")
    hypothetical["reviewed"] = True
    for boundary in hypothetical["boundaries"]:
        boundary["evidence"]["reviewed"] = True
    try:
        windows = load_windows(hypothetical)
    except (NotReady, ValueError) as exc:
        return dict(status="NOT_READY", d1_compatibility="BLOCKED_BY_UNRESOLVED_BOUNDARY",
                    windows=[], reason=str(exc), skipped_gaps=False)
    if not complete:
        return dict(status="NOT_READY", d1_compatibility="EXPANSION_SEQUENCE_INCOMPLETE",
                    windows=[], reason="required expansion sequence incomplete", skipped_gaps=False)
    return dict(status="UNREVIEWED_PREVIEW", d1_compatibility="PASS_IN_MEMORY_ONLY",
                windows=[w.definition() for w in windows], skipped_gaps=False)


def render_review(ledger, candidate, entries, preview):
    observations = {o["observation_id"]: o for o in ledger["observations"]}
    expansion = [e for e in entries if e["kind"] == "expansion"]
    rotations = [e for e in entries if e["kind"] == "rotation"]
    ready = candidate["catalog_status"] == "COMPLETE_UNREVIEWED"
    verdict = "READY_FOR_HUMAN_REVIEW" if ready else "NOT_READY"
    lines = ["# TCG Live historical boundary review — Gate 1.11-D2-R1", "",
        f"**{verdict} — candidate reviewed=false; no human approval, promotion or historical rebuild.**", "",
        "Coverage: Scarlet & Violet (2023-03-30) through 30th Celebration (2026-09-15), current OPEN.",
        f"Expected: {len(expansion)} full-expansion identities, 22 expansion release instants (Black Bolt / White Flare share one), and {len(rotations)} Standard rotations.",
        f"Exact machine-verified event records: {sum(e['exact_time_verified'] for e in expansion)} expansions; {sum(e['exact_time_verified'] for e in rotations)} Standard rotation.",
        "Methodology: **one MARS per expansion release window**. Only expansion starts determine contiguity. Rotations remain audited metadata and never create extra windows or supply expansion timestamps.",
        "The expansion sequence is complete, awaiting human review." if ready else
        "The expansion sequence is incomplete. Unknown expansion starts remain null; **no windows are derived across gaps**. Unknown rotation times do not block it.", "",
        "## Chronological expansion inventory", "",
        "Identity order is independently reconciled; pending rows do not assert an exact timestamp. Local time below is the source expression, including its errors.", "",
        "| Event | Type | Code | Official local time | Verified UTC | Primary official source | Secondary source | Machine authority | Identity | Conflict / status |",
        "|---|---|---|---|---|---|---|---|---|---|"]
    for e in expansion + rotations:
        if e == rotations[0]:
            lines += ["", "## Audited rotations — metadata only", "",
                "These records stay outside the runner boundary array. A confirmed date is not midnight or an exact UTC instant.", "",
                "| Event | Type | Code | Official local time / date | Verified UTC | Primary official source | Secondary source | Machine authority | Identity | Conflict / status |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        ids = e.get("exact_evidence_ids") or e.get("context_evidence_ids") or []
        source = observations[ids[0]] if ids else None
        temporal = source.get("time") if source and e.get("exact_evidence_ids") else None
        local = temporal["raw_local"] if temporal else (
            "Date only: " + e["confirmed_date"] if e.get("confirmed_date") else "Exact time not established")
        url = source["canonical_url"] if source else e.get("attempted_source_url", "")
        authority = source["authority"] if source else "UNVERIFIED"
        lines.append(f"| {e['name']} | {e['kind']} | {e['identity'].get('code') or e['event_id']} | {local} | {e['machine_verified_utc'] or '—'} | [official]({url}) | unavailable / not corroborated | {authority} | {'MAPPED' if e['mapped'] else 'UNMAPPED'} | {e['status']} |")
    lines += ["", "## Timezone conversion", "",
              "All exact values below come from C1 `extract_time`, using versioned `tzdata:2026.3` and America/Los_Angeles. No default hour, midnight or Limitless release date is used.", "",
              "| Event | Declared zone | Parsed local | Calculated UTC | Explicit UTC clock |", "|---|---|---|---|---|"]
    for e in entries:
        if not e["exact_time_verified"]:
            continue
        source = observations[e["exact_evidence_ids"][0]]
        t = source["time"]
        lines.append(f"| {e['name']} | {t['raw_zone']} | {t['local']} | {t['calculated_utc']} | {t['explicit_utc'] or 'not supplied'} |")
    lines += ["", "## Nontrivial findings and blockers", ""]
    for e in entries:
        for note in e.get("notes", []):
            lines.append(f"- **{e['name']}:** {note}")
    lines += render_adjudications(ledger, entries, observations)
    lines += ["", "## Independent inventory reconciliation", "",
              "A: official forum RSS + advertised category sitemap + landing. B: Pokémon.com news landing and attempted official article/sitemap inventory. C: project/Limitless identity-only inventory.", "",
              "| Identity | EXPECTED | FOUND_OFFICIAL | EXACT_TIME_VERIFIED | MAPPED | CONFLICT | MISSING |",
              "|---|---|---|---|---|---|---|"]
    for e in entries:
        lines.append(f"| {e['name']} | yes | {e['found_official']} | {e['exact_time_verified']} | {e['mapped']} | {e['conflict']} | {e['missing']} |")
    lines += ["", *ledger["coverage"]["notes"], "", "## Pre-scope inventory", ""]
    for item in ledger["pre_scope"]:
        lines.append(f"- [{item['name']}]({item['url']}): {item['status']}. Outside required rebuild scope; no exact boundary accepted or scope extension.")
    lines += ["", "## Candidate and derived-window preview", "",
        "- Candidate: `data/candidates/releases/tcg_live_historical_boundaries_candidate.json`.",
        "- Evidence ledger: `data/candidates/releases/tcg_live_historical_boundary_evidence.json`.",
        "- Preview: `data/candidates/releases/tcg_live_historical_window_preview.json`.",
        f"- D1 compatibility of the actual candidate, with review flags temporarily true **only in memory**: `{preview['d1_compatibility']}`.",
        "- No complete window list is emitted while required expansion starts remain unresolved. Rotation timing and secondary discovery limitations remain visible audit metadata; they do not split/block an otherwise complete expansion sequence.",
        "- The latest known expansion is `30C`: `[2026-09-15T17:00:00Z, OPEN)`. No observation cutoff is part of this catalog.",
        "- Perfect Order / rotation 2026 coincidence is retained in ledger metadata (`coincident_expansion_ids=[POR]`); the runner gets only the expansion event. Rotations 2023/2024/2025 are never assumed coincident.",
        "- Black Bolt and White Flare retain both expansion identities at their shared official release instant. D1 groups that joint release into one window without a zero-length interval. No rotation creates an extra MARS/window.",
        "", "## Provenance and review procedure", "",
        "Each ledger observation is a separately checksummed minimal projection of a C1 observation. It retains the original observation ID/excerpt digest, normalized source content digest, metadata fingerprint, author/role/publication/edit evidence, parser/adapter/policy/tzdb versions and retrieval provenance. Raw captures/full selected excerpts remain ignored local research evidence; full copyrighted announcements are not committed.",
        "The only observer change is whitespace equivalence around the em dash in expansion names (`live-release-2`), demonstrated by Shrouded Fable. The time parser and authority policy are unchanged. The former parser observation is retained as a predecessor reference, not portrayed as an official source correction.",
        "No official correction resolving the three inconsistent expansion timestamps was established. Blocked Pokémon.com sources may contain additional evidence; absence of conflict there is not claimed. Human review must resolve expansion holes/conflicts, inspect the coverage limitations and explicitly approve a separate runner-ready input. Unknown rotation times remain metadata.",
        "", "## Offline validation", "",
        "Run `python -m historical_boundaries` to verify ledger integrity, regenerate the candidate/preview in memory and compare committed outputs. It performs no network requests and invokes only the D1 boundary loader, never NEXT/ALL/WINDOW. Run `python -m historical_boundaries --write` to regenerate the fixed unreviewed artifacts from the ledger after reviewed research changes; it cannot write active catalogs.",
        "", f"**Verdict: {verdict}.**", ""]
    return "\n".join(lines)


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def artifacts(ledger):
    candidate, entries, preview = compile_catalog(ledger)
    return {CANDIDATE: serialize(candidate), PREVIEW: serialize(preview),
            REVIEW: render_review(ledger, candidate, entries, preview)}
