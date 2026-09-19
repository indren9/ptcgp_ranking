"""Frozen R3 human decisions, explicitly above (and never inside) C1.

This is not an automatic UTC-precedence rule. Only the three decisions approved
by Chat Madre/user in GATE_1_11_D2_R3 are admissible. Constructing a provenance
record does not approve it: the persisted individual attestation is required.
"""
from copy import deepcopy
from datetime import datetime
import re

from official_release_observer.parsing import sha, stable
from official_release_observer.timeparse import extract_time

POLICY_VERSION = "pokemon-explicit-utc-human-1"
APPROVAL_GATE = "GATE_1_11_D2_R3"
APPROVAL_SOURCE = "Chat Madre/user"
APPROVAL_REQUEST_SHA256 = "39839143c09588fb8af4a85ae2dbffc1ecb1b24b8aa47684341907d0f9868675"
APPROVED_STARTS = {
    "PAF": "2024-01-25T17:00:00Z",
    "SSP": "2024-11-07T17:00:00Z",
    "PRE": "2025-01-16T17:00:00Z",
}
RATIONALES = {
    "PAF": "The full-expansion announcement publishes 17:00 UTC with a seasonally inconsistent PDT label. No competing first-party exact UTC was found in the bounded research. The human reviewer explicitly selects the published UTC.",
    "SSP": "The full-expansion announcement publishes 17:00 UTC while local 10:00 PT corresponds to 18:00 UTC. No competing first-party exact UTC was found in the bounded research. The human reviewer explicitly selects the published UTC.",
    "PRE": "The independent December 3 Pokemon letter confirms January 16, 2025. The human reviewer explicitly combines that date with the full-expansion release post's 17:00 UTC, preserving its calendar/local-clock contradictions. The Parade 18:00 event clock is not full-expansion availability. No competing first-party exact UTC was found in the bounded research.",
}


def decision_record(ledger, code):
    """Build the source-bound decision template with approval FALSE by default.

    The compiler has already verified the unchanged C1 observations/statements.
    The explicit request fixes the only allowed instants; an empirical bound or
    historical release-hour pattern cannot authorize a new decision here.
    """
    if code not in APPROVED_STARTS:
        raise ValueError("R3 human policy is restricted to PAF / SSP / PRE")
    entry = next(e for e in ledger["inventory"] if e["event_id"] == code)
    statements = [s for s in ledger["temporal_statements"] if s["event_id"] == code]
    release = [s for s in statements if "full_expansion" in s["proposition_kinds"]
               and s["source_declared_utc"]]
    if len(release) != 1:
        raise ValueError("R3 requires one original full-expansion UTC statement")
    s = release[0]
    source = next(o for o in ledger["observations"] if o["observation_id"] == s["source_observation_id"])
    stamp = APPROVED_STARTS[code]
    if (source["authority"] != "PASS" or source["event_kind"] not in {"expansion", "combined"}
            or s["source_observation_id"] not in entry["exact_evidence_ids"]
            or s["source_declared_utc"] != stamp[11:16] + " UTC"
            or source["state"] != ("INVALID_LOCAL_TIME" if code == "PAF" else "OFFICIAL_CONFLICT")):
        raise ValueError("R3 decision is not bound to its original conflicting official release")
    # This check is independent of the source fingerprint below: a competing
    # source invalidates this approval, rather than being overridden by it.
    for other in ledger["observations"]:
        if (other["authority"] == "PASS" and other["event_kind"] in {"expansion", "combined"}
                and other["expansion"] == entry["official_source_expansion"] and other.get("time")):
            time = other["time"]
            explicit = time.get("explicit_utc")
            if ((other["state"] == "EXACT" and time["calculated_utc"] != stamp)
                    or explicit and explicit not in {"17:00", "17:00:00"}):
                raise ValueError("competing first-party exact UTC requires a new human decision")
    corroboration = []
    if code == "PRE":
        dates = [row for row in statements if row["proposition_kinds"] == ["full_expansion"]
                 and row["time"]["state"] == "DATE_CONFIRMED"
                 and row["time"]["date"] == stamp[:10]]
        if len(dates) != 1:
            raise ValueError("PRE human decision requires the independent official release date")
        date = dates[0]
        official = next(o for o in ledger["observations"] if o["observation_id"] == date["source_observation_id"])
        if official["authority"] != "PASS" or official["canonical_url"] == source["canonical_url"]:
            raise ValueError("PRE date corroboration must be independent first-party evidence")
        corroboration.append(dict(statement_id=date["statement_id"], observation_id=official["observation_id"],
            url=official["canonical_url"], proposition=date["proposition"],
            source_expression=date["parser_input"], confirmed_date=date["time"]["date"]))
    elif s["time"]["date"] != stamp[:10]:
        raise ValueError("approved date differs from the official full-expansion date")
    # Diagnostic only: the raw C1 local conversion (including failure) is retained
    # separately from a clearly labelled hypothetical Pacific civil conversion.
    clock = re.search(r"(\d{1,2}:\d{2} AM) (?:PDT|PT)\b", s["parser_input"])
    if not clock:
        raise ValueError("original local clock missing")
    diagnostic_input = datetime.fromisoformat(stamp[:10]).strftime("%B %d, %Y") + " - " + clock[1] + " PT"
    diagnostic = extract_time(diagnostic_input)
    conflicting_fields = {
        "PAF": ["January date versus PDT seasonal label"],
        "SSP": ["10:00 PT converts to 18:00 UTC, versus published 17:00 UTC"],
        "PRE": ["Literal January 16, 2024 is Tuesday, not Thursday", "Independent release date is 2025-01-16, not literal 2024", "10:00 Pacific in winter converts to 18:00 UTC, versus published 17:00 UTC"],
    }[code]
    payload = dict(event_id=code, status="HUMAN_ADJUDICATED", authority_class="HUMAN_ADJUDICATED",
        original_source_url=source["canonical_url"], original_source_expression=s["parser_input"],
        source_observation_id=source["observation_id"], source_statement_id=s["statement_id"],
        source_content_digest=source["content_digest"], c1_machine_state=source["state"],
        machine_catalog_status="CANONICAL_PENDING" if code == "PAF" else "OFFICIAL_CONFLICT",
        local_clock_diagnostic=deepcopy(s["local_clock_diagnostic"]),
        hypothetical_pacific_diagnostic=dict(parser_input=diagnostic_input, result=diagnostic,
            authority=False, note="Diagnostic only on the approved date; original PDT/calendar fields are not repaired."),
        explicit_utc=s["source_declared_utc"], conflicting_fields=conflicting_fields,
        corroborating_official_sources=corroboration, policy_version=POLICY_VERSION,
        approved_canonical_utc=stamp, rationale=RATIONALES[code], approval_source=APPROVAL_SOURCE,
        approval_gate=APPROVAL_GATE, approval_request_sha256=APPROVAL_REQUEST_SHA256,
        authorization_basis="EXPLICIT_USER_DECISION_ON_FIRST_PARTY_UTC",
        research_ledger_sha256=sha(stable(ledger)),
        competing_first_party_exact_utc_found=False,
        research_scope="Bounded R1/R2 research; blocked editorial sources remain a coverage limitation.",
        limitless_used_for_time=False, historical_pattern_used=False)
    return dict(payload, adjudication_id=sha(stable(payload)), adjudication_reviewed=False)


