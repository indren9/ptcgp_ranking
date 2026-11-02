"""Explicit offline import of verified CRI/PBL results; never a daily receipt.

Run with ``python -m scripts.tcg_live_historical_import --request request.json``.
The caller supplies pinned identities, not an acquisition configuration.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import yaml

from acquisition.scope import ScopePolicy
from acquisition.selection import select_tournaments
from historical_rebuild.model import digest, utc
from historical_rebuild.production import ProductionBackend, unpack
from historical_rebuild.runner import STAGES
from historical_rebuild.store import (check_artifact, file_digest, inside,
                                      load_state, read_result, write_json, writer_lock)
from pipelines.limitless_api_acquisition import _selection_record
from sources.limitless.tournament_api.release_catalog import load_release_catalog_snapshot


def require(ok, message):
    if not ok:
        raise ValueError("historical_import: " + message)


def plain_path(path):
    """Reject aliases/reparse points before resolving either read or write paths."""
    require(Path(path).is_absolute(), "absolute local path required")
    require(not str(path).startswith(("\\\\", "//")), "network path forbidden")
    path = Path(path).absolute()
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "symlink/junction path")
    return path.resolve()


def json_value(value):
    return json.loads(json.dumps(value, default=lambda x: x.isoformat().replace("+00:00", "Z")))


def qa_frames(core, mars):
    """Check flat evidence independently of the stored validation verdict."""
    from mars.validate_io import validate_contract
    wr, nd, flat = (unpack(core[k]) for k in ("wr_matrix", "n_dir_matrix", "score_flat"))
    rank = unpack(mars["ranking"])
    verdict = validate_contract(wr, nd)
    require(verdict["ok"] and not verdict["issues"], "Core validator")
    axis = list(wr.index)
    require(len(axis) >= 2 and len(set(axis)) == len(axis), "unique axes")
    require(axis == list(wr.columns) == list(nd.index) == list(nd.columns), "axes")
    require(np.isnan(np.diag(wr)).all() and np.isnan(np.diag(nd)).all(), "diagonals")
    require(not flat.duplicated(["Deck A", "Deck B"]).any(), "duplicate pairs")
    require(set(zip(flat["Deck A"], flat["Deck B"])) ==
            {(a, b) for a in axis for b in axis if a != b}, "directed pair coverage")
    for col in ("W", "L", "T", "N"):
        require(np.isfinite(flat[col]).all() and (flat[col] >= 0).all()
                and (flat[col] == np.floor(flat[col])).all(), "invalid counts " + col)
    require((flat.N == flat.W + flat.L + flat["T"]).all(), "N=W+L+T")
    pairs = flat.set_index(["Deck A", "Deck B"])
    for (a, b), row in pairs.iterrows():
        rev = pairs.loc[(b, a)]
        require(row.W == rev.L and row.L == rev.W and row["T"] == rev["T"], "opposite directions")
        require(nd.loc[a, b] == row.W + row.L, "n_dir=W+L")
        expected = round(100 * row.W / (row.W + row.L), 2) if row.W + row.L else np.nan
        require(np.isclose(wr.loc[a, b], expected, equal_nan=True), "WR from flat")
    require(set(rank.Deck) == set(axis) and not rank.Deck.duplicated().any(), "ranking axes")
    require(rank["Score_%"].is_monotonic_decreasing, "ranking order")
    require(np.isfinite(rank[["Score_%", "MAS_%", "LB_%", "BT_%", "SE_%"]].to_numpy()).all(), "ranking finiteness")
    return {"qa": "PASS", "axis_count": len(axis), "directed_rows": len(flat), "ranking_rows": len(rank)}


def selection_caveats(freeze, ids, scope, eligibility):
    classes = freeze["classifications"]
    all_ids = [x["id"] for x in classes]
    require(len(set(all_ids)) == len(all_ids) == freeze["candidate_count"], "classification identities")
    require([x["id"] for x in classes if x["classification"] == "eligible"] == ids,
            "eligible classification identities")
    invalid = [x["id"] for x in classes if x["reason"] == "invalid_record"]
    require(invalid == freeze["invalid_record_ids"] and len(invalid) == freeze["invalid_record_count"],
            "invalid_record identities")
    require([x["id"] for x in freeze["invalid_record_evidence"]] == invalid, "invalid evidence")
    require(len(ids) == freeze["eligible_count"], "eligible count")
    exclusions = {k: 0 for k in freeze["exclusion_counts"]}
    for row in classes:
        if row["classification"] == "eligible":
            require(row["reason"] is None, "eligible reason")
        else:
            require(row["classification"] == "excluded" and row["reason"] in exclusions, "classification reason")
            exclusions[row["reason"]] += 1
    require(exclusions == freeze["exclusion_counts"], "exclusion counts")
    for row in freeze["invalid_record_evidence"]:
        verdict = select_tournaments([row["selection_record"]], scope=scope, eligibility=eligibility)
        require(verdict.exclusion_counts.get("invalid_record") == 1 and not verdict.tournament_ids
                and not verdict.failures, "invalid evidence classification")
    n = len(all_ids)
    coverage = freeze["eligibility_evidence_coverage"]
    require(n > 0 and coverage["candidate_count"] == n and coverage["covered_count"] == n - len(invalid)
            and coverage["percent"] == round(100 * (n - len(invalid)) / n, 6), "evidence coverage")
    return {k: freeze[k] for k in ("candidate_count", "eligible_count", "invalid_record_count",
            "invalid_record_ids", "invalid_record_evidence", "eligibility_evidence_coverage")}


def verify_source(request, repo):
    """Read-only verification. No runner, acquire, backend execute or client calls."""
    root = plain_path(request["source_root"])
    state_path = plain_path(root / "state/current.json")
    require(file_digest(state_path) == request["source_state_sha256"], "source state hash")
    code = request["historical_scope"]["set_code"]
    require(code in ("CRI", "PBL"), "only CRI/PBL supported")
    state = load_state(state_path, code, STAGES)
    require(state is not None and state["status"] == "DONE", "source not DONE")
    require(state["window_fingerprint"] == digest(state["window"]), "window fingerprint")
    cfg_path = plain_path(repo / "config/tcg.yaml")
    catalog_path = plain_path(repo / "data/reference/ptcg_live_windows.json")
    require(file_digest(cfg_path) == request["config_sha256"], "config hash")
    require(file_digest(catalog_path) == request["catalog_sha256"], "catalog hash")
    cfg = yaml.safe_load(cfg_path.read_bytes())
    require(cfg["source"]["game"] == "PTCG" and cfg["source"]["acquisition"] == "tournament_api", "TCG config")
    require(cfg.get("wr_policy", {}).get("tie_handling") == "exclude", "unsupported WR policy")
    catalog = load_release_catalog_snapshot(catalog_path)
    release = next(x for x in catalog.releases if x.code == code)
    require(not release.is_current and release.next_release_datetime is not None, "closed window required")
    scope = ScopePolicy(cfg["source"]["tournament_api"]["scope_policy_id"], "PTCG", "STANDARD",
                        code, release.name, release.release_datetime, release.next_release_datetime,
                        release.catalog_version)
    target = json_value(asdict(scope))
    require(target == request["target_scope"], "target scope identity")
    inventory = {"state/current.json": request["source_state_sha256"]}
    def verified(ref):
        directory = plain_path(inside(root, ref["path"]))
        for name in ref["files"]:
            plain_path(inside(directory, name))
        require(check_artifact(root, ref), "artifact hash/manifest: " + ref["path"])
        for name, checksum in ref["files"].items():
            inventory[ref["path"] + "/" + name] = checksum
        return read_result(root, ref)
    results = {}
    for stage, entry in state["stages"].items():
        require(entry["status"] == "VALID", "invalid stage " + stage)
        require(entry["output_fingerprint"] == entry["artifact"]["sha256"], "stage fingerprint")
        results[stage] = verified(entry["artifact"])
    freeze = results["TOURNAMENT_IDS_FROZEN"]
    old = freeze["classification_provenance"]["scope"]
    require(old == request["historical_scope"], "historical scope identity")
    for field in ("game", "format", "policy_id", "set_code", "start_datetime", "end_datetime"):
        a, b = old[field], target[field]
        if field.endswith("datetime"):
            a, b = utc(a), utc(b)
        require(a == b, "incompatible scope " + field)
    window = state["window"]
    require(window["window_id"] == code and window["name"] == old["set_name"]
            and utc(window["start_utc"]) == scope.start_datetime
            and utc(window["end_utc"]) == scope.end_datetime, "state window identity")
    backend = ProductionBackend(cfg)
    eligibility = backend.eligibility
    require(json_value(asdict(eligibility)) == freeze["classification_provenance"]["eligibility_policy"], "eligibility config")
    ids = state["tournament_ids"]
    require(ids and len(set(ids)) == len(ids) and set(state["raw"]) == set(ids), "raw ID coverage")
    require([x["id"] for x in freeze["tournaments"]] == ids, "frozen ID order")
    require(state["tournament_manifest_digest"] == digest(freeze["tournaments"]), "frozen manifest digest")
    for item in freeze["tournaments"]:
        tid = item["id"]
        entry = state["raw"][tid]
        require(entry["state"] == "VALID", "invalid raw state")
        raw = verified(entry["artifact"])
        require(entry["sha256"] == entry["artifact"]["sha256"], "raw payload digest")
        backend.validate_raw(tid, raw)
        require(raw["details"] == item["details"], "frozen/raw details mismatch")
        selected = select_tournaments([_selection_record(raw["details"])], scope=scope, eligibility=eligibility)
        require(selected.tournament_ids == (tid,) and not selected.failures, "raw eligibility")
    semantics = {}
    for stage in ("NORMALIZATION", "CORE", "MARS", "VALIDATION"):
        previous = state["stages"][stage]["semantics"]
        current = backend.semantics(stage)
        require(previous == current, "stage semantics " + stage)
        semantics[stage] = {"historical": previous, "current": current, "action": "import_result"}
    core, mars, normal, val = (results[s] for s in ("CORE", "MARS", "NORMALIZATION", "VALIDATION"))
    require(val["validated"] is True and val["core_hash"] == digest(core) and val["mars_hash"] == digest(mars), "validation lineage")
    qa = qa_frames(core, mars)
    identities, top = unpack(normal["identities"]), unpack(normal["top"])
    require(not identities["Deck ID"].duplicated().any() and identities["Deck ID"].notna().all()
            and identities["Deck"].notna().all()
            and identities["Deck"].astype(str).str.strip().ne("").all(), "label identities")
    require(set(unpack(mars["ranking"]).Deck).issubset(set(identities["Deck ID"])), "missing labels")
    require(not top.Deck.duplicated().any() and set(top.Deck).issubset(set(identities["Deck ID"])), "top-meta labels")
    require(set(unpack(mars["ranking"]).Deck).issubset(set(top.Deck)), "top-meta/ranking axes")
    for column in ("Count", "Share_%"):
        require(np.isfinite(top[column]).all() and (top[column] >= 0).all(), "invalid top-meta " + column)
    require((top["Count"] == np.floor(top["Count"])).all() and top["Count"].sum() > 0,
            "top-meta participant counts")
    require((top["Share_%"] <= 100).all() and np.allclose(top["Share_%"],
            100 * top["Count"] / top["Count"].sum(), atol=0.011), "top-meta shares")
    require(list(top["Rank"]) == list(range(1, len(top) + 1)), "top-meta ranks")
    # This version supports the proven historical format only. It does not
    # accept caller-supplied URLs or manufacture a producer-compatible table.
    require(set(top.columns) == {"Rank", "Deck", "Count", "Share_%"}, "unexpected top-meta/URL contract")
    caveats = selection_caveats(freeze, ids, scope, eligibility)
    frames = {"score_latest.csv": core["score_flat"], "filtered_wr_latest.csv": core["wr_matrix"],
              "n_dir_latest.csv": core["n_dir_matrix"], "ranking_historical.csv": mars["ranking"],
              "top_meta_historical.csv": normal["top"], "deck_identity_map.csv": normal["identities"]}
    provenance = {"source_root": str(root), "source_inventory": inventory, "source_state": state,
                  "historical_scope": old, "target_scope": target, "semantics": semantics,
                  "tournament_ids": ids, "tournament_ids_sha256": digest(ids), "selection_caveats": caveats,
                  "effective_config": cfg, "config": {"path": str(cfg_path), "sha256": file_digest(cfg_path)},
                  "catalog": {"path": str(catalog_path), "sha256": file_digest(catalog_path)},
                  "historical_revision": None, "historical_revision_reason": "not recorded by historical state",
                  "qa": qa, "snapshot_freshness_certified": False, "universe_completeness_certified": False,
                  "source_urls": {"status": "absent_in_historical_top_meta", "producer_compatible": False},
                  "blockers": ["source URL provenance required by bundle producer",
                               "freshness and universe completeness unproven",
                               "separate production/publication authorization required"] +
                              (["unclassifiable PBL records require explicit production decision"] if caveats["invalid_record_count"] else [])}
    return provenance, frames, core, mars


# Runtime code used to interpret and verify an historical import. A clean
# tracked tree is necessary, but not sufficient without binding the executed file.
IMPORTER_RELATIVE = "scripts/tcg_live_historical_import.py"
RUNTIME_PATHS = ("historical_rebuild", "acquisition", "core", "mars", "pipelines",
                 "sources", "storage", "config", "data/reference", IMPORTER_RELATIVE)


def git_result(repo, *arguments):
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True)
    require(result.returncode == 0, "Git verification failed: " + arguments[0])
    return result.stdout


def verify_software(repo, revision):
    """Pin executed importer bytes to the requested commit and reject dirty dependencies."""
    importer = plain_path(Path(__file__))
    require(importer == repo / IMPORTER_RELATIVE, "executed importer outside pinned repository")
    raw = importer.read_bytes()
    implementation = hashlib.sha256(raw).hexdigest()
    committed_oid = git_result(repo, "rev-parse", revision + ":" + IMPORTER_RELATIVE).strip()
    # Hash the very same bytes whose SHA-256 is recorded, using Git's checkout
    # filters. A clean CRLF checkout must match the committed LF blob on Windows.
    filtered = subprocess.run(["git", "-C", str(repo), "hash-object", "--stdin",
                               "--path=" + IMPORTER_RELATIVE], input=raw, capture_output=True)
    require(filtered.returncode == 0, "Git verification failed: hash-object")
    require(filtered.stdout.strip() == committed_oid,
            "executed importer differs from pinned Git blob")
    git_result(repo, "diff", "--quiet", revision, "--", *RUNTIME_PATHS)
    require(not git_result(repo, "ls-files", "--others", "--exclude-standard", "--", *RUNTIME_PATHS),
            "untracked runtime dependency")
    return implementation


def import_historical(*, repo_root: Path, request_path: Path):
    repo = plain_path(repo_root)
    request_path = plain_path(request_path)
    request_bytes = request_path.read_bytes()
    request = json.loads(request_bytes)
    require(set(request) == {"schema_version", "source_kind", "source_root", "source_state_sha256",
            "historical_scope", "target_scope", "config_sha256", "catalog_sha256", "software_revision"}, "request schema")
    require(request["schema_version"] == 1 and request["source_kind"] == "historical_import", "explicit opt-in required")
    revision = git_result(repo, "rev-parse", "HEAD").decode("ascii").strip()
    require(revision == request["software_revision"], "software revision")
    implementation = verify_software(repo, revision)
    provenance, frames, core, mars = verify_source(request, repo)
    payload = {"schema_version": 1, "source_kind": "historical_import", "production_receipt": False,
               "publication_authorized": False, "runtime_usable": False, "network_calls": 0,
               "software_revision": revision, "implementation_sha256": implementation,
               "request": {"path": str(request_path), "sha256": file_digest(request_path), "payload": request},
               "lineage": provenance}
    import_id = "historical-import-" + digest(payload)
    # Fixed scratch namespace: no public/state/OneDrive destination parameter.
    scratch = plain_path(repo.parent / "historical-import-candidates")
    source = plain_path(request["source_root"])
    for protected in (repo, source):
        require(not scratch.is_relative_to(protected) and not protected.is_relative_to(scratch), "scratch overlaps inputs")
    require(not request_path.is_relative_to(scratch), "request overlaps output")
    final = scratch / import_id
    pending = scratch / (import_id + ".pending")
    with writer_lock(scratch):
        require(not final.exists() and not pending.exists(), "candidate already exists or interrupted; preserve and inspect before retry")
        pending.mkdir()
        # An interruption leaves a pending tree, never a completed manifest.
        output_hashes = {}
        roundtrip = {}
        for name, packed in frames.items():
            path = pending / name
            path.write_bytes(packed["csv"].encode("utf-8"))
            reread = {**packed, "csv": path.read_text(encoding="utf-8")}
            pd.testing.assert_frame_equal(unpack(reread), unpack(packed))
            roundtrip[name] = reread
            output_hashes[name] = file_digest(path)
        qa_frames({"score_flat": roundtrip["score_latest.csv"], "wr_matrix": roundtrip["filtered_wr_latest.csv"],
                   "n_dir_matrix": roundtrip["n_dir_latest.csv"]}, {"ranking": roundtrip["ranking_historical.csv"]})
        # Recheck all bytes before sealing. Never silently use a changed input.
        for relative, checksum in provenance["source_inventory"].items():
            require(file_digest(source / relative) == checksum, "source changed during import")
        require(request_path.read_bytes() == request_bytes, "request changed during import")
        for item in (provenance["config"], provenance["catalog"]):
            require(file_digest(Path(item["path"])) == item["sha256"], "config/catalog changed during import")
        require(git_result(repo, "rev-parse", "HEAD").decode("ascii").strip() == revision,
                "revision changed during import")
        require(verify_software(repo, revision) == implementation, "implementation changed during import")
        manifest = {**payload, "import_id": import_id, "outputs": output_hashes}
        write_json(pending / "import_manifest.json", manifest)
        run = {"schema_version": 1, "source_kind": "historical_import", "run_id": import_id,
               "status": "OFFLINE_CANDIDATE_VERIFIED", "production_receipt": False, "published": False,
               "runtime_usable": False, "producer_compatible": False, "network_calls": 0,
               "import_manifest_sha256": file_digest(pending / "import_manifest.json"),
               "outputs": output_hashes, "qa": provenance["qa"], "blockers": provenance["blockers"]}
        write_json(pending / "run_manifest.json", run)
        # Last pre-rename gate: a concurrent change to a .pending CSV is not sealed.
        plain_path(pending)
        for name, checksum in output_hashes.items():
            require(file_digest(plain_path(pending / name)) == checksum,
                    "pending output changed before rename: " + name)
        require(file_digest(pending / "import_manifest.json") == run["import_manifest_sha256"],
                "import manifest changed before rename")
        require(git_result(repo, "rev-parse", "HEAD").decode("ascii").strip() == revision,
                "revision changed before rename")
        require(verify_software(repo, revision) == implementation,
                "software changed before rename")
        pending.rename(final)
    return {"candidate": str(final), "import_id": import_id, "qa": provenance["qa"],
            "manifest_sha256": file_digest(final / "import_manifest.json"),
            "run_manifest_sha256": file_digest(final / "run_manifest.json"), "blockers": provenance["blockers"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    result = import_historical(repo_root=Path(__file__).resolve().parents[1], request_path=args.request)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
