"""C.2C synthetic end-to-end import, corruption, isolation and recovery tests."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import socket
import subprocess

import numpy as np
import pandas as pd
import pytest
import yaml

import scripts.tcg_live_historical_import as imp
from historical_rebuild.model import digest
from historical_rebuild.production import BASE, ProductionBackend, pack, unpack
from historical_rebuild.runner import STAGES
from historical_rebuild.store import artifact, file_digest, save_state, write_json, writer_lock


def frames():
    axis = ["a", "b"]
    wr = pd.DataFrame([[np.nan, 60.], [40., np.nan]], index=axis, columns=axis)
    nd = pd.DataFrame([[np.nan, 10.], [10., np.nan]], index=axis, columns=axis)
    flat = pd.DataFrame([["a", "b", 6, 4, 2, 12], ["b", "a", 4, 6, 2, 12]],
                        columns=["Deck A", "Deck B", "W", "L", "T", "N"])
    return {k: pack(v) for k, v in dict(wr_matrix=wr, n_dir_matrix=nd, score_flat=flat).items()}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*a, **kw):
        pytest.fail("network/acquisition/publication forbidden")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(ProductionBackend, "acquire", fail)
    monkeypatch.setattr(ProductionBackend, "_network", fail)
    monkeypatch.setattr(ProductionBackend, "execute", fail)
    import requests
    monkeypatch.setattr(requests.Session, "request", fail)


def build_env(tmp_path, code="CRI"):
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "data/reference").mkdir(parents=True)
    for name in ("config/tcg.yaml", "data/reference/ptcg_live_windows.json"):
        (repo / name).write_bytes((BASE / name).read_bytes())
    (repo / "scripts").mkdir()
    # Git-backed synthetic software pin, while executing the real imported module.
    (repo / imp.IMPORTER_RELATIVE).write_bytes(Path(imp.__file__).read_bytes())
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "config/tcg.yaml",
                    "data/reference/ptcg_live_windows.json", imp.IMPORTER_RELATIVE],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Offline", "-c", "user.email=offline@example.invalid",
                    "commit", "-m", "synthetic"], check=True, capture_output=True)
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    cfg = yaml.safe_load((repo / "config/tcg.yaml").read_bytes())
    backend = ProductionBackend(cfg)
    releases = imp.load_release_catalog_snapshot(repo / "data/reference/ptcg_live_windows.json")
    release = next(r for r in releases.releases if r.code == code)
    target = imp.json_value(asdict(imp.ScopePolicy(cfg["source"]["tournament_api"]["scope_policy_id"],
                         "PTCG", "STANDARD", code, release.name, release.release_datetime,
                         release.next_release_datetime, release.catalog_version)))
    old = {**target, "set_name": {"CRI": "Chaos Rising", "PBL": "Pitch Black"}[code],
           "catalog_version": "reviewed-boundaries-v1"}
    root = tmp_path / "history" / code
    fixture = BASE / "tests/fixtures/limitless_api"
    details = {**json.loads((fixture / "details.json").read_bytes()), "id": "t1", "game": "PTCG",
               "platform": "PTCGL", "date": target["start_datetime"]}
    raw = {"details": details, "standings": [{"player": "p1"}], "pairings": [{"phase": 1, "round": 1}]}
    top = pd.DataFrame({"Rank": [1, 2], "Deck": ["a", "b"], "Count": [6, 4], "Share_%": [60., 40.]})
    normal = {"top": pack(top), "identities": pack(pd.DataFrame({"Deck ID": ["a", "b"], "Deck": ["Alpha", "Beta"]}))}
    core = frames()
    (tmp_path / "synthetic-mars").mkdir()
    mars = backend._mars(None, {"CORE": core, "NORMALIZATION": normal}, tmp_path / "synthetic-mars")
    invalid = ["6a282e7a47f3797bf6baff56", "6a56d5c26355385f44dd4e9a", "6a5bf8dc52c24ac2da645f63", "6a9452cbabb9482237503eac"] if code == "PBL" else []
    freeze = {"tournaments": [{"id": "t1", "details": details}], "candidate_count": 1 + len(invalid),
              "eligible_count": 1, "invalid_record_count": len(invalid), "invalid_record_ids": invalid,
              "invalid_record_evidence": [{"id": tid, "selection_record": {"id": tid}} for tid in invalid],
              "classifications": [{"id": "t1", "classification": "eligible", "reason": None}] +
                                 [{"id": tid, "classification": "excluded", "reason": "invalid_record"} for tid in invalid],
              "exclusion_counts": {"invalid_record": len(invalid)},
              "eligibility_evidence_coverage": {"candidate_count": 1 + len(invalid), "covered_count": 1,
                        "percent": round(100 / (1 + len(invalid)), 6), "definition": "synthetic metadata coverage"},
              "classification_provenance": {"scope": old, "eligibility_policy": imp.json_value(asdict(backend.eligibility))}}
    values = {"TOURNAMENT_IDS_FROZEN": freeze, "NORMALIZATION": normal, "CORE": core, "MARS": mars,
              "VALIDATION": {"validated": True, "core_hash": digest(core), "mars_hash": digest(mars)}}
    stages = {}
    for stage in STAGES:
        directory = root / stage.lower() / "synthetic-generation"
        write_json(directory / "result.json", values.get(stage, {"stage": stage}))
        ref = artifact(root, directory)
        stages[stage] = {"status": "VALID", "input_fingerprint": "synthetic", "output_fingerprint": ref["sha256"],
                         "artifact": ref, "started_at": "2026-10-13T00:00:00Z", "completed_at": "2026-10-13T01:00:00Z",
                         "semantics": backend.semantics(stage) if stage in ("NORMALIZATION", "CORE", "MARS", "VALIDATION") else {}}
    raw_dir = root / "raw/synthetic-generation"
    write_json(raw_dir / "result.json", raw)
    ref = artifact(root, raw_dir)
    window = {"window_id": code, "name": old["set_name"], "start_utc": old["start_datetime"], "end_utc": old["end_datetime"]}
    state = {"schema_version": 1, "window_id": code, "window": window, "window_fingerprint": digest(window),
             "status": "DONE", "stages": stages, "tournament_ids": ["t1"], "tournament_manifest_digest": digest(freeze["tournaments"]),
             "raw": {"t1": {"state": "VALID", "artifact": ref, "sha256": ref["sha256"], "input_fingerprint": "synthetic"}},
             "counts": {"valid": 1, "total": 1}, "current_stage": "DONE", "last_checkpoint": "synthetic", "error": None, "provenance": {}}
    state_path = root / "state/current.json"
    save_state(state_path, state)
    request = {"schema_version": 1, "source_kind": "historical_import", "source_root": str(root),
               "source_state_sha256": file_digest(state_path), "historical_scope": old, "target_scope": target,
               "config_sha256": file_digest(repo / "config/tcg.yaml"),
               "catalog_sha256": file_digest(repo / "data/reference/ptcg_live_windows.json"), "software_revision": revision}
    req = tmp_path / "request.json"
    write_json(req, request)
    return repo, root, state, request, req


def run(env):
    # The real imported functions execute; only their __file__ identity points to
    # the versioned synthetic copy, so the pin is exercised without a real source run.
    actual = imp.__file__
    try:
        imp.__file__ = str(env[0] / imp.IMPORTER_RELATIVE)
        return imp.import_historical(repo_root=env[0], request_path=env[4])
    finally:
        imp.__file__ = actual


def reseal(env, stage, value):
    repo, root, state, request, req = env
    directory = root / state["stages"][stage]["artifact"]["path"]
    write_json(directory / "result.json", value)
    ref = artifact(root, directory)
    state["stages"][stage].update(artifact=ref, output_fingerprint=ref["sha256"])
    save_state(root / "state/current.json", state)
    request["source_state_sha256"] = file_digest(root / "state/current.json")
    write_json(req, request)


@pytest.mark.parametrize("code", ["CRI", "PBL"])
def test_actual_import_and_readback(tmp_path, code):
    env = build_env(tmp_path, code)
    before = {p: p.read_bytes() for p in env[1].rglob("*") if p.is_file()}
    result = run(env)
    out = Path(result["candidate"])
    manifest = json.loads((out / "import_manifest.json").read_bytes())
    run_manifest = json.loads((out / "run_manifest.json").read_bytes())
    assert run_manifest["import_manifest_sha256"] == file_digest(out / "import_manifest.json")
    assert manifest["source_kind"] == "historical_import"
    assert manifest["implementation_sha256"] == file_digest(env[0] / imp.IMPORTER_RELATIVE)
    assert manifest["lineage"]["selection_caveats"]["invalid_record_count"] == (4 if code == "PBL" else 0)
    assert manifest["lineage"]["source_urls"]["producer_compatible"] is False
    assert run_manifest["production_receipt"] is run_manifest["published"] is run_manifest["runtime_usable"] is False
    assert result["qa"]["qa"] == "PASS"
    for name, checksum in manifest["outputs"].items():
        assert file_digest(out / name) == checksum
    assert before == {p: p.read_bytes() for p in env[1].rglob("*") if p.is_file()}
    assert not (env[0] / "public").exists() and not (env[0] / ".github").exists()
    assert not list(out.rglob("*receipt*")) and not list(out.rglob("*state*"))


@pytest.mark.parametrize("field", ["set_code", "set_name", "start_datetime", "end_datetime", "catalog_version", "game", "format", "policy_id"])
@pytest.mark.parametrize("which", ["historical_scope", "target_scope"])
def test_identity_mismatch(tmp_path, field, which):
    env = build_env(tmp_path)
    env[3][which][field] = "wrong"
    write_json(env[4], env[3])
    with pytest.raises((ValueError, RuntimeError)):
        run(env)
    assert not (tmp_path / "historical-import-candidates").exists()


@pytest.mark.parametrize("kind", ["state_hash", "config", "catalog", "revision", "missing_manifest", "corrupt_manifest", "raw_hash", "raw_missing", "raw_coverage", "semantics", "label", "url", "invalid_hidden", "stage_manifest"])
def test_corruption_fails_closed(tmp_path, kind):
    env = build_env(tmp_path, "PBL")
    repo, root, state, request, req = env
    if kind in ("state_hash", "revision"):
        request[{"state_hash": "source_state_sha256", "revision": "software_revision"}[kind]] = "wrong"
    elif kind in ("config", "catalog"):
        path = repo / ("config/tcg.yaml" if kind == "config" else "data/reference/ptcg_live_windows.json")
        path.write_bytes(path.read_bytes() + b"\n")
    elif kind in ("missing_manifest", "corrupt_manifest"):
        path = root / state["stages"]["CORE"]["artifact"]["path"] / "result.json"
        if kind == "missing_manifest":
            path.unlink()
        else:
            path.write_bytes(b"{")
    elif kind in ("raw_hash", "raw_missing"):
        path = root / state["raw"]["t1"]["artifact"]["path"] / "result.json"
        if kind == "raw_missing":
            path.unlink()
        else:
            path.write_bytes(b"wrong")
    elif kind in ("label", "url"):
        normal = imp.read_result(root, state["stages"]["NORMALIZATION"]["artifact"])
        frame = unpack(normal["identities" if kind == "label" else "top"])
        if kind == "label":
            frame.loc[0, "Deck ID"] = "missing"
            normal["identities"] = pack(frame)
        else:
            frame["URL"] = "https://invented.invalid"
            normal["top"] = pack(frame)
        reseal(env, "NORMALIZATION", normal)
    elif kind == "invalid_hidden":
        frozen = imp.read_result(root, state["stages"]["TOURNAMENT_IDS_FROZEN"]["artifact"])
        frozen["invalid_record_ids"] = []
        reseal(env, "TOURNAMENT_IDS_FROZEN", frozen)
    else:
        if kind == "raw_coverage":
            state["raw"] = {}
        elif kind == "semantics":
            state["stages"]["CORE"]["semantics"]["config"] = {}
        else:
            state["stages"]["CORE"]["artifact"]["sha256"] = "wrong"
        save_state(root / "state/current.json", state)
        request["source_state_sha256"] = file_digest(root / "state/current.json")
    write_json(req, request)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        run(env)
    assert not (tmp_path / "historical-import-candidates").exists()


def test_lock_and_duplicate_no_overwrite(tmp_path):
    env = build_env(tmp_path)
    scratch = tmp_path / "historical-import-candidates"
    with writer_lock(scratch):
        with pytest.raises(RuntimeError, match="Writer lock"):
            run(env)
    result = run(env)
    before = {p: p.read_bytes() for p in Path(result["candidate"]).rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="already exists"):
        run(env)
    assert before == {p: p.read_bytes() for p in Path(result["candidate"]).rglob("*") if p.is_file()}


def test_interruption_and_explicit_recovery(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    write = imp.write_json
    def crash(path, value):
        if path.name == "run_manifest.json":
            raise OSError("synthetic crash")
        write(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(imp, "write_json", crash)
        with pytest.raises(OSError, match="synthetic crash"):
            run(env)
    scratch = tmp_path / "historical-import-candidates"
    pending, = scratch.glob("*.pending")
    assert not (pending / "run_manifest.json").exists()
    with pytest.raises(ValueError, match="interrupted"):
        run(env)
    pending.rename(scratch / "preserved-interrupted-evidence")
    assert run(env)["qa"]["qa"] == "PASS"


@pytest.mark.parametrize("kind", ["N", "n_dir", "WR", "direction", "pair", "rank", "nan", "diagonal"])
def test_qa_rejects_corrupt_values(tmp_path, kind):
    env = build_env(tmp_path)
    core = imp.read_result(env[1], env[2]["stages"]["CORE"]["artifact"])
    mars = imp.read_result(env[1], env[2]["stages"]["MARS"]["artifact"])
    if kind in ("rank", "nan"):
        frame = unpack(mars["ranking"])
        frame.loc[0, "Deck" if kind == "rank" else "MAS_%"] = "missing" if kind == "rank" else np.nan
        mars["ranking"] = pack(frame)
    else:
        key = {"n_dir": "n_dir_matrix", "WR": "wr_matrix", "diagonal": "wr_matrix"}.get(kind, "score_flat")
        frame = unpack(core[key])
        if kind in ("n_dir", "WR", "diagonal"):
            frame.iloc[0, 0 if kind == "diagonal" else 1] = 99
        else:
            frame.loc[0, {"N": "N", "direction": "W", "pair": "Deck B"}[kind]] = "a" if kind == "pair" else 99
        core[key] = pack(frame)
    with pytest.raises((ValueError, KeyError)):
        imp.qa_frames(core, mars)


def test_source_symlink_rejected(tmp_path):
    env = build_env(tmp_path)
    link = tmp_path / "alias"
    try:
        link.symlink_to(env[1], target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    env[3]["source_root"] = str(link)
    write_json(env[4], env[3])
    with pytest.raises(ValueError, match="symlink"):
        run(env)


@pytest.mark.parametrize("kind", ["checksum", "schema", "missing_field", "raw_incomplete", "details", "config_semantics", "blank_label", "top_label"])
def test_resealed_incompatible_input(tmp_path, kind):
    env = build_env(tmp_path)
    repo, root, state, request, req = env
    if kind in ("checksum", "schema", "missing_field"):
        if kind == "schema":
            state["schema_version"] = 999
        if kind == "missing_field":
            del state["counts"]
        save_state(root / "state/current.json", state)
        if kind == "checksum":
            wrapper = json.loads((root / "state/current.json").read_bytes())
            wrapper["sha256"] = "wrong"
            write_json(root / "state/current.json", wrapper)
    elif kind in ("raw_incomplete", "details"):
        path = root / state["raw"]["t1"]["artifact"]["path"] / "result.json"
        raw = json.loads(path.read_bytes())
        if kind == "raw_incomplete":
            raw["pairings"] = []
        else:
            raw["details"]["name"] = "changed"
        write_json(path, raw)
        ref = artifact(root, path.parent)
        state["raw"]["t1"].update(artifact=ref, sha256=ref["sha256"])
        save_state(root / "state/current.json", state)
    elif kind == "config_semantics":
        path = repo / "config/tcg.yaml"
        cfg = yaml.safe_load(path.read_bytes())
        cfg["mars"]["ALPHA_COMPOSITE"] = 0.71
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        request["config_sha256"] = file_digest(path)
    else:
        normal = imp.read_result(root, state["stages"]["NORMALIZATION"]["artifact"])
        key = "identities" if kind == "blank_label" else "top"
        frame = unpack(normal[key])
        frame.loc[0, "Deck"] = " " if kind == "blank_label" else "missing"
        normal[key] = pack(frame)
        reseal(env, "NORMALIZATION", normal)
    request["source_state_sha256"] = file_digest(root / "state/current.json")
    write_json(req, request)
    with pytest.raises((ValueError, RuntimeError)):
        run(env)
    assert not (tmp_path / "historical-import-candidates").exists()


def test_changed_input_never_seals_candidate(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    original = imp.qa_frames
    calls = []
    def mutate(core, mars):
        result = original(core, mars)
        calls.append(True)
        if len(calls) == 2:
            env[4].write_bytes(env[4].read_bytes() + b"\n")
        return result
    monkeypatch.setattr(imp, "qa_frames", mutate)
    with pytest.raises(ValueError, match="request changed"):
        run(env)
    scratch = tmp_path / "historical-import-candidates"
    assert list(scratch.glob("*.pending"))
    assert not list(scratch.rglob("run_manifest.json"))


def test_unknown_lock_preserved(tmp_path):
    env = build_env(tmp_path)
    scratch = tmp_path / "historical-import-candidates"
    scratch.mkdir()
    lock = scratch / "writer.lock"
    lock.write_bytes(b"unknown owner")
    with pytest.raises(RuntimeError, match="Writer lock"):
        run(env)
    assert lock.read_bytes() == b"unknown owner"


@pytest.mark.parametrize("kind", ["no_opt_in", "publish_flag", "relative_source"])
def test_explicit_request_contract(tmp_path, kind):
    env = build_env(tmp_path)
    if kind == "no_opt_in":
        env[3]["source_kind"] = "live"
    elif kind == "publish_flag":
        env[3]["publish"] = True
    else:
        env[3]["source_root"] = "relative"
    write_json(env[4], env[3])
    with pytest.raises(ValueError):
        run(env)
    assert not (tmp_path / "historical-import-candidates").exists()


@pytest.mark.parametrize("kind", ["importer", "untracked_dependency", "git_failure"])
def test_software_pin_fails_closed_before_candidate(tmp_path, kind):
    env = build_env(tmp_path)
    repo = env[0]
    if kind == "importer":
        path = repo / imp.IMPORTER_RELATIVE
        path.write_bytes(path.read_bytes() + b"\n# unapproved local edit\n")
    elif kind == "untracked_dependency":
        (repo / "historical_rebuild").mkdir()
        (repo / "historical_rebuild/untracked.py").write_text("x = 1\n")
    else:
        (repo / ".git/HEAD").write_text("ref: refs/heads/nonexistent\n")
    with pytest.raises(ValueError, match="importer|dependency|Git verification"):
        run(env)
    assert not (tmp_path / "historical-import-candidates").exists()


def test_importer_mutation_during_import_never_seals(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    original = imp.qa_frames
    calls = []
    def mutate(core, mars):
        result = original(core, mars)
        calls.append(True)
        if len(calls) == 2:
            path = env[0] / imp.IMPORTER_RELATIVE
            path.write_bytes(path.read_bytes() + b"\n# changed concurrently\n")
        return result
    monkeypatch.setattr(imp, "qa_frames", mutate)
    with pytest.raises(ValueError, match="importer"):
        run(env)
    scratch = tmp_path / "historical-import-candidates"
    assert list(scratch.glob("*.pending"))
    assert not list(scratch.rglob("run_manifest.json"))


@pytest.mark.parametrize("location", ["path", "filename"])
def test_artifact_reference_traversal_rejected(tmp_path, location):
    env = build_env(tmp_path)
    ref = env[2]["stages"]["CORE"]["artifact"]
    if location == "path":
        ref["path"] = "../../outside"
    else:
        ref["files"]["../outside.csv"] = "0" * 64
    save_state(env[1] / "state/current.json", env[2])
    env[3]["source_state_sha256"] = file_digest(env[1] / "state/current.json")
    write_json(env[4], env[3])
    with pytest.raises((ValueError, RuntimeError)):
        run(env)
    assert not (tmp_path / "historical-import-candidates").exists()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows junction test")
def test_windows_junction_source_rejected(tmp_path):
    env = build_env(tmp_path)
    alias = tmp_path / "source-junction"
    create = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(env[1])],
                            capture_output=True, text=True)
    if create.returncode != 0:
        pytest.skip("Windows junction creation unavailable: " + create.stderr.strip())
    try:
        env[3]["source_root"] = str(alias)
        write_json(env[4], env[3])
        with pytest.raises(ValueError, match="symlink/junction"):
            run(env)
        assert not (tmp_path / "historical-import-candidates").exists()
    finally:
        alias.rmdir()


def test_pending_csv_mutation_at_pre_rename_gate(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    original = imp.write_json
    def externally_changed(path, value):
        original(path, value)
        if path.name == "run_manifest.json":
            # Race injected after the last manifest write, before the rename.
            csv = path.parent / "score_latest.csv"
            csv.write_bytes(csv.read_bytes() + b"\nexternally changed\n")
    monkeypatch.setattr(imp, "write_json", externally_changed)
    with pytest.raises(ValueError, match="pending output changed before rename"):
        run(env)
    scratch = tmp_path / "historical-import-candidates"
    assert list(scratch.glob("*.pending"))
    assert not list(scratch.glob("historical-import-*")) or all(
        path.name.endswith(".pending") for path in scratch.glob("historical-import-*"))


def test_clean_crlf_checkout_matches_git_blob(tmp_path):
    env = build_env(tmp_path)
    importer = env[0] / imp.IMPORTER_RELATIVE
    data = importer.read_bytes().replace(b"\r\n", b"\n")
    importer.write_bytes(data.replace(b"\n", b"\r\n"))
    subprocess.run(["git", "-C", str(env[0]), "config", "core.autocrlf", "true"],
                   check=True, capture_output=True)
    result = run(env)
    manifest = json.loads((Path(result["candidate"]) / "import_manifest.json").read_bytes())
    assert manifest["implementation_sha256"] == file_digest(importer)
    assert result["qa"]["qa"] == "PASS"
