"""Audited propositions, kept separate from accepted expansion boundaries.

All clock conversion delegates to C1. A valid event/maintenance clock is never
promoted to expansion availability, and diagnostic local UTC does not resolve a
contradictory explicit UTC statement.
"""
import re

from official_release_observer.parsing import sha, stable
from official_release_observer.timeparse import PATTERN, extract_time

KINDS = {"full_expansion", "battle_pass", "ladder", "event", "maintenance", "patch_support"}


def statement(source, *, event_id, proposition_kinds, proposition,
              source_fragments, assembly="literal", notes=""):
    if source["authority"] != "PASS":
        raise ValueError("statement requires verified first-party authority")
    if event_id not in {"PAF", "SSP", "PRE"} or not proposition_kinds or not set(proposition_kinds) <= KINDS:
        raise ValueError("unknown adjudication identity/proposition kind")
    if not source_fragments or any(not f or f not in source["excerpt"] for f in source_fragments):
        raise ValueError("time fragment is not retained in the source excerpt")
    if assembly == "literal" and len(source_fragments) == 1:
        parser_input = source_fragments[0]
    elif assembly == "same_maintenance_paragraph" and len(source_fragments) == 2:
        if set(proposition_kinds) != {"maintenance"}:
            raise ValueError("maintenance clock cannot be assembled as expansion evidence")
        parser_input = source_fragments[0] + " - " + source_fragments[1]
    elif assembly == "date_with_official_title_year" and len(source_fragments) == 1:
        years = set(re.findall(r"\b20\d{2}\b", source["title"] or ""))
        if len(years) != 1:
            raise ValueError("date context requires one explicit official title year")
        parser_input = source_fragments[0] + ", " + years.pop()
        if extract_time(parser_input)["state"] != "DATE_CONFIRMED":
            raise ValueError("contextual year is restricted to date-only evidence")
    else:
        raise ValueError("unsupported source-field assembly")
    parsed = extract_time(parser_input)
    # This separate calculation diagnoses contradictions. It is not accepted UTC.
    local_input = re.split(r"\s*\(", parser_input, maxsplit=1)[0]
    local_diagnostic = extract_time(local_input)
    declaration = PATTERN.fullmatch(local_input.strip())
    zone = declaration["zone"] if declaration else None
    explicit = re.search(r"\(([^)]*UTC)\)", parser_input)
    eligible = ("full_expansion" in proposition_kinds
                and source["event_kind"] in {"expansion", "combined"}
                and source["state"] == "EXACT"
                and source.get("time") == parsed)
    data = dict(event_id=event_id, source_observation_id=source["observation_id"],
                proposition_kinds=sorted(set(proposition_kinds)), proposition=proposition,
                source_fragments=source_fragments, assembly=assembly, parser_input=parser_input,
                time=parsed, local_clock_diagnostic=local_diagnostic,
                source_declared_zone=zone.upper() if zone else None,
                source_declared_utc=explicit[1] if explicit else None,
                eligible_for_expansion=eligible, notes=notes, reviewed=False)
    return {**data, "statement_id": sha(stable(data))}


def verify_statements(ledger, observations):
    statements = ledger.get("temporal_statements", [])
    by_id = {s["statement_id"]: s for s in statements}
    if len(by_id) != len(statements):
        raise ValueError("duplicate temporal statement")
    for item in statements:
        source = observations[item["source_observation_id"]]
        expected = statement(source, **{key: item[key] for key in (
            "event_id", "proposition_kinds", "proposition", "source_fragments", "assembly", "notes")})
        if item != expected:
            raise ValueError("temporal statement integrity/conversion failed")
    for event_id, audit in ledger.get("adjudications", {}).items():
        if event_id not in {"PAF", "SSP", "PRE"}:
            raise ValueError("unexpected adjudication identity")
        for sid in audit["statement_ids"]:
            if sid not in by_id or by_id[sid]["event_id"] != event_id:
                raise ValueError("adjudication source/identity mismatch")


def render_adjudications(ledger, entries, observations):
    if not ledger.get("adjudications"):
        return []
    statements = {s["statement_id"]: s for s in ledger["temporal_statements"]}
    by_event = {e["event_id"]: e for e in entries}
    lines = ["", "## D2-R1: PAF / SSP / PRE adjudication", "",
        "The following is the preserved R1 machine assessment, before R3 human adjudication. Calculated local UTC is diagnostic when the source has a conflicting explicit UTC or invalid date. Only a conflict-free full-expansion proposition can authorize machine acceptance. Event, Battle Pass, Ladder and maintenance clocks are not replacements.", ""]
    for code in ("PAF", "SSP", "PRE"):
        audit = ledger["adjudications"][code]
        machine_status = by_event[code].get("machine_status", by_event[code]["status"])
        lines += [f"### {code} — R1 machine result: {machine_status}", "", audit["conclusion"], "",
            "| Official source / authority | Proposition | Source time fields | Zone | Local-clock UTC (diagnostic) | Explicit UTC | C1 state |",
            "|---|---|---|---|---|---|---|"]
        for sid in audit["statement_ids"]:
            s = statements[sid]
            o = observations[s["source_observation_id"]]
            t, local = s["time"], s["local_clock_diagnostic"]
            lines.append(f"| [{o['source_id']}]({o['canonical_url']}) / {o['authority']} | {s['proposition']} | {' … '.join(s['source_fragments'])} | {s['source_declared_zone'] or '—'} | {local['calculated_utc'] or '—'} | {s['source_declared_utc'] or 'not supplied'} | {t['state']} |")
        lines += [""]
        for sid in audit["statement_ids"]:
            s = statements[sid]
            if s["notes"]:
                lines.append("- " + s["notes"])
        lines.append("")
    lines += ["", "### Research coverage and source revisions", ""]
    lines.extend("- " + note for note in ledger.get("resolution_research", {}).get("notes", []))
    return lines
