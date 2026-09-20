"""Frozen R4 promotion, offline and separate from research and production.

The pinned approval is an explicit human attestation, not approval inferred
from a successful schema check. New inputs require a new human review.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json

from historical_rebuild.model import load_windows, utc
from historical_rebuild.store import atomic_write
from official_release_observer import (
    AUTHORITY_POLICY_VERSION, PARSER_VERSION, SOURCE_ADAPTER_VERSION,
)
from official_release_observer.parsing import sha, stable
from .catalog import BASE, CANDIDATE, HUMAN_ADJUDICATIONS, LEDGER, REQUIRED_EXPANSIONS, serialize
from .human_adjudication import APPROVED_STARTS, POLICY_VERSION

APPROVED = BASE / "data/approved/releases"
APPROVAL = APPROVED / "tcg_live_historical_boundary_approval.json"
REVIEWED = APPROVED / "tcg_live_historical_boundaries_reviewed.json"
GENERATOR_VERSION = "historical-boundary-promotion-1"
# Trust anchor for the full human approval, including all three source hashes.
# Never recompute this constant from inputs or accept a caller-supplied digest.
APPROVAL_SHA256 = "7553fe1b1f09990c9294d9eeee6b5d738fadc5f4c5ad81a9f4278425e7528268"


def bound_json(raw: bytes, expected: str, label: str):
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"{label} SHA-256 differs from frozen final approval")
    return json.loads(raw)


def build_reviewed(candidate_raw: bytes, adjudications_raw: bytes, ledger_raw: bytes,
                   approval_raw: bytes) -> dict:
    """Promote only the exact approved R3 bytes; do not reinterpret timestamps."""
    review = bound_json(approval_raw, APPROVAL_SHA256, "final catalog approval")
    candidate = bound_json(candidate_raw, review["source_candidate_sha256"], "source candidate")
    register = bound_json(adjudications_raw, review["human_adjudications_sha256"], "human adjudications")
    ledger = bound_json(ledger_raw, review["evidence_ledger_sha256"], "evidence ledger")
    if (candidate.get("schema_version") != 1 or candidate.get("reviewed") is not False
            or candidate.get("window_unit") != "expansion"
            or candidate.get("catalog_status") != "COMPLETE_UNREVIEWED"
            or "observation_cutoff_utc" in candidate):
        raise ValueError("source candidate must remain complete, unreviewed and expansion-only")
    boundaries = candidate["boundaries"]
    if ([b["event_id"] for b in boundaries] != [code for code, _ in REQUIRED_EXPANSIONS]
            or len(boundaries) != 23
            or Counter(b["evidence"]["status"] for b in boundaries)
            != {"MACHINE_VERIFIED": 20, "HUMAN_ADJUDICATED": 3}):
        raise ValueError("approved expansion identities/status counts differ")
    if any(not b["start_utc"] or b["kind"] != "expansion" or b["reasons"] != ["expansion"]
           or b["evidence"].get("reviewed") is not False
           or b["evidence"].get("exact_time") is not True for b in boundaries):
        raise ValueError("unresolved, reviewed or non-expansion source boundary")
    if (register.get("schema_version") != 1 or register.get("catalog_reviewed") is not False
            or register.get("policy_version") != POLICY_VERSION or ledger.get("reviewed") is not False):
        raise ValueError("research ledger and human register must remain unreviewed")
    decisions = register["decisions"]
    if (len(decisions) != 3 or {d["event_id"]: d["approved_canonical_utc"] for d in decisions}
            != APPROVED_STARTS):
        raise ValueError("human decisions differ from the frozen R3 starts")
    by_code = {b["event_id"]: b for b in boundaries}
    for decision in decisions:
        boundary = by_code[decision["event_id"]]
        evidence = boundary["evidence"]
        if (decision.get("adjudication_reviewed") is not True
                or decision.get("historical_pattern_used") is not False
                or decision.get("limitless_used_for_time") is not False
                or decision["research_ledger_sha256"] != sha(stable(ledger))
                or boundary["start_utc"] != decision["approved_canonical_utc"]
                or evidence.get("adjudication_reviewed") is not True
                or evidence.get("adjudication_id") != decision["adjudication_id"]
                or evidence["status"] != "HUMAN_ADJUDICATED"):
            raise ValueError("missing individual approval or inconsistent human provenance")
    # Copy the already adjudicated sequence, preserving every evidence field.
    # Do not invoke compile_catalog/compatibility_preview or any time methodology.
    result = deepcopy(candidate)
    result.update(reviewed=True, catalog_status="COMPLETE_REVIEWED", review=review,
                  generation=dict(software="historical_boundaries.reviewed", version=GENERATOR_VERSION,
                                  authority_policy_version=AUTHORITY_POLICY_VERSION,
                                  parser_version=PARSER_VERSION, source_adapter_version=SOURCE_ADAPTER_VERSION))
    for boundary in result["boundaries"]:
        boundary["evidence"]["reviewed"] = True
    return result


def reviewed_bytes() -> bytes:
    return serialize(build_reviewed(CANDIDATE.read_bytes(), HUMAN_ADJUDICATIONS.read_bytes(),
                                    LEDGER.read_bytes(), APPROVAL.read_bytes())).encode("utf-8")


def validate_persisted(expected: bytes):
    """Pass the persisted document directly to the actual D1 boundary loader."""
    raw = REVIEWED.read_bytes()
    if raw != expected:
        raise ValueError("persisted reviewed artifact differs from frozen deterministic promotion")
    payload = json.loads(raw)
    windows = load_windows(payload)
    if (len(windows) != 22 or len({b["start_utc"] for b in payload["boundaries"]}) != 22
            or any(w.end_utc != following.start_utc for w, following in zip(windows, windows[1:]))
            or any(w.end_utc is not None and utc(w.end_utc) <= utc(w.start_utc) for w in windows)):
        raise ValueError("D1 windows must be 22 contiguous, positive-duration release windows")
    joint = [w for w in windows if {e["event_id"] for e in w.events} == {"BLK", "WHT"}]
    if len(joint) != 1 or joint[0].start_utc != "2025-07-17T17:00:00Z":
        raise ValueError("D1 must group BLK/WHT into one logical release window")
    if (windows[-1].window_id != "30C" or windows[-1].start_utc != "2026-09-15T17:00:00Z"
            or windows[-1].end_utc is not None or "observation_cutoff_utc" in payload
            or any(w.observation_cutoff_utc is not None for w in windows)):
        raise ValueError("final 30C window must remain OPEN without an observation cutoff")
    return windows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="create the fixed reviewed artifact if absent; never replace different bytes")
    args = parser.parse_args(argv)
    expected = reviewed_bytes()
    if REVIEWED.resolve() != REVIEWED.absolute():
        raise ValueError("reviewed output cannot traverse symlinks/junctions")
    if REVIEWED.exists():
        if REVIEWED.read_bytes() != expected:
            raise ValueError("reviewed artifact is immutable; refusing to replace different bytes")
    elif args.write:
        atomic_write(REVIEWED, expected)
    windows = validate_persisted(expected)
    print(f"D1_COMPATIBILITY=PASS; boundaries=23; logical_windows={len(windows)}; "
          "reviewed=true; candidate reviewed=false; final=OPEN; network_calls=0; rebuild=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
