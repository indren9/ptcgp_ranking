"""Offline R2 audit/replay. No live Tournament API adapter or MARS invocation."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from .catalog import DATA, LEDGER
from .targeted_fallback import (
    ROOT, check, digest, plans_from_ledger, public_api_audit, run_fallback, write_json,
)

ARTIFACT = DATA / "tcg_live_r2_adjudication.json"


def build(research, *, emit=None, root=ROOT):
    check(research)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if research["ledger_sha256"] != digest(ledger) or research["reviewed"] is not False:
        raise ValueError("R1 ledger changed; re-adjudicate the research before replay")
    plans = plans_from_ledger(ledger)
    audit = public_api_audit([s["evidence_id"] for s in research["sources"]
                              if s["url"].startswith("https://docs.limitlesstcg.com/")])
    results = []
    for plan in plans:
        result = run_fallback(plan, audit, None, emit=emit, root=root)
        # Machine-independent artifact; absolute local paths never become provenance.
        result.pop("checkpoint", None)
        results.append(dict(expansion=plan.code, plan=asdict(plan), **result))
    ready = all(r["proposed_start_utc"] for r in results)
    return dict(schema_version=1, gate="1.11-D2-R2", reviewed=False, research=research,
        feasibility_audit=asdict(audit), results=results,
        authority_classes=dict(MACHINE_VERIFIED_SOURCE="Coherent first-party source only",
            HUMAN_ADJUDICATED="Requires explicit human approval; none granted in R2",
            LIMITLESS_CORROBORATED="An empirical bound only; never release-time authority"),
        preview_status="NOT_READY" if not ready else "PROPOSED_ONLY",
        historical_rebuild_executed=False, tournament_api_requests=0,
        verdict="READY_FOR_HUMAN_ADJUDICATION" if ready else "NOT_READY")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate only the fixed unreviewed R2 metadata artifact")
    args = parser.parse_args(argv)
    current = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    expected = build(current["research"], emit=print)
    # JSON normalizes tuples to arrays for comparison with the persisted artifact.
    expected = json.loads(json.dumps(expected))
    if args.write:
        write_json(ARTIFACT, expected)
    elif current != expected:
        raise ValueError("R2 artifact differs from offline replay")
    print(expected["verdict"] + "; reviewed=false; historical rebuild=false; Tournament API requests=0")


if __name__ == "__main__":
    main()
