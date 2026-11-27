"""P2 R3 candidate: provisional TCG Live current-window planning and execution.

No canonical catalog, FROZEN or OneDrive writes. Not wired to any scheduler.
Use --plan to stage a reproducible candidate. --run-live is strictly opt-in;
WP2 authoring does not authorize invoking it (WP3/WP4 gates are separate).
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from official_release_observer.parsing import integrity
from official_release_observer.reconcile import reconcile
from pipelines.deck_ranking import run_deck_ranking


TEMP_PREFIX = "TMP-PTCGL-"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProvisionalGateError(ValueError):
    """Fail-closed: incomplete authority, scope, identity or evidence."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProvisionalGateError("release timestamp must be explicit UTC (Z)")
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProvisionalGateError("release timestamp is invalid") from exc
    if dt.utcoffset() is None:
        raise ProvisionalGateError("release timestamp is timezone-naive")
    return dt.astimezone(UTC)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProvisionalGateError(f"invalid JSON object: {path}")
    return data


def _is_under(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _guard_workspace(repo_root: Path, work_root: Path) -> None:
    repo, work = repo_root.resolve(), work_root.resolve()
    if _is_under(work, repo) or _is_under(repo, work):
        raise ProvisionalGateError("work_root must be isolated outside repo_root")
    if work.exists() and work.is_symlink():
        raise ProvisionalGateError("symlinked work_root not allowed")


def _source_observations(report: Mapping[str, Any], report_path: Path | None = None) -> list[dict[str, Any]]:
    """Accept only the latest immutable observer review and its sealed Store records."""
    if report.get("status") != "NON_AUTHORITATIVE_OBSERVER_REVIEW":
        raise ProvisionalGateError("observer report is not a supported source")
    coverage = report.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        raise ProvisionalGateError("official discovery is incomplete")
    if coverage.get("coverage_errors"):
        raise ProvisionalGateError("official discovery has coverage errors")
    # An embedded observations array was an unreceipted bypass.
    if "observations" in report or report_path is None:
        raise ProvisionalGateError("observer evidence requires an immutable review receipt")
    from official_release_observer.storage import Store
    from official_release_observer.parsing import stable, sha
    store = Store()
    path = Path(report_path).resolve()
    review_digest = sha(stable(report))
    if path.parent != store.root / "reviews" or path.name != review_digest + ".json":
        raise ProvisionalGateError("observer review path or digest mismatch")
    if not path.is_file() or _canonical(_read_json(path)) != _canonical(report):
        raise ProvisionalGateError("observer review bytes or identity mismatch")
    receipts = store.receipts()
    if not receipts or receipts[-1].get("review_digest") != review_digest:
        raise ProvisionalGateError("observer receipt is missing or superseded")
    receipt = receipts[-1]
    for key in ("coverage", "quarantined_identities", "quarantined_observations"):
        if receipt.get(key) != report.get(key):
            raise ProvisionalGateError("observer receipt/review provenance mismatch")
    raw = list(store.latest().values())
    if not raw:
        raise ProvisionalGateError("observer evidence missing")
    if any(not isinstance(row, dict) or not integrity(row) for row in raw):
        raise ProvisionalGateError("observer observation failed integrity")
    return raw

def _matching_candidates(report: Mapping[str, Any], report_path: Path | None = None) -> list[dict[str, Any]]:
    observations = _source_observations(report, report_path)
    computed = reconcile(observations)
    # Reproduce quarantine state applied by the immutable observer Store.
    quarantined = set(report.get("quarantined_identities") or [])
    for candidate in computed:
        if candidate["provisional_identity"] in quarantined:
            candidate.update(state="QUARANTINED", accepted_exact_timestamp=None)
    supplied = report.get("candidates")
    if not isinstance(supplied, list):
        raise ProvisionalGateError("observer candidates missing")
    # A report must not supply a different scope than its sealed observations.
    a = sorted((_canonical(c) for c in computed))
    b = sorted((_canonical(c) for c in supplied))
    if a != b:
        raise ProvisionalGateError("observer candidate reconciliation mismatch")
    return computed


def _base_catalog(catalog_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from sources.limitless.tournament_api.release_catalog import load_release_catalog_snapshot

    # Validate the entire historical chain using the existing canonical reader.
    load_release_catalog_snapshot(catalog_path)
    data = _read_json(catalog_path)
    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ProvisionalGateError("base release catalog has no entries")
    current = [row for row in releases if row.get("is_current") is True]
    if len(current) != 1 or current[0] is not releases[-1]:
        raise ProvisionalGateError("base catalog current release is ambiguous")
    return data, current[0]


def provisional_event(
    report: Mapping[str, Any], catalog_path: Path, *,
    now: datetime | None = None, report_path: Path | None = None,
) -> dict[str, Any]:
    catalog, current = _base_catalog(catalog_path)
    candidates = _matching_candidates(report, report_path)
    base_start = _utc(current["release_datetime"])
    eligible = []
    for c in candidates:
        ts = c.get("accepted_exact_timestamp")
        if c.get("state") != "MACHINE_VERIFIED" or not ts:
            # Unknown, ambiguous or contradictory new events remain unavailable.
            if c.get("expansion") and c.get("state") == "OFFICIAL_CONFLICT":
                raise ProvisionalGateError("official release conflict needs review")
            continue
        at = _utc(ts)
        if at > base_start:
            eligible.append((at, c))
    if len(eligible) != 1:
        raise ProvisionalGateError(f"expected one newly verified boundary; found {len(eligible)}")
    start, candidate = eligible[0]
    if not candidate.get("provisional_identity") or not candidate.get("expansion"):
        raise ProvisionalGateError("release identity or name missing")
    now = (now or datetime.now(UTC)).astimezone(UTC)
    official = str(candidate["provisional_identity"])
    code = TEMP_PREFIX + hashlib.sha256((official + "|" + candidate["accepted_exact_timestamp"]).encode()).hexdigest()[:12].upper()
    name = str(candidate["expansion"]).strip()
    if not name or not re.fullmatch(r"[\w\s\-—–':.!()]+", name, flags=re.UNICODE):
        raise ProvisionalGateError("release name has unsafe or unsupported characters")
    return {
        "schema_version": 1,
        "identity_status": "PROVISIONAL",
        "temporary_code": code,
        "official_name": name,
        "official_identity": official,
        "start": start.isoformat().replace("+00:00", "Z"),
        "now": now.isoformat().replace("+00:00", "Z"),
        "action": "READY_FOR_PROVISIONAL_API_RUN" if start < now else "WAIT_FOR_RELEASE",
        "base_catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "base_catalog_version": catalog["catalog_version"],
        "source_observation_ids": sorted(candidate["observation_ids"]),
        "evidence_sha256": _digest(candidate),
    }


def staging_catalog(base: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    releases = deepcopy(base["releases"])
    if not releases or releases[-1]["is_current"] is not True:
        raise ProvisionalGateError("last canonical window must be open/current")
    if _utc(event["start"]) <= _utc(releases[-1]["release_datetime"]):
        raise ProvisionalGateError("new boundary must follow the current boundary")
    if any(row["code"] == event["temporary_code"] for row in releases):
        raise ProvisionalGateError("temporary code collision")
    version = "staging-tcgl-r3-" + _digest({"parent": event["base_catalog_sha256"], "evidence": event["evidence_sha256"]})[:20]
    source = str(base["source"])
    for row in releases:
        row["catalog_version"] = version
    releases[-1]["next_release_datetime"] = event["start"]
    releases[-1]["is_current"] = False
    releases.append({
        "code": event["temporary_code"],
        "name": event["official_name"],
        "release_datetime": event["start"],
        "next_release_datetime": None,
        "is_current": True,
        "source": source,
        "catalog_version": version,
    })
    return {
        "catalog_version": version,
        "source": source,
        "releases": releases,
        "staging_only": {
            "identity_status": "PROVISIONAL",
            "code_is_official": False,
            "event": dict(event),
            "never_publish": True,
        },
    }


def _expected_stage_config(
    *, repo_root: Path, stage_root: Path, catalog_stage: Path, temporary_code: str,
) -> dict[str, Any]:
    cfg = yaml.safe_load((repo_root / "config/tcg.yaml").read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ProvisionalGateError("canonical TCG YAML is invalid")
    cfg = deepcopy(cfg)
    source = cfg.get("source") or {}
    if source.get("game") != "PTCG" or source.get("acquisition") != "tournament_api":
        raise ProvisionalGateError("wrong canonical acquisition profile")
    api = source.get("tournament_api") or {}
    eligibility = api.get("eligibility") or {}
    if not (eligibility.get("require_online") is True and
            "PTCGL" in eligibility.get("allowed_platforms", []) and
            "STANDARD" in eligibility.get("allowed_formats", [])):
        raise ProvisionalGateError("TCG Live eligibility is not explicit")
    api["release_catalog"] = str(catalog_stage)
    api["window"] = {"mode": "code", "code": temporary_code}
    api["execution_mode"] = "live"
    api["replay_run_id"] = ""
    api["raw_store_root"] = str(stage_root / "raw")
    api["cache_root"] = str(stage_root / "cache-api")
    cfg["paths"]["output_dir"] = str(stage_root / "outputs")
    cfg["paths"]["cache_dir"] = str(stage_root / "cache")
    cfg["paths"]["logs_dir"] = str(stage_root / "logs")
    cfg.setdefault("publication", {}).setdefault("onedrive", {})["enabled"] = False
    return cfg


def stage(
    *, repo_root: Path, work_root: Path, report_path: Path, catalog_path: Path | None = None
) -> dict[str, Any]:
    """Write only under a separate work_root. Does not acquire tournaments."""
    repo_root, work_root = repo_root.resolve(), work_root.resolve()
    _guard_workspace(repo_root, work_root)
    catalog_path = (catalog_path or repo_root / "data/reference/ptcg_live_windows.json").resolve()
    if not _is_under(catalog_path, repo_root):
        raise ProvisionalGateError("base catalog must belong to verified repository")
    base, _ = _base_catalog(catalog_path)
    report = _read_json(report_path)
    event = provisional_event(report, catalog_path, report_path=report_path)
    if event["action"] != "READY_FOR_PROVISIONAL_API_RUN":
        return event  # No staging on an announced-but-not-yet-effective boundary.
    cfg_path = repo_root / "config/tcg.yaml"
    config_digest = hashlib.sha256(cfg_path.read_bytes()).hexdigest()
    # Immutable stage identity; changes in evidence create a separate stage.
    stage_root = work_root / event["temporary_code"] / event["evidence_sha256"][:12]
    if stage_root.exists():
        saved_path = stage_root / "provisional_plan.json"
        if not saved_path.is_file():
            raise ProvisionalGateError("incomplete earlier stage: inspection required")
        saved = _read_json(saved_path)
        keys = ("temporary_code", "official_identity", "start", "evidence_sha256", "base_catalog_sha256")
        if any(saved.get(k) != event.get(k) for k in keys):
            raise ProvisionalGateError("staged identity or parent changed: manual review")
        if (hashlib.sha256(Path(saved["stage_yaml"]).read_bytes()).hexdigest() != saved["yaml_sha256"]
            or hashlib.sha256(Path(saved["stage_catalog"]).read_bytes()).hexdigest() != saved["catalog_sha256"]):
            raise ProvisionalGateError("existing staging artifacts changed")
        verify_staged_plan(repo_root=repo_root, plan_path=saved_path)
        return saved  # Return only a semantically verified immutable plan.
    stage_root.mkdir(parents=True, exist_ok=False)
    catalog_stage = stage_root / "catalog.staging.json"
    catalog_stage.write_text(json.dumps(staging_catalog(base, event), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    cfg = _expected_stage_config(
        repo_root=repo_root, stage_root=stage_root,
        catalog_stage=catalog_stage, temporary_code=event["temporary_code"],
    )
    yaml_stage = stage_root / "tcg-provisional.staging.yaml"
    yaml_stage.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    from official_release_observer.storage import Store
    from official_release_observer.parsing import stable, sha
    receipt = Store().receipts()[-1]
    plan = dict(event, stage_root=str(stage_root), stage_catalog=str(catalog_stage),
                stage_yaml=str(yaml_stage), yaml_sha256=hashlib.sha256(yaml_stage.read_bytes()).hexdigest(),
                catalog_sha256=hashlib.sha256(catalog_stage.read_bytes()).hexdigest(),
                observer_report_path=str(report_path.resolve()),
                observer_receipt_sha256=sha(stable(receipt)),
                base_config_sha256=config_digest,
                publication_allowed=False)
    plan_file = stage_root / "provisional_plan.json"
    plan_file.write_text(json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    verify_staged_plan(repo_root=repo_root, plan_path=plan_file)
    return plan


def verify_staged_plan(*, repo_root: Path, plan_path: Path) -> dict[str, Any]:
    """Rederive provenance, semantics and effective scope before any stage use."""
    repo_root = Path(repo_root).resolve()
    plan_path = Path(plan_path).resolve()
    if plan_path.name != "provisional_plan.json":
        raise ProvisionalGateError("unexpected provisional plan filename")
    plan = _read_json(plan_path)
    if (plan.get("action") != "READY_FOR_PROVISIONAL_API_RUN" or
            plan.get("publication_allowed") is not False or
            plan.get("schema_version") != 1):
        raise ProvisionalGateError("plan is not an authorized nonpublishing stage")
    stage_root = plan_path.parent
    _guard_workspace(repo_root, stage_root)
    if str(stage_root) != str(Path(str(plan.get("stage_root") or "")).resolve()):
        raise ProvisionalGateError("plan workspace identity mismatch")
    temp = str(plan.get("temporary_code") or "")
    evidence = str(plan.get("evidence_sha256") or "")
    if not re.fullmatch(r"TMP-PTCGL-[0-9A-F]{12}", temp) or not _SHA256.fullmatch(evidence):
        raise ProvisionalGateError("invalid stage identity")
    if stage_root != stage_root.parents[1] / temp / evidence[:12]:
        raise ProvisionalGateError("unexpected stage directory layout")
    original = repo_root / "data/reference/ptcg_live_windows.json"
    cfg_source = repo_root / "config/tcg.yaml"
    if (hashlib.sha256(original.read_bytes()).hexdigest() != plan.get("base_catalog_sha256")
            or hashlib.sha256(cfg_source.read_bytes()).hexdigest() != plan.get("base_config_sha256")):
        raise ProvisionalGateError("canonical catalog or effective YAML changed since planning")
    from official_release_observer.storage import Store
    from official_release_observer.parsing import stable, sha
    receipts = Store().receipts()
    if not receipts or sha(stable(receipts[-1])) != plan.get("observer_receipt_sha256"):
        raise ProvisionalGateError("observer receipt changed or is stale")
    report_path = Path(str(plan.get("observer_report_path") or "")).resolve()
    report = _read_json(report_path)
    # This call validates the exact immutable review, newest receipt and observations.
    event = provisional_event(report, original, report_path=report_path)
    if event["action"] != "READY_FOR_PROVISIONAL_API_RUN":
        raise ProvisionalGateError("provisional release not effective at runtime")
    fields = ("schema_version", "identity_status", "temporary_code", "official_name",
              "official_identity", "start", "base_catalog_sha256",
              "base_catalog_version", "source_observation_ids", "evidence_sha256")
    if any(plan.get(k) != event.get(k) for k in fields):
        raise ProvisionalGateError("provisional event or identity changed")
    cat_path = (stage_root / "catalog.staging.json")
    cfg_path = (stage_root / "tcg-provisional.staging.yaml")
    if (str(cat_path) != str(plan.get("stage_catalog")) or
            str(cfg_path) != str(plan.get("stage_yaml"))):
        raise ProvisionalGateError("staged artifact paths changed")
    if (hashlib.sha256(cat_path.read_bytes()).hexdigest() != plan.get("catalog_sha256")
            or hashlib.sha256(cfg_path.read_bytes()).hexdigest() != plan.get("yaml_sha256")):
        raise ProvisionalGateError("staging artifact hash mismatch")
    # The staging catalog embeds the original event, not later plan metadata.
    event_fields = ("schema_version", "identity_status", "temporary_code",
                    "official_name", "official_identity", "start", "now", "action",
                    "base_catalog_sha256", "base_catalog_version",
                    "source_observation_ids", "evidence_sha256")
    staged_event = {key: plan[key] for key in event_fields}
    planned_at = _utc(staged_event["now"])
    if planned_at < _utc(plan["start"]) or planned_at > datetime.now(UTC):
        raise ProvisionalGateError("provisional plan creation time is invalid")
    base, _ = _base_catalog(original)
    if _canonical(_read_json(cat_path)) != _canonical(staging_catalog(base, staged_event)):
        raise ProvisionalGateError("staged catalog semantics differ from verified event")
    expected_cfg = _expected_stage_config(
        repo_root=repo_root, stage_root=stage_root, catalog_stage=cat_path,
        temporary_code=temp,
    )
    if yaml.safe_load(cfg_path.read_text(encoding="utf-8")) != expected_cfg:
        raise ProvisionalGateError("staged YAML semantics differ from canonical profile")
    return plan


def run_staged_current(*, repo_root: Path, plan_path: Path) -> dict[str, Any]:
    """Intentional LIVE operation. Not called in WP2; WP3 run approval required."""
    plan = verify_staged_plan(repo_root=repo_root, plan_path=plan_path)
    stage_root = Path(plan["stage_root"]).resolve()
    _guard_workspace(repo_root, stage_root)
    cfg_path = Path(plan["stage_yaml"])
    if not _is_under(cfg_path.resolve(), stage_root):
        raise ProvisionalGateError("staging YAML outside isolated root")
    if hashlib.sha256(cfg_path.read_bytes()).hexdigest() != plan["yaml_sha256"]:
        raise ProvisionalGateError("staging YAML digest changed")
    cat_path = Path(plan["stage_catalog"])
    if hashlib.sha256(cat_path.read_bytes()).hexdigest() != plan["catalog_sha256"]:
        raise ProvisionalGateError("staging release catalog digest changed")
    from uuid import uuid4

    # Do not overwrite yesterday's CURRENT ranking, raw, or manifest. The
    # immutable plan points to a fresh dated run on every invocation.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_root = stage_root / "runs" / (stamp + "-" + uuid4().hex[:8])
    run_root.mkdir(parents=True, exist_ok=False)
    run_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    api = run_cfg["source"]["tournament_api"]
    api["raw_store_root"] = str(run_root / "raw")
    api["cache_root"] = str(run_root / "cache-api")
    run_cfg["paths"]["output_dir"] = str(run_root / "outputs")
    run_cfg["paths"]["cache_dir"] = str(run_root / "cache")
    run_cfg["paths"]["logs_dir"] = str(run_root / "logs")
    runtime_yaml = run_root / "tcg-current-runtime.yaml"
    runtime_yaml.write_text(yaml.safe_dump(run_cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    result = run_deck_ranking(
        base_dir=repo_root, config_path=runtime_yaml, output_dir=run_root / "outputs",
        run_scrape=True, run_core=True, run_mars=True,
        run_heatmap=False, run_report=False, configure_logs=True,
        show_progress=False,
    )
    api_run = str(result.diagnostics.get("tournament_api_run_id") or "")
    rows = int(result.diagnostics.get("mars_rows") or 0)
    if not api_run or rows <= 0:
        # Keep failed/provisional artifacts under the isolated stage for review.
        raise ProvisionalGateError("NO_ELIGIBLE_DATA_OR_INSUFFICIENT_RANKING; nothing promotable")
    receipt = {"state": "PROVISIONAL_RESULT_ONLY", "temporary_code": plan["temporary_code"],
               "api_run_id": api_run, "mars_rows": rows,
               "output_root": str(run_root / "outputs"), "canonical_publication": False}
    (run_root / "provisional_result.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run-live", type=Path, metavar="STAGED_PLAN_JSON")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--observer-report", type=Path)
    parser.add_argument("--catalog", type=Path)
    args = parser.parse_args()
    if args.plan:
        if args.work_root is None or args.observer_report is None:
            parser.error("--plan requires --work-root and --observer-report")
        report = stage(repo_root=args.repo_root, work_root=args.work_root,
                       report_path=args.observer_report, catalog_path=args.catalog)
    else:
        report = run_staged_current(repo_root=args.repo_root, plan_path=args.run_live)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
