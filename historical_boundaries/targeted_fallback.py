"""D2-R2 research only: positive upper bounds, never release-time authority.

The public API's scheduled start cannot date historical deck contents. There is
deliberately no live API adapter here. A future bounded evidence provider must
independently establish time and immutable deck provenance before this engine
may inspect it. The current audited provider fails closed before acquisition.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Callable, Protocol

from official_release_observer.parsing import sha, stable
from official_release_observer.timeparse import extract_time
from .catalog import compile_catalog

POLICY = "d2-r2-positive-bound-1"
TARGETS = ("PAF", "SSP", "PRE")
ROOT = Path(__file__).resolve().parents[1] / ".cache/gate111-d2-r2/fallback"


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("an explicit UTC timestamp is required")
    return parsed


def digest(value):
    return sha(stable(value))


def sealed(value):
    return dict(value, sha256=digest(value))


def check(value):
    if value.get("sha256") != digest({k: v for k, v in value.items() if k != "sha256"}):
        raise ValueError("research evidence/checkpoint integrity failure")
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


@dataclass(frozen=True)
class Plan:
    code: str
    official_status: str
    candidates: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    derivation: tuple[str, ...]
    first_party_exhausted: bool

    @property
    def interval(self):
        # No safety margin is needed for the two explicit clock hypotheses.
        return self.candidates[0], self.candidates[-1]


def plans_from_ledger(ledger):
    """Derive ONLY the configured hypotheses; preserve every C1 contradiction.

    PAF's seasonal correction and PRE's contextual date are explicit hypotheses,
    not silently repaired observations. No event clock participates here.
    """
    _, entries, _ = compile_catalog(ledger)
    by_code = {row["event_id"]: row for row in entries}
    plans = []
    for code in TARGETS:
        entry = by_code[code]
        if entry["status"] == "MACHINE_VERIFIED":
            plans.append(Plan(code, entry["status"], (entry["start_utc"],),
                              tuple(entry["exact_evidence_ids"]),
                              ("Coherent first-party availability; fallback prohibited.",), False))
            continue
        statements = [s for s in ledger["temporal_statements"] if s["event_id"] == code]
        release = [s for s in statements if "full_expansion" in s["proposition_kinds"]
                   and s["source_declared_utc"]]
        if len(release) != 1:
            raise ValueError("expected one unresolved full-expansion clock statement")
        source = release[0]
        ids = [source["statement_id"]]
        source_date = source["time"]["date"]
        date = source_date
        notes = ["Explicit 17:00 UTC and local 10:00 are competing source fields; neither wins automatically."]
        if code == "PRE":
            dates = [s for s in statements if s["proposition_kinds"] == ["full_expansion"]
                     and s["time"]["state"] == "DATE_CONFIRMED"]
            if len(dates) != 1 or dates[0]["time"]["date"] != "2025-01-16":
                raise ValueError("PRE requires independent first-party full-expansion date evidence")
            date = dates[0]["time"]["date"]
            ids.append(dates[0]["statement_id"])
            notes.append("2025-01-16 comes from the December 3 letter. The release post's literal 2024 remains conflicting. Both clocks are hypotheses on the independently confirmed date; the Parade clock is excluded.")
        if code == "PAF":
            notes.append("17:00 follows the literal UTC/PDT offset; 18:00 is conditional on intended Pacific civil time (PST). The seasonally invalid PDT declaration is not repaired or accepted by C1.")
        expected = {"PAF": ("2024-01-25", "PDT"), "SSP": ("2024-11-07", "PT"),
                    "PRE": ("2024-01-16", "PT")}[code]
        if (source_date, source["source_declared_zone"]) != expected or source["source_declared_utc"] != "17:00 UTC" or "10:00 AM" not in source["parser_input"]:
            raise ValueError("source clock changed; new first-party adjudication required")
        local = extract_time(datetime.fromisoformat(date).strftime("%B %d, %Y") + " - 10:00 AM PT")
        candidates = (date + "T17:00:00Z", local["calculated_utc"])
        if local["state"] != "EXACT" or candidates[1] != date + "T18:00:00Z":
            raise ValueError("unexpected diagnostic clock conversion")
        plans.append(Plan(code, entry["status"], candidates, tuple(ids), tuple(notes), True))
    return plans


@dataclass(frozen=True)
class Feasibility:
    timestamp_semantics: str
    historical_edits: str
    bounded_query: bool
    evidence_ids: tuple[str, ...]

    def outcome(self):
        if self.historical_edits == "RETROACTIVE_EDITS_UNSAFE":
            return "UNSAFE"
        if (self.timestamp_semantics != "PROVEN_CARD_PRESENT_BY"
                or self.historical_edits != "IMMUTABLE_AT_OBSERVATION"
                or not self.bounded_query or not self.evidence_ids):
            return "INCONCLUSIVE"
        return "USABLE"


def public_api_audit(evidence_ids):
    return Feasibility("ORGANIZER_SCHEDULED_START", "NOT_ESTABLISHED", False, tuple(evidence_ids))


class BoundedEvidenceProvider(Protocol):
    """Trusted adapter contract, NOT satisfied by Limitless's public API.

    enumerate_interval must perform server-side bounded discovery or use an
    already bounded local inventory; no historical pagination is permitted.
    fetch must return details, standings and per-player temporal attestations
    tied to those exact raw decks. Attestations require independent review.
    """
    def enumerate_interval(self, code: str, start: str, end: str) -> list[dict]: ...
    def fetch(self, tournament_id: str) -> dict: ...


def inspect_deck(plan, bundle, row):
    """Validate positive card identity AND a separately proven temporal binding.

    Never use details.date as present_by, nor infer set from an archetype. The
    temporal attestation is an externally audited input, not an API field. Its
    source references and exact deck digest must survive into the observation.
    """
    details = bundle["details"]
    if (details.get("game") != "PTCG" or details.get("platform") != "PTCGL"
            or details.get("isOnline") is not True or details.get("decklists") is not True
            or details.get("format") != "STANDARD"):
        return [], "INVALID_TOURNAMENT"
    player = row.get("player")
    deck = row.get("decklist")
    if not player or not isinstance(deck, dict):
        return [], "NO_DECKLIST"
    proof = bundle.get("temporal_attestations", {}).get(player, {})
    if proof.get("historical_edits") == "RETROACTIVE_EDITS_UNSAFE":
        return [], "UNSAFE"
    if (proof.get("timestamp_semantics") != "PROVEN_CARD_PRESENT_BY"
            or proof.get("historical_edits") != "IMMUTABLE_AT_OBSERVATION"
            or proof.get("tournament_id") != details["id"]
            or proof.get("player") != player or proof.get("deck_sha256") != digest(deck)
            or proof.get("valid_play_on_ptcgl") is not True
            or not proof.get("evidence_ids")):
        return [], "UNPROVEN_TEMPORAL_BINDING"
    when = proof.get("present_by_utc")
    try:
        utc(when)
        utc(details["date"])
    except (AttributeError, TypeError, ValueError, KeyError):
        return [], "INVALID_TIMESTAMP"
    positives = []
    for section in ("pokemon", "trainer", "energy"):
        cards = deck.get(section, [])
        if not isinstance(cards, list):
            return [], "INVALID_DECKLIST"
        for card in cards:
            if not isinstance(card, dict) or card.get("set") != plan.code:
                continue
            count, number, name = card.get("count"), card.get("number"), card.get("name")
            if (type(count) is not int or count <= 0 or not isinstance(number, str)
                    or not number.isdigit() or not isinstance(name, str) or not name.strip()):
                continue
            positives.append(dict(tournament_id=details["id"], tournament_timestamp=details["date"],
                tournament_timestamp_semantics="ORGANIZER_SCHEDULED_START",
                present_by_utc=when, player=player, card_id=plan.code + "-" + number,
                card_name=name, card_set=card["set"], target_expansion=plan.code,
                card_url=f"https://limitlesstcg.com/cards/{plan.code}/{number}",
                deck_sha256=digest(deck), raw_sha256=digest(bundle),
                temporal_evidence_ids=proof["evidence_ids"]))
    return positives, "INSPECTED"


def adjudicate(plan, observations):
    candidates = list(plan.candidates)
    if not candidates or any(utc(a) >= utc(b) for a, b in zip(candidates, candidates[1:])):
        raise ValueError("first-party candidates must be unique and chronological")
    valid = [o for o in observations if o["target_expansion"] == plan.code and o["card_set"] == plan.code]
    earliest = min(valid, key=lambda o: (utc(o["present_by_utc"]), o["tournament_id"], o["player"], o["card_id"])) if valid else None
    eliminated = [c for c in candidates if earliest and utc(c) > utc(earliest["present_by_utc"])]
    remaining = [c for c in candidates if c not in eliminated]
    unique = len(remaining) == 1 and bool(eliminated)
    return dict(status="HUMAN_REVIEW_CANDIDATE" if unique else plan.official_status,
        authority_class="LIMITLESS_CORROBORATED" if unique else None,
        fallback_outcome="DISCRIMINATING" if unique else "INCONCLUSIVE",
        official_candidates=candidates, candidates_eliminated=eliminated,
        candidates_remaining=remaining, proposed_start_utc=remaining[0] if unique else None,
        earliest_positive=earliest, empirical_upper_bound_utc=earliest["present_by_utc"] if earliest else None,
        evidence_ids=list(plan.evidence_ids), reviewed=False,
        reasoning=("Positive target-set use excludes only strictly later first-party candidates; human approval remains required." if unique else
                   "All candidates contradicted; do not invent a timestamp." if not remaining else
                   "No discriminating positive evidence; absence or a late observation cannot select a candidate."))


def progress(plan, state, result):
    total = len(state.get("tournaments", []))
    done = len(state.get("completed_tournaments", []))
    filled = (16 * done // total) if total else 0
    first = result["earliest_positive"]
    earliest = (f"{first['present_by_utc']} tournament={first['tournament_id']} card={first['card_id']}" if first else "none")
    return (f"{plan.code} fallback | {plan.interval[0]} — {plan.interval[1]} (end exclusive)\n"
            f"tournaments inspected: [{'#' * filled}{'-' * (16-filled)}] {done} / {total}\n"
            f"earliest target-set observation: {earliest}\n"
            f"later candidate: {'EXCLUDED' if result['candidates_eliminated'] else 'NOT EXCLUDED'}")


def run_fallback(plan: Plan, audit: Feasibility, provider: BoundedEvidenceProvider | None,
                 *, root=ROOT, emit: Callable[[str], None] | None = None):
    """Checkpoint each fetched bundle and each inspected deck; stop on a bound.

    Raw hashes and inspection policy participate in resume validation. A corrupt
    checkpoint fails closed. Progress callbacks receive text after persistence;
    their failure cannot alter the result. Run a single writer per research root.
    """
    if plan.code not in TARGETS:
        raise ValueError("fallback restricted to PAF / SSP / PRE")
    if plan.official_status == "MACHINE_VERIFIED":
        return dict(status="MACHINE_VERIFIED", authority_class="MACHINE_VERIFIED_SOURCE",
                    fallback_outcome="NOT_REQUIRED", proposed_start_utc=plan.candidates[0], reviewed=False)
    if plan.official_status not in {"OFFICIAL_CONFLICT", "CANONICAL_PENDING"} or not plan.first_party_exhausted or not plan.evidence_ids or len(plan.candidates) != 2:
        raise ValueError("fallback requires unresolved, researched first-party candidates")
    adjudicate(plan, [])  # Validate BEFORE any acquisition.
    if utc(plan.interval[1]) - utc(plan.interval[0]) > timedelta(hours=1):
        raise ValueError("research interval exceeds the configured one-hour ambiguity")
    context = dict(policy=POLICY, plan=asdict(plan), audit=asdict(audit))
    folder = Path(root) / plan.code / digest(context)
    checkpoint = folder / "checkpoint.json"
    state = dict(context_sha256=digest(context), tournaments=None, completed_tournaments=[],
                 inspected_decks={}, raw_hashes={}, positive_observations=[], earliest_positive=None,
                 complete=False, reviewed=False)
    if checkpoint.exists():
        state = check(json.loads(checkpoint.read_text(encoding="utf-8")))
        state.pop("sha256")
        if state["context_sha256"] != digest(context) or state["reviewed"] is not False:
            raise ValueError("checkpoint context/review mismatch")
        for tid, raw_hash in state["raw_hashes"].items():
            raw_path = folder / "raw" / (raw_hash + ".json")
            if digest(json.loads(raw_path.read_text(encoding="utf-8"))) != raw_hash:
                raise ValueError("raw evidence digest mismatch")
    def finish(outcome=None):
        result = adjudicate(plan, state["positive_observations"])
        if outcome:
            result["fallback_outcome"] = outcome
        if outcome in {"UNSAFE", "INCONCLUSIVE_PREFLIGHT"}:
            # Unsafe temporal data can never leave behind a proposed timestamp.
            result = adjudicate(plan, []) | {"fallback_outcome": outcome}
        state["earliest_positive"] = result["earliest_positive"]
        state["result"] = result
        write_json(checkpoint, sealed(state))
        if emit:
            try:
                emit(progress(plan, state, result))
            except Exception:
                pass  # Display only; never an evidence gate.
        return deepcopy(result) | dict(tournaments_inspected=len(state["completed_tournaments"]),
            decklists_inspected=len(state["inspected_decks"]), checkpoint=str(checkpoint))
    feasible = audit.outcome()
    if feasible != "USABLE":
        state["tournaments"] = []
        state["complete"] = True
        return finish("UNSAFE" if feasible == "UNSAFE" else "INCONCLUSIVE_PREFLIGHT")
    if state["complete"]:
        return finish(state["result"]["fallback_outcome"])
    # A process can die after the per-deck checkpoint but before the stop marker.
    # Resume must honor that positive observation without inspecting another deck.
    if (adjudicate(plan, state["positive_observations"])["candidates_eliminated"]
            or any(r["outcome"] == "UNSAFE" for r in state["inspected_decks"].values())):
        unsafe = any(r["outcome"] == "UNSAFE" for r in state["inspected_decks"].values())
        for row in state["inspected_decks"].values():
            if row["tournament_id"] not in state["completed_tournaments"]:
                state["completed_tournaments"].append(row["tournament_id"])
        state["complete"] = True
        return finish("UNSAFE" if unsafe else None)
    if provider is None:
        raise ValueError("a proven bounded evidence provider is required")
    if state["tournaments"] is None:
        tournaments = provider.enumerate_interval(plan.code, *plan.interval)
        if len({t["id"] for t in tournaments}) != len(tournaments):
            raise ValueError("duplicate tournament IDs")
        if any(not utc(plan.interval[0]) <= utc(t["date"]) < utc(plan.interval[1]) for t in tournaments):
            raise ValueError("provider returned evidence outside the narrow interval")
        state["tournaments"] = sorted(tournaments, key=lambda t: (utc(t["date"]), t["id"]))
        finish()
    for tournament in state["tournaments"]:
        tid = tournament["id"]
        if tid in state["completed_tournaments"]:
            continue
        raw_hash = state["raw_hashes"].get(tid)
        if raw_hash:
            bundle = json.loads((folder / "raw" / (raw_hash + ".json")).read_text(encoding="utf-8"))
        else:
            bundle = provider.fetch(tid)
            if bundle["details"]["id"] != tid or bundle["details"]["date"] != tournament["date"]:
                raise ValueError("tournament raw/inventory mismatch")
            raw_hash = digest(bundle)
            write_json(folder / "raw" / (raw_hash + ".json"), bundle)
            state["raw_hashes"][tid] = raw_hash
            finish()
        rows = bundle["standings"]
        if len({r.get("player") for r in rows}) != len(rows):
            raise ValueError("duplicate player identity")
        for row in sorted(rows, key=lambda r: str(r.get("player", ""))):
            key = digest([tid, row.get("player"), raw_hash])
            if key in state["inspected_decks"]:
                continue
            positives, outcome = inspect_deck(plan, bundle, row)
            state["inspected_decks"][key] = dict(tournament_id=tid, player=row.get("player"),
                raw_sha256=raw_hash, deck_sha256=digest(row.get("decklist")), outcome=outcome)
            state["positive_observations"].extend(positives)
            result = finish()
            if outcome == "UNSAFE" or result["candidates_eliminated"]:
                # Count the partially inspected tournament; unneeded decks stop here.
                state["completed_tournaments"].append(tid)
                state["complete"] = True
                return finish("UNSAFE" if outcome == "UNSAFE" else None)
        state["completed_tournaments"].append(tid)
        finish()
    state["complete"] = True
    return finish()