def apply_human_adjudications(ledger, entries, register):
    """Overlay explicitly approved decisions; never change a machine assessment."""
    if register is None:
        return entries
    if (register.get("schema_version") != 1 or register.get("catalog_reviewed") is not False
            or register.get("policy_version") != POLICY_VERSION):
        raise ValueError("invalid human adjudication register; catalog must remain unreviewed")
    rows = register.get("decisions")
    if not isinstance(rows, list) or len({r["event_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate/malformed human adjudication decisions")
    result = deepcopy(entries)
    by_code = {e["event_id"]: e for e in result}
    for row in rows:
        expected = decision_record(ledger, row["event_id"])
        # Approval is a separate explicit attestation, not generated from evidence
        # integrity. Removing it leaves the original machine-blocked state.
        provided = {k: v for k, v in row.items() if k != "adjudication_reviewed"}
        required = {k: v for k, v in expected.items() if k != "adjudication_reviewed"}
        if provided != required:
            raise ValueError("human adjudication provenance/authorization differs from the frozen R3 decision")
        approval = row.get("adjudication_reviewed", False)
        if type(approval) is not bool:
            raise ValueError("individual human approval must be an explicit boolean")
        if not approval:
            continue
        entry = by_code[row["event_id"]]
        if (entry["status"] != row["machine_catalog_status"] or not entry["mapped"]
                or entry["machine_verified_utc"] is not None):
            raise ValueError("human decision cannot replace a different machine assessment")
        entry.update(machine_status=entry["status"], status="HUMAN_ADJUDICATED",
            start_utc=row["approved_canonical_utc"], confirmed_date=row["approved_canonical_utc"][:10],
            adjudication_reviewed=True, human_adjudication=deepcopy(row))
    return result


def render_human_adjudications(entries):
    rows = [e for e in entries if e["status"] == "HUMAN_ADJUDICATED"]
    if not rows:
        return []
    lines = ["", "## R3 human adjudication — approved individual decisions", "",
        "RAW OFFICIAL EVIDENCE → unchanged C1 MACHINE ASSESSMENT → HUMAN ADJUDICATION → CANONICAL PROPOSED BOUNDARY.", "",
        f"Approval source: **{APPROVAL_SOURCE}**; gate `{APPROVAL_GATE}`; policy `{POLICY_VERSION}`. The individual `adjudication_reviewed=true` flags do not attest the complete catalog, which remains `reviewed=false`. No Limitless evidence or historical timing pattern selected these instants.", ""]
    for e in rows:
        h = e["human_adjudication"]
        local = h["local_clock_diagnostic"]["calculated_utc"] or h["local_clock_diagnostic"]["state"]
        lines += [f"### {e['event_id']} — HUMAN_ADJUDICATED — {e['start_utc']}", "",
            f"- Original [official release]({h['original_source_url']}): `{h['original_source_expression']}`.",
            f"- Original C1 state: `{h['c1_machine_state']}`; machine catalog status: `{h['machine_catalog_status']}`; machine-verified UTC remains null.",
            f"- Original local-clock diagnostic: `{local}`. Explicit UTC: `{h['explicit_utc']}`.",
            "- Preserved conflicts: " + "; ".join(h["conflicting_fields"]) + ".",
            "- Human rationale: " + h["rationale"],
            f"- Decision ID: `{h['adjudication_id']}`; `adjudication_reviewed=true`."]
        for source in h["corroborating_official_sources"]:
            lines.append(f"- Independent [official date evidence]({source['url']}): `{source['confirmed_date']}`; date only, not an event clock.")
        lines.append("")
    return lines
