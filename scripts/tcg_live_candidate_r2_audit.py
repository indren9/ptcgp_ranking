"""Independent, non-publishing C.5 byte audit. No acquisition or ranking execution.

Only GET/HEAD are exposed by the transport. Existing credentials may still have
IAM write privileges: this software boundary does NOT make those read-only.
Private bytes live only in a fresh 0700 temporary directory, removed on exit.
Limits: 64 MiB/object, 4 GiB total, 50,000 RAW files, 30 minutes; exceeding
any limit is failure, never partial PASS. Only the fixed public report survives.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time


CANDIDATE_SHA = "4684da475293d1ba1351c2673122e76d5c6620c4"
CANDIDATE_RUN = 42523186837
PREFIX = f"candidates/c5_30c/{CANDIDATE_RUN}/1/{CANDIDATE_SHA}"
MANIFEST_SHA = "05a9b99864a2d45135535b96fa3a434d25f613ec2fb0d99fad40940fb9237983"
EXPECTED_HASHES = {
    "receipt": "b57bb56db512e9382aa8556eb232b4bd7dbc735fb75151ce47155c1f5b38266a",
    "config": "cecf223185aec9add84d38934783af416efa40a087f992c7c1ef98280c4f89c2",
    "catalog": "f90c053d279682384d4e9042fee84e2a36d19193c1b24e79ee49e7d31caa6bfa",
    "score_flat": "51137165686407261c35f6f7a13f46dd221eb98ff45de9162cd2bbba5e1e2fa8",
    "wr_matrix": "6095f82c6ca322ced22882f569902cdb525624e0519c47508fea11afedb0e091",
    "n_dir_matrix": "7fd49d14d6563152232933974d5a26775a3d0434ce13482e1781f62b555311e6",
    "mars_ranking": "6d7ee594c3a342322b7680497d597d024c220ea31f3277343bb1d3d17ebe9942",
}
MAX_OBJECT = 64 * 1024**2
MAX_TOTAL = 4 * 1024**3
MAX_RAW_FILES = 50_000
MAX_SECONDS = 30 * 60


class AuditFailure(Exception):
    """Never contains private source values."""


@contextmanager
def silent():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            yield
    finally:
        logging.disable(previous)


def require(ok):
    if not ok:
        raise AuditFailure("audit rejected")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    require(isinstance(value, str) and bool(value) and len(value) <= 1024)
    require(not any(c in value for c in ("\\", ":", "%", "\x00")))
    require(all(ord(c) >= 32 and ord(c) != 127 for c in value))
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value)
    require(all(p not in ("", ".", "..") and p.strip() == p for p in value.split("/")))
    return path


def digest_valid(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def read_json(data):
    # Reject duplicate keys and non-JSON NaN/Infinity rather than normalizing.
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    def invalid(_):
        raise AuditFailure("invalid JSON")
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    require(isinstance(value, dict))
    return value


class ReadOnlyR2:
    """Bounded transport; no backend writer, LIST, fallback or retry."""

    def __init__(self, client, bucket):
        self.__client = client
        self.__bucket = bucket

    def head(self, key):
        safe_path(key)
        response = self.__client.head_object(Bucket=self.__bucket, Key=key)
        require(response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200)
        return response["ContentLength"], response.get("Metadata", {})

    def get_bytes(self, key, limit):
        safe_path(key)
        response = self.__client.get_object(Bucket=self.__bucket, Key=key)
        body = response["Body"]
        try:
            require(response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200)
            size = response["ContentLength"]
            require(type(size) is int and 0 <= size <= limit and "ContentRange" not in response)
            data = bytearray()
            while True:
                chunk = body.read(min(1024**2, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                require(len(data) <= limit)
            require(len(data) == size)
            return bytes(data)
        finally:
            body.close()


class Reader:
    def __init__(self, backend):
        self.backend = backend
        self.total = 0
        self.count = 0
        self.started = time.monotonic()

    def get(self, key, digest=None, size=None):
        safe_path(key)
        require(time.monotonic() - self.started < MAX_SECONDS)
        actual_size, metadata = self.backend.head(key)
        require(type(actual_size) is int and 0 <= actual_size <= MAX_OBJECT)
        require(self.total + actual_size <= MAX_TOTAL)
        require(size is None or (type(size) is int and size == actual_size))
        claimed = metadata.get("sha256")
        require(digest_valid(claimed) and metadata.get("size") == str(actual_size))
        require(digest is None or (digest_valid(digest) and claimed == digest))
        data = self.backend.get_bytes(key, actual_size)
        require(type(data) is bytes and len(data) == actual_size and sha(data) == claimed)
        require(time.monotonic() - self.started < MAX_SECONDS)
        self.total += len(data)
        self.count += 1
        return data


def inventory(rows, *, prefix=None):
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_RAW_FILES)
    paths, keys = set(), set()
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"relative_path", "object_key", "sha256", "size"})
        relative = safe_path(row["relative_path"]).as_posix()
        key = safe_path(row["object_key"]).as_posix()
        require(relative not in paths and type(row["size"]) is int and 0 <= row["size"] <= MAX_OBJECT)
        require(digest_valid(row["sha256"]))
        if prefix is not None:
            require(key == prefix + "/" + relative and key not in keys)
        else:
            # Content-addressed RAW can legitimately share a blob across paths.
            digest = row["sha256"]
            require(key == f"limitless-api/v1/objects/sha256/{digest[:2]}/{digest}")
        paths.add(relative)
        keys.add(key)
    require(not any(parent.as_posix() in paths for p in paths for parent in PurePosixPath(p).parents))
    return paths


def restore(reader, rows, root):
    for row in rows:
        data = reader.get(row["object_key"], row["sha256"], row["size"])
        destination = root / safe_path(row["relative_path"])
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with destination.open("xb") as stream:
            stream.write(data)


def check_contracts(files, report, mars_config=None):
    import numpy as np
    import pandas as pd
    from core.matrices import build_matrices, n_dir_from_WL
    from mars.validate_io import validate_contract

    score = pd.read_csv(files["score_flat"])
    wr = pd.read_csv(files["wr_matrix"], index_col=0)
    nd = pd.read_csv(files["n_dir_matrix"], index_col=0)
    require(wr.index.is_unique and wr.columns.is_unique and wr.index.equals(wr.columns) and len(wr) >= 2)
    qa = validate_contract(wr, nd)
    require(qa["ok"] and not qa["issues"])
    require(not score.duplicated(["Deck A", "Deck B"]).any())
    require((score["Deck A"] != score["Deck B"]).all())
    require(len(score) == len(wr) * (len(wr) - 1))
    require(set(score["Deck A"]) == set(wr.index) == set(score["Deck B"]))
    counts = score[["W", "L", "T", "N"]].to_numpy(float)
    require(np.isfinite(counts).all() and (counts >= 0).all() and (counts == np.floor(counts)).all())
    require((score["N"] == score["W"] + score["L"] + score["T"]).all())
    pairs = score.set_index(["Deck A", "Deck B"])
    for (a, b), row in pairs.iterrows():
        reverse = pairs.loc[(b, a)]
        require(row["W"] == reverse["L"] and row["L"] == reverse["W"] and row["T"] == reverse["T"])
    w, l, _, expected_wr = build_matrices(score, list(wr.index))
    require(np.allclose(wr, expected_wr, equal_nan=True))
    require(np.allclose(nd, n_dir_from_WL(w, l), equal_nan=True))
    ranking = pd.read_csv(files["mars_ranking"], index_col=0)
    require(len(ranking) == report["mars_rows"] == len(wr))
    require(ranking.index.is_unique and ranking["Deck"].is_unique and set(ranking["Deck"]) == set(wr.index))
    require(np.isfinite(ranking.select_dtypes(include="number").to_numpy()).all())
    require(list(ranking.index) == list(range(1, len(wr) + 1)))
    require(ranking["Score_%"].is_monotonic_decreasing)
    require(ranking["Score_%"].between(0, 100).all())
    by_deck = ranking.set_index("Deck").reindex(wr.index)
    require(np.allclose(by_deck["N_eff"], nd.sum(axis=1)))
    require(np.allclose(by_deck["Opp_used"], (nd.fillna(0) > 0).sum(axis=1)))
    require((by_deck["Opp_total"] == len(wr) - 1).all())
    require(np.allclose(by_deck["Coverage_%"], 100 * by_deck["Opp_used"] / (len(wr) - 1)))
    if mars_config is not None:
        require(np.allclose(by_deck["LB_%"], by_deck["MAS_%"] - mars_config["Z_PENALTY"] * by_deck["SE_%"]))
    return len(wr)


def verify(reader, root):
    import yaml
    from acquisition.manifest import validate_manifest_dict
    from scripts.tcg_live_candidate_receipt import (
        REQUIRED_FILES, REQUIRED_OUTPUTS, candidate_identity, candidate_workspace,
        load_verified_candidate, _receipt_path,
    )
    from sources.limitless.tournament_api.object_store import validate_local_run_evidence

    manifest = read_json(reader.get(PREFIX + "/manifest.json", MANIFEST_SHA))
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1)
    require(manifest["complete"] is True and manifest["published"] is False)
    require(manifest["github_repository"] == "indren9/ptcgp_ranking")
    require(type(manifest["github_run_id"]) is int and manifest["github_run_id"] == CANDIDATE_RUN)
    require(type(manifest["github_run_attempt"]) is int and manifest["github_run_attempt"] == 1)
    require(manifest["software_sha"] == CANDIDATE_SHA)
    paths = inventory(manifest["files"], prefix=PREFIX)
    require(len(paths) == 13)
    restore(reader, manifest["files"], root)
    work = root / "work"
    identity = manifest["identity"]
    window = identity["window"]
    require(window["code"] == "30C" and identity["software_revision"] == CANDIDATE_SHA)
    config, catalog = root / "metadata/config.yaml", root / "metadata/catalog.json"
    require(identity == candidate_identity(window=window, config_path=config, catalog_path=catalog, revision=CANDIDATE_SHA))
    receipt_path = _receipt_path(work, identity)
    require(manifest["receipt_relative_path"] == receipt_path.relative_to(root).as_posix())
    receipt = read_json(receipt_path.read_bytes())
    require(receipt["schema_version"] == 2 and receipt["identity"] == identity)
    require(set(receipt["files"]) == set(REQUIRED_FILES))
    safe_path(receipt["source_run"])
    files = {k: work / safe_path(v["path"]) for k, v in receipt["files"].items()}
    effective = candidate_workspace(work_root=work, identity=identity) / "tcg-live-offline.yaml"
    require(manifest["effective_config_relative_path"] == effective.relative_to(root).as_posix())
    expected_paths = {"metadata/config.yaml", "metadata/catalog.json", "metadata/report.json",
                      receipt_path.relative_to(root).as_posix(), effective.relative_to(root).as_posix(),
                      *(p.relative_to(root).as_posix() for p in files.values())}
    require(len(expected_paths) == 13 and paths == expected_paths)
    # All originals remain byte-identical. The loader only reads this new root.
    verified = load_verified_candidate(work_root=work, identity=identity)
    require(verified is not None)
    report = read_json((root / "metadata/report.json").read_bytes())
    require(report["candidate_ready"] is True and report["published"] is False and report["candidate_reused"] is False)
    require(report["offline_network_calls"] == 0 and report["latest_completed_window"] == window)
    require(all(report.get(k) is None for k in ("publication", "onedrive_publication", "bundle_dir")))
    for field in ("canonical_live_run_id", "offline_run_id", "object_manifest_key", "mars_rows", "offline_network_calls"):
        require(report[field] == receipt["report"][field])
    live_id, offline_id = report["canonical_live_run_id"], report["offline_run_id"]
    safe_path(live_id)
    safe_path(offline_id)
    require("/" not in live_id and "/" not in offline_id and live_id != offline_id)
    require(manifest["canonical_live_run_id"] == live_id and manifest["offline_run_id"] == offline_id)
    live = read_json(files["live_manifest"].read_bytes())
    replay = read_json(files["replay_manifest"].read_bytes())
    diagnostics = read_json(files["replay_diagnostics"].read_bytes())
    for acquisition in (live, replay):
        validate_manifest_dict(acquisition)
        require(acquisition["source"] == "Limitless Tournament API")
        require(acquisition["software"]["git_revision"] == CANDIDATE_SHA)
        scope = acquisition["scope"]
        require(scope["game"] == "PTCG" and scope["format"] == "STANDARD" and scope["set_code"] == "30C")
        require(scope["catalog_version"] == window["catalog_version"])
        require(all(datetime.fromisoformat(scope[k].replace("Z", "+00:00")) ==
                    datetime.fromisoformat(window[k].replace("Z", "+00:00")) for k in ("start", "end")))
    require(live["run_id"] == live_id and replay["run_id"] == offline_id)
    for field in ("scope", "selection", "raw", "contracts"):
        require(live[field] == replay[field])
    require(live["normalized"]["hashes"] == replay["normalized"]["hashes"])
    require(live["aggregation"]["comparable_matches"] == replay["aggregation"]["comparable_matches"])
    require(diagnostics["execution_mode"] == "offline" and type(diagnostics["network_calls"]) is int and diagnostics["network_calls"] == 0)
    require(not live["selection"]["failures"])
    ids = live["selection"]["tournament_ids"]
    require(len(ids) == len(set(ids)) == live["selection"]["included_count"] > 0)
    cfg = yaml.safe_load(config.read_bytes())
    effective_cfg = yaml.safe_load(effective.read_bytes())
    api = effective_cfg["source"]["tournament_api"]
    require(cfg["source"]["game"] == "PTCG" and cfg["source"]["tournament_api"]["execution_mode"] == "live")
    require(api["execution_mode"] == "offline" and api["replay_run_id"] == live_id)
    require(api["window"] == {"mode": "code", "code": "30C"} and effective_cfg["mars"] == cfg["mars"])
    catalog_data = read_json(catalog.read_bytes())
    entries = [row for row in catalog_data["releases"] if row["code"] == "30C"]
    require(len(entries) == 1 and catalog_data["catalog_version"] == window["catalog_version"])
    require(entries[0]["release_datetime"] == window["start"] and entries[0]["next_release_datetime"] == window["end"])
    # Bind the preserved run's references, without following any original paths.
    original = manifest["original_work_root"]
    require(isinstance(original, str) and original.startswith("/") and not original.endswith("/"))
    original_repo = manifest["original_repo_root"]
    require(isinstance(original_repo, str) and original_repo.startswith("/") and not original_repo.endswith("/"))
    original_transaction = original + "/" + candidate_workspace(work_root=Path(), identity=identity).as_posix()
    expected_cfg = deepcopy(cfg)
    expected_cfg["source"]["tournament_api"].update(
        execution_mode="offline", replay_run_id=live_id,
        raw_store_root=original_transaction + "/raw-replay",
        cache_root=original_transaction + "/cache-offline", cache_ttl_min=0,
        release_catalog=original_repo + "/" + cfg["source"]["tournament_api"]["release_catalog"],
        window={"mode": "code", "code": "30C"})
    expected_cfg["saving"].update(output_profile="debug", include_time_when_changed=False,
                                  filename_prefix_with_set=False)
    expected_cfg["paths"]["output_dir"] = original_transaction + "/source-output"
    require(effective_cfg == expected_cfg)
    run = read_json(files["run_manifest"].read_bytes())
    require(run["set"]["code"] == "30C")
    for key in REQUIRED_OUTPUTS:
        require(run["outputs"][key] == original + "/" + receipt["files"][key]["path"])
    diag = run["diagnostics"]
    require(run["config_summary"] == {k: effective_cfg.get(k) or {} for k in
                                     ("source", "top_meta", "analysis", "nan_filter", "saving")})
    require(diag["tournament_api_execution_mode"] == "offline" and diag["tournament_api_network_calls"] == 0)
    require(diag["tournament_api_run_id"] == offline_id and diag["tournament_api_replay_run_id"] == live_id)
    require(diag["mars_rows"] == report["mars_rows"])
    # Fixed QA anchors, independently recalculated from downloaded bytes.
    anchors = {"receipt": receipt_path, "config": config, "catalog": catalog,
               **{k: files[k] for k in EXPECTED_HASHES if k in files}}
    for key, path in anchors.items():
        require(sha(path.read_bytes()) == EXPECTED_HASHES[key])
    require(manifest["config_sha256"] == EXPECTED_HASHES["config"] and manifest["catalog_sha256"] == EXPECTED_HASHES["catalog"])
    # Canonical RAW index: no LIST, only exact run marker and content hashes.
    raw_key = f"limitless-api/v1/runs/{live_id}/manifest.json"
    require(manifest["canonical_raw_manifest_key"] == report["object_manifest_key"] == raw_key)
    raw_index_bytes = reader.get(raw_key)
    raw_index = read_json(raw_index_bytes)
    require(raw_index["schema_version"] == "1" and raw_index["run_id"] == live_id)
    require(raw_index["source"] == "Limitless Tournament API")
    raw_paths = inventory(raw_index["files"])
    # Validate all source refs before the existing local validator opens a path.
    refs = live["raw"]["snapshot_refs"]
    require(isinstance(refs, list) and 0 < len(refs) <= MAX_RAW_FILES)
    ref_paths = set()
    expected_raw_paths = {f"runs/{live_id}/{name}" for name in ("manifest.json", "diagnostics.json", "raw_refs.json")}
    for ref in refs:
        relative = safe_path(ref["relative_path"]).as_posix()
        require(relative in raw_paths and relative not in ref_paths and digest_valid(ref["sha256"]))
        ref_paths.add(relative)
        expected_raw_paths.add(relative)
        if "snapshots" in PurePosixPath(relative).parts:
            metadata_path = (PurePosixPath(relative).parent / "metadata.json").as_posix()
            require(metadata_path in raw_paths)
            expected_raw_paths.add(metadata_path)
    require(raw_paths == expected_raw_paths)
    raw_root = root / "raw"
    raw_root.mkdir(mode=0o700)
    restore(reader, raw_index["files"], raw_root)
    source = raw_root / "runs" / live_id / "manifest.json"
    require(source.read_bytes() == files["live_manifest"].read_bytes())
    require(sha(source.read_bytes()) == raw_index["source_manifest_sha256"])
    validate_local_run_evidence(raw_root, live_id)
    # Metadata is checked semantically too, rather than just trusting its hash.
    for ref in refs:
        relative = PurePosixPath(ref["relative_path"])
        if "snapshots" not in relative.parts:
            continue
        metadata = read_json((raw_root / relative.parent / "metadata.json").read_bytes())
        require(metadata["schema_version"] == "1" and metadata["snapshot_id"] == ref["snapshot_id"])
        if ref["payload_type"].startswith("catalog:"):
            require(metadata["sha256"] == ref["sha256"])
        else:
            require(metadata["tournament_id"] == ref["tournament_id"])
            require(metadata["payload_hashes"][ref["payload_type"]] == ref["sha256"])
    refs_path = source.parent / "raw_refs.json"
    raw_refs = read_json(refs_path.read_bytes())
    require(raw_refs["schema_version"] == "1" and raw_refs["run_id"] == live_id)
    require(raw_refs["tournament_ids"] == ids)
    require(sorted(raw_refs["raw_refs"], key=lambda x: x["relative_path"]) ==
            sorted(refs, key=lambda x: x["relative_path"]))
    # RAW discovery diagnostics were not among the 13 candidate files.
    discovery = read_json((source.parent / "diagnostics.json").read_bytes())
    raw_count = discovery["discovery_rows"] + discovery["discovery_duplicate_rows"]
    page_size = cfg["source"]["tournament_api"]["discovery_page_size"]
    oldest = discovery["discovery_oldest"]
    require(raw_count < page_size or raw_count % page_size != 0 or (oldest is not None and
            datetime.fromisoformat(oldest) <= datetime.fromisoformat(window["start"])))
    axis_count = check_contracts(files, report, cfg["mars"])
    return {"candidate_files": 13, "raw_files": len(raw_index["files"]),
            "verified_objects": reader.count, "verified_bytes": reader.total,
            "axis_count": axis_count, "raw_manifest_sha256": sha(raw_index_bytes)}


def audit(backend, *, audit_sha, temp_parent=None):
    """Public boundary: never return exception text, source strings or partial PASS."""
    result = {"schema_version": 1, "status": 2, "published": False,
              "candidate_run_id": CANDIDATE_RUN, "candidate_attempt": 1,
              "candidate_software_sha": CANDIDATE_SHA}
    # Even imported validators printing on error cannot leak through this boundary.
    with silent():
        try:
            require(isinstance(audit_sha, str) and re.fullmatch(r"[0-9a-f]{40}", audit_sha) and audit_sha != CANDIDATE_SHA)
            with tempfile.TemporaryDirectory(prefix="c5-r2-audit-", dir=temp_parent) as directory:
                os.chmod(directory, 0o700)
                evidence = verify(Reader(backend), Path(directory))
            result.update(evidence)
            result.update(status=1, audit_software_sha=audit_sha, manifest_sha256=MANIFEST_SHA)
        except Exception:
            pass
    return result


def main():
    # Suppress SDK/import diagnostics as well as validator exceptions. No log file
    # is retained. Workflow redirects this process too, including interpreter errors.
    result = {"schema_version": 1, "status": 2, "published": False}
    with silent():
        try:
            import boto3
            from botocore.config import Config
            from sources.limitless.tournament_api.object_store import S3ObjectStoreConfig
            config = S3ObjectStoreConfig.from_env()
            require(all(os.environ.get(k) for k in (
                "MARS_RAW_S3_ENDPOINT", "MARS_RAW_S3_BUCKET", "MARS_RAW_S3_REGION",
                "MARS_RAW_S3_ACCESS_KEY_ID", "MARS_RAW_S3_SECRET_ACCESS_KEY")))
            client = boto3.client("s3", endpoint_url=config.endpoint, region_name=config.region,
                aws_access_key_id=config.access_key_id, aws_secret_access_key=config.secret_access_key,
                config=Config(s3={"addressing_style": config.addressing_style},
                              retries={"total_max_attempts": 1}, connect_timeout=15, read_timeout=60))
            result = audit(ReadOnlyR2(client, config.bucket), audit_sha=os.environ["GITHUB_SHA"],
                           temp_parent=os.environ["RUNNER_TEMP"])
        except Exception:
            pass
    # Exclusive evidence file in an exclusively-created workflow directory.
    destination = Path(os.environ["RUNNER_TEMP"]) / "c5-audit-safe" / "audit.json"
    try:
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except Exception:
        return 1
    return 0 if result["status"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
