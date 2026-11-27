"""P2 R3 candidate: verified Limitless card-catalog name/code association.

Consumes an archived HTML snapshot from https://limitlesstcg.com/cards and
produces a *new* candidate-only catalog/YAML in isolated staging. Never edits
existing temporary manifests, FROZEN catalogs, or public publication state.
Do not treat a successful match as a release-date source or QA approval.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import yaml

from scripts.tcg_live_provisional_current import (
    ProvisionalGateError, _base_catalog, _guard_workspace,
    _is_under, _read_json, _canonical, _digest, _utc,
)

SET_PAGE = "https://limitlesstcg.com/cards"
CODE_RE = re.compile(r"^[A-Z0-9]{2,8}$")
PATH_RE = re.compile(r"^/cards/(?:en/)?([A-Z0-9]{2,8})/?$")


def _normalized(name: str) -> str:
    """Conservative normalization: no fuzzy/semantic guesses."""
    name = re.sub(r"[\s\-–—]+", " ", name.casefold()).strip()
    return re.sub(r"[^\w\s]", "", name)


def exact_catalog_match(html: str, expected_name: str) -> tuple[str, str]:
    """Match one set row containing an exact normalized name and code link.

    The source is reference-only; parsing drift or ambiguity is not success.
    """
    soup = BeautifulSoup(html, "html.parser")
    matches = set()
    for link in soup.select("a[href]"):
        absolute = urljoin(SET_PAGE + "/", str(link.get("href") or ""))
        url = urlsplit(absolute)
        if url.scheme != "https" or url.netloc != "limitlesstcg.com":
            continue
        m = PATH_RE.fullmatch(url.path)
        if not m or url.query or url.fragment:
            continue
        code = m.group(1)
        text = link.get_text(" ", strip=True)
        # The listing may display "Expansion Name CODE" within the anchor.
        text = re.sub(r"\s+" + re.escape(code) + r"$", "", text, flags=re.I).strip()
        if _normalized(text) == _normalized(expected_name):
            matches.add((code, absolute))
    if len(matches) != 1:
        raise ProvisionalGateError(f"Limitless name/code relation not unique ({len(matches)} matches)")
    return next(iter(matches))


def build_bound_stage(
    *, repo_root: Path, work_root: Path, plan_path: Path,
    cards_html_path: Path, source_url: str = SET_PAGE,
) -> dict:
    """No LIVE run, no canonical mutation; stage a fresh binding candidate."""
    repo_root, work_root = repo_root.resolve(), work_root.resolve()
    _guard_workspace(repo_root, work_root)
    if source_url != SET_PAGE:
        raise ProvisionalGateError("Limitless binding only from the fixed cards catalog")
    from scripts.tcg_live_provisional_current import verify_staged_plan
    plan = verify_staged_plan(repo_root=repo_root, plan_path=plan_path)
    stage_root = Path(str(plan["stage_root"])).resolve()
    if not _is_under(stage_root, work_root):
        raise ProvisionalGateError("provisional stage is not under approved work_root")
    temp = str(plan.get("temporary_code") or "")
    if not re.fullmatch(r"TMP-PTCGL-[0-9A-F]{12}", temp):
        raise ProvisionalGateError("invalid temporary identity")
    source_bytes = cards_html_path.read_bytes()
    html = source_bytes.decode("utf-8")
    code, code_url = exact_catalog_match(html, str(plan["official_name"]))
    if code.startswith("TMP") or not CODE_RE.fullmatch(code):
        raise ProvisionalGateError("unacceptable official code")
    original = repo_root / "data/reference/ptcg_live_windows.json"
    actual_parent_digest = hashlib.sha256(original.read_bytes()).hexdigest()
    if actual_parent_digest != plan["base_catalog_sha256"]:
        raise ProvisionalGateError("parent catalog drift; cannot silently rebase")
    base, _ = _base_catalog(original)
    if any(row["code"].casefold() == code.casefold() for row in base["releases"]):
        raise ProvisionalGateError("official code collides with existing release")
    event_start = _utc(plan["start"])
    if event_start <= _utc(base["releases"][-1]["release_datetime"]):
        raise ProvisionalGateError("official event does not follow current release")
    binding = {
        "schema_version": 1, "status": "LIMITLESS_REFERENCE_MATCHED_CANDIDATE",
        "temporary_code": temp, "verified_code": code,
        "official_name": plan["official_name"],
        "official_start": plan["start"], "official_event_identity": plan["official_identity"],
        "source_url": source_url, "code_url": code_url,
        "source_html_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "parent_catalog_sha256": actual_parent_digest,
        "provisional_evidence_sha256": plan["evidence_sha256"],
        "canonical_mutation_allowed": False,
    }
    binding_sha = _digest(binding)
    target = work_root / temp / ("binding-" + binding_sha[:16])
    if target.exists():
        raise ProvisionalGateError("binding candidate stage exists; inspect prior output")
    target.mkdir(parents=True, exist_ok=False)
    version = "staging-tcgl-bound-" + binding_sha[:20]
    rows = deepcopy(base["releases"])
    for row in rows:
        row["catalog_version"] = version
    rows[-1]["is_current"] = False
    rows[-1]["next_release_datetime"] = plan["start"]
    rows.append({"code": code, "name": plan["official_name"],
                 "release_datetime": plan["start"],
                 "next_release_datetime": None, "is_current": True,
                 "catalog_version": version, "source": base["source"]})
    candidate = {"catalog_version": version, "source": base["source"],
                 "releases": rows, "staging_only": {"binding": binding,
                                                    "never_publish": True}}
    catalog_path = target / "catalog.bound.staging.json"
    catalog_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Use the existing profile without changing any methodology or seed.
    cfg = yaml.safe_load((repo_root / "config/tcg.yaml").read_text(encoding="utf-8"))
    api = cfg["source"]["tournament_api"]
    api["release_catalog"] = str(catalog_path)
    api["window"] = {"mode": "code", "code": code}
    api["execution_mode"] = "live"
    api["replay_run_id"] = ""
    api["raw_store_root"] = str(target / "raw")
    api["cache_root"] = str(target / "cache-api")
    cfg["paths"]["output_dir"] = str(target / "outputs")
    cfg["paths"]["cache_dir"] = str(target / "cache")
    cfg["paths"]["logs_dir"] = str(target / "logs")
    cfg.setdefault("publication", {}).setdefault("onedrive", {})["enabled"] = False
    yaml_path = target / "tcg-bound.staging.yaml"
    yaml_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    receipt = dict(binding, binding_digest=binding_sha, stage_root=str(target),
                   catalog_path=str(catalog_path), yaml_path=str(yaml_path),
                   artifact_status="CANDIDATE_ONLY_NOT_PUBLISHED")
    (target / "identity_binding_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--cards-html", type=Path, required=True)
    args = parser.parse_args()
    result = build_bound_stage(repo_root=args.repo_root, work_root=args.work_root,
                               plan_path=args.plan, cards_html_path=args.cards_html)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
