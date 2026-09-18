"""Gate 1.11-D1: offline execution, invalidation, crash and isolation contracts."""
from collections import Counter
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path

import pytest

from historical_rebuild.model import (CheckpointError, NotReady, RebuildError,
    RetryableError, ValidationError, digest, load_windows)
from historical_rebuild.runner import Progress, Runner, STAGES, status_text
from historical_rebuild.store import (atomic_write, inside, read_result, save_state,
                                      writer_lock)


def boundaries():
    return {"schema_version": 1, "reviewed": True, "boundaries": [
        {"event_id": name, "name": f"Synthetic {name}", "kind": "expansion",
         "start_utc": start, "reasons": ["expansion"],
         "evidence": {"reviewed": True, "exact_time": True,
                      "url": "https://www.pokemon.com/example-offline-fixture"}}
        for name, start in [("A", "2020-01-01T17:00:00Z"), ("B", "2020-02-01T17:00:00Z"),
                            ("C", "2020-03-01T17:00:00Z")]]}


class FixtureBackend:
    def __init__(self):
        self.versions = {s: 1 for s in STAGES}
        self.ids = ["t1", "t2", "t3"]
        self.calls = []
        self.fetches = []
        self.fail_tid = None
        self.fail_stage = None
        self.interrupt = False
        self.bad_raw = False

    def semantics(self, stage):
        return {"version": self.versions[stage]}

    def acquire(self, window, item):
        tid = item["id"]
        self.fetches.append((window.window_id, tid))
        if tid == self.fail_tid:
            if self.interrupt:
                raise KeyboardInterrupt("synthetic terminal interruption")
            raise RetryableError("synthetic network interruption")
        return {"id": tid, "valid": not self.bad_raw}

    def validate_raw(self, tid, payload):
        if payload != {"id": tid, "valid": True}:
            raise ValidationError("synthetic raw validation failed")

    def execute(self, stage, window, inputs, directory):
        self.calls.append((window.window_id, stage))
        if stage == self.fail_stage:
            raise ValidationError("synthetic validation blocker")
        if stage == "DISCOVERY":
            return {"ids": self.ids}
        if stage == "TOURNAMENT_IDS_FROZEN":
            return {"tournaments": [{"id": tid} for tid in inputs["DISCOVERY"]["ids"]]}
        if stage == "NORMALIZATION":
            for item in inputs["RAW_ACQUISITION"]["tournaments"]:
                assert inputs["load_raw"](item["id"])["valid"]
        return {"stage": stage, "fixture": self.versions[stage]}


@pytest.fixture
def env(tmp_path):
    backend = FixtureBackend()
    windows = load_windows(boundaries())
    root = tmp_path / "Rebuild"
    def runner():
        return Runner(root, windows, backend, Progress(StringIO()))
    return backend, windows, root, runner


def run_a(env):
    env[3]().run("window", "A")


def stage_calls(backend):
    return Counter(stage for _, stage in backend.calls)


def test_completed_window_skipped_without_any_output_overwrite(env):
    backend, _, root, make = env
    run_a(env)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    backend.calls.clear()
    backend.fetches.clear()
    assert make().run("window", "A") == []
    assert backend.calls == backend.fetches == []
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_partial_raw_resume_only_missing(env):
    backend, _, _, make = env
    backend.fail_tid = "t3"
    with pytest.raises(RebuildError, match="FAILED_RETRYABLE"):
        run_a(env)
    state = make().status()[0]
    assert state["counts"] == {"valid": 2, "total": 3}
    assert state["status"] != "DONE"
    assert state["raw"]["t3"]["state"] == "FAILED_RETRYABLE"
    backend.fail_tid = None
    backend.fetches.clear()
    run_a(env)
    assert backend.fetches == [("A", "t3")]
    assert make().status()[0]["status"] == "DONE"


def test_single_corrupt_raw_reacquired_individually_old_work_retained(env):
    backend, _, root, make = env
    run_a(env)
    state = make().status()[0]
    ref = state["raw"]["t2"]["artifact"]
    old = inside(root / "A", ref["path"]) / "result.json"
    old.write_text("broken", encoding="utf-8")
    backend.fetches.clear()
    backend.calls.clear()
    assert make().status()[0]["status"] == "STALE"
    run_a(env)
    assert backend.fetches == [("A", "t2")]
    assert "DISCOVERY" not in stage_calls(backend)
    assert "NORMALIZATION" in stage_calls(backend)
    assert old.read_text() == "broken"
    assert make().status()[0]["raw"]["t2"]["history"] == [ref]


def test_changed_id_manifest_reuses_common_ids(env):
    backend, _, _, make = env
    run_a(env)
    backend.ids = ["t2", "t3", "t4"]
    backend.versions["DISCOVERY"] += 1
    backend.fetches.clear()
    run_a(env)
    assert backend.fetches == [("A", "t4")]
    state = make().status()[0]
    assert state["tournament_ids"] == ["t2", "t3", "t4"]
    assert state["raw"]["t1"]["state"] == "VALID"  # retained, no longer counted
    assert state["counts"] == {"valid": 3, "total": 3}


def test_boundary_change_invalidates_discovery_downstream(env):
    backend, windows, root, make = env
    run_a(env)
    changed = boundaries()
    changed["boundaries"][1]["start_utc"] = "2020-02-02T17:00:00Z"
    backend.calls.clear()
    r = Runner(root, load_windows(changed), backend, Progress(StringIO()))
    assert r.status()[0]["status"] == "STALE"
    r.run("window", "A")
    assert set(stage_calls(backend)) == set(STAGES[1:3] + STAGES[4:-1])


@pytest.mark.parametrize("stage,rerun", [
    ("DISCOVERY", {"DISCOVERY", "TOURNAMENT_IDS_FROZEN", "NORMALIZATION", "CORE", "MARS", "VALIDATION", "REPORT"}),
    ("NORMALIZATION", {"NORMALIZATION", "CORE", "MARS", "VALIDATION", "REPORT"}),
    ("CORE", {"CORE", "MARS", "VALIDATION", "REPORT"}),
    ("MARS", {"MARS", "VALIDATION", "REPORT"}),
    ("REPORT", {"REPORT"}),
])
def test_stage_semantic_invalidation_preserves_unrelated_work(env, stage, rerun):
    backend, _, _, make = env
    run_a(env)
    prior = deepcopy(make().status()[0])
    backend.calls.clear()
    backend.fetches.clear()
    backend.versions[stage] += 1
    run_a(env)
    assert set(stage_calls(backend)) == rerun
    assert not backend.fetches
    current = make().status()[0]
    assert current["status"] == "DONE"
    if stage in {"MARS", "REPORT"}:
        assert current["stages"]["CORE"] == prior["stages"]["CORE"]


def test_terminal_interrupt_recovers_persisted_raw(env):
    backend, _, _, make = env
    backend.fail_tid, backend.interrupt = "t3", True
    with pytest.raises(KeyboardInterrupt):
        run_a(env)
    assert make().status()[0]["counts"]["valid"] == 2
    backend.fail_tid = None
    backend.fetches.clear()
    run_a(env)
    assert backend.fetches == [("A", "t3")]
    assert make().status()[0]["status"] == "DONE"


@pytest.mark.parametrize("corruption", ["truncated", "checksum", "schema", "missing_field"])
def test_corrupt_checkpoint_fails_closed(env, corruption):
    _, _, root, make = env
    run_a(env)
    path = root / "A/state/current.json"
    if corruption == "truncated":
        path.write_text('{"state":', encoding="utf-8")
    else:
        data = json.loads(path.read_bytes())
        if corruption == "checksum":
            data["sha256"] = "wrong"
            path.write_text(json.dumps(data), encoding="utf-8")
        else:
            if corruption == "schema":
                data["state"]["schema_version"] = 999
            else:
                del data["state"]["stages"]["CORE"]["artifact"]
            save_state(path, data["state"])
    before = path.read_bytes()
    with pytest.raises(CheckpointError):
        make().status()
    with pytest.raises(CheckpointError):
        run_a(env)
    assert path.read_bytes() == before


@pytest.mark.parametrize("lock_bytes", [b'{"pid":9999999,"host":"old-pc"}', b"partial-lock"])
def test_stale_unknown_lock_fails_closed(env, lock_bytes):
    _, _, root, make = env
    root.mkdir()
    lock = root / "writer.lock"
    lock.write_bytes(lock_bytes)
    with pytest.raises(RebuildError, match="manually archive"):
        run_a(env)
    assert lock.read_bytes() == lock_bytes
    assert len(make().status()) == 3


def test_single_writer_excludes_competing_run(env):
    with writer_lock(env[2]):
        with pytest.raises(RebuildError, match="Writer lock"):
            run_a(env)


def test_atomic_replace_failure_preserves_previous_checkpoint(tmp_path, monkeypatch):
    import historical_rebuild.store as store
    path = tmp_path / "checkpoint.json"
    atomic_write(path, b"old-valid-state")
    def fail(*args):
        raise OSError("simulated power loss before replace")
    monkeypatch.setattr(store.os, "replace", fail)
    with pytest.raises(OSError):
        atomic_write(path, b"new-state")
    assert path.read_bytes() == b"old-valid-state"
    assert list(tmp_path.iterdir()) == [path]


def test_missing_checkpoint_with_existing_work_fails_closed(env):
    backend, _, root, make = env
    run_a(env)
    (root / "A/state/current.json").unlink()
    backend.calls.clear()
    backend.fetches.clear()
    with pytest.raises(CheckpointError, match="Missing checkpoint"):
        make().run("next")
    assert backend.calls == backend.fetches == []


def test_status_no_network_no_recomputation_no_writes(env, monkeypatch):
    import requests
    backend, _, root, make = env
    run_a(env)
    def fail(*args, **kwargs):
        pytest.fail("STATUS attempted execution/network")
    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr(backend, "execute", fail)
    monkeypatch.setattr(backend, "acquire", fail)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert "Overall: 1 / 3 windows DONE" in status_text(make().status())
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_next_one_window_then_resumes_next(env):
    backend, _, _, make = env
    make().run("next")
    assert {wid for wid, _ in backend.calls} == {"A"}
    backend.calls.clear()
    make().run("next")
    assert {wid for wid, _ in backend.calls} == {"B"}
    assert [s["status"] for s in make().status()] == ["DONE", "DONE", "PENDING"]


def test_all_sequential_stops_on_blocker_preserves_done(env):
    backend, _, root, make = env
    run_a(env)
    previous = (root / "A/state/current.json").read_bytes()
    backend.calls.clear()
    backend.fail_stage = "CORE"
    with pytest.raises(RebuildError):
        make().run("all")
    assert {wid for wid, _ in backend.calls} == {"B"}
    assert (root / "A/state/current.json").read_bytes() == previous
    assert not (root / "C").exists()


def test_all_completes_in_order_with_explicit_open_cutoff(env):
    backend, _, root, _ = env
    payload = boundaries()
    payload["observation_cutoff_utc"] = "2020-03-02T17:00:00Z"
    runner = Runner(root, load_windows(payload), backend, Progress(StringIO()))
    runner.run("all")
    assert list(dict.fromkeys(wid for wid, _ in backend.calls)) == ["A", "B", "C"]
    assert all(s["status"] == "DONE" for s in runner.status())


def test_window_only_requested(env):
    backend, _, root, make = env
    make().run("window", "B")
    assert {wid for wid, _ in backend.calls} == {"B"}
    assert not (root / "A").exists()
    with pytest.raises(RebuildError, match="unknown window"):
        make().run("window", "unknown")


def test_validation_failure_never_done(env):
    backend, _, _, make = env
    backend.fail_stage = "VALIDATION"
    with pytest.raises(RebuildError, match="VALIDATION_FAILURE"):
        run_a(env)
    state = make().status()[0]
    assert state["status"] == "BLOCKED"
    assert state["stages"]["DONE"]["status"] != "VALID"
    assert "REPORT" not in stage_calls(backend)


def test_unvalidated_download_never_counts_complete(env):
    backend, _, _, make = env
    backend.bad_raw = True
    with pytest.raises(RebuildError):
        run_a(env)
    state = make().status()[0]
    assert state["counts"]["valid"] == 0
    assert state["raw"]["t1"]["state"] == "FAILED_BLOCKED"


def test_derived_ends_and_open_window_requires_separate_cutoff(env):
    _, windows, _, make = env
    assert windows[0].end_utc == windows[1].start_utc
    assert windows[1].end_utc == windows[2].start_utc
    assert windows[-1].end_utc is None
    with pytest.raises(RebuildError, match="observation_cutoff_utc"):
        make().run("window", "C")
    assert make().status()[-1]["status"] != "DONE"


def test_coincident_rotation_reason_no_zero_length_window():
    payload = boundaries()
    rotation = deepcopy(payload["boundaries"][1])
    rotation.update(event_id="rotation", kind="rotation", reasons=["standard_rotation"])
    payload["boundaries"].insert(2, rotation)
    windows = load_windows(payload)
    assert len(windows) == 3
    assert {e["kind"] for e in windows[1].events} == {"rotation", "expansion"}
    assert all(w.start_utc != w.end_utc for w in windows)
    rotation["start_utc"] = "2020-02-02T17:00:00Z"
    windows = load_windows(payload)
    assert len(windows) == 4
    assert windows[2].window_id == "rotation"


@pytest.mark.parametrize("change", ["missing", "date_only", "unreviewed", "independent_end", "unofficial", "unsorted", "path"])
def test_missing_ambiguous_unreviewed_boundaries_fail_closed(change):
    payload = boundaries()
    event = payload["boundaries"][1]
    if change == "missing":
        event["start_utc"] = None
    elif change == "date_only":
        event["start_utc"] = "2020-02-01"
    elif change == "unreviewed":
        event["evidence"]["reviewed"] = False
    elif change == "independent_end":
        event["end_utc"] = "2020-03-01T17:00:00Z"
    elif change == "unofficial":
        event["evidence"]["url"] = "https://pokemon.com.evil.example/x"
    elif change == "unsorted":
        payload["boundaries"].reverse()
    else:
        event["event_id"] = "../production"
    with pytest.raises(NotReady):
        load_windows(payload)


@pytest.mark.parametrize("interactive", [True, False])
def test_progress_rendering_both_modes(env, interactive):
    class Terminal(StringIO):
        def isatty(self):
            return interactive
    stream = Terminal()
    backend, windows, root, _ = env
    Runner(root, windows, backend, Progress(stream)).run("next")
    output = stream.getvalue()
    for text in ("Overall windows 0/3", "Overall windows 1/3", "RAW 3/3", "RAW_ACQUISITION", "tournament=t2", windows[0].start_utc):
        assert text in output
    assert ("[########################]" in output) == interactive


def test_path_escape_checkpoint_reference_never_reused(env):
    _, _, root, make = env
    run_a(env)
    state = make().status()[0]
    state["stages"]["CORE"]["artifact"]["path"] = "../../public"
    save_state(root / "A/state/current.json", state)
    assert make().status()[0]["status"] != "DONE"


def test_production_fixture_pipeline_uses_real_core_mars_report_offline(tmp_path, monkeypatch):
    """Real adapters, real math and workbook; only the HTTP client is a fixture."""
    import requests
    import yaml
    from historical_rebuild.production import ProductionBackend, BASE
    cfg = yaml.safe_load((BASE / "config/tcg.yaml").read_text(encoding="utf-8"))
    fixture = BASE / "tests/fixtures/limitless_api"
    calls = []
    class Client:
        def list_tournaments(self, **kwargs):
            calls.append("discovery")
            return [{"id": "t1", "game": "PTCG", "date": "2020-01-15T12:00:00Z"}]
        def get_tournament_details(self, tid, **kwargs):
            calls.append("details")
            data = json.loads((fixture / "details.json").read_bytes())
            return {**data, "game": "PTCG", "platform": "PTCGL", "date": "2020-01-15T12:00:00Z"}
        def get_tournament_standings(self, tid, **kwargs):
            calls.append("standings")
            return json.loads((fixture / "standings.json").read_bytes())
        def get_tournament_pairings(self, tid, **kwargs):
            calls.append("pairings")
            # Enough deterministic decisive evidence for real AUTO_K train/test
            # splits; the production algorithm/configuration remain untouched.
            return [{"phase": 1, "round": i, "table": 1, "player1": "p1", "player2": "p2",
                     "winner": "p1" if i % 3 else "p2"} for i in range(1, 21)]
    def fail(*args, **kwargs):
        pytest.fail("real network forbidden")
    monkeypatch.setattr(requests.Session, "request", fail)
    root = tmp_path / "Rebuild"
    windows = load_windows(boundaries())
    backend = ProductionBackend(cfg, client=Client())
    runner = Runner(root, windows, backend, Progress(StringIO()))
    assert runner.status()[0]["status"] == "PENDING"
    assert calls == []
    runner.run("window", "A")
    state = runner.status()[0]
    assert state["status"] == "DONE"
    assert calls == ["discovery", "details", "standings", "pairings"]
    assert list(root.rglob("*.xlsx"))
    core = deepcopy(state["stages"]["CORE"])
    cfg["mars"]["ALPHA_COMPOSITE"] = 0.70
    changed = Runner(root, windows, ProductionBackend(cfg, client=Client()), Progress(StringIO()))
    changed.run("window", "A")
    new = changed.status()[0]
    assert new["stages"]["CORE"] == core
    assert new["stages"]["MARS"]["input_fingerprint"] != state["stages"]["MARS"]["input_fingerprint"]
    assert calls == ["discovery", "details", "standings", "pairings"]
    # Independent clean output with identical inputs produces identical hashes,
    # including the workbook container (timestamps are normalized).
    other_root = tmp_path / "SecondRebuild"
    other = Runner(other_root, windows, ProductionBackend(cfg, client=Client()), Progress(StringIO()))
    other.run("window", "A")
    assert {s: new["stages"][s]["output_fingerprint"] for s in STAGES} == {
        s: other.status()[0]["stages"][s]["output_fingerprint"] for s in STAGES}


def test_production_semantics_ignore_report_only_edits(tmp_path, monkeypatch):
    import historical_rebuild.production as production
    source = (production.BASE / "pipelines/deck_ranking.py").read_text(encoding="utf-8")
    (tmp_path / "pipelines").mkdir()
    path = tmp_path / "pipelines/deck_ranking.py"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(production, "BASE", tmp_path)
    initial = production.function_semantics("pipelines/deck_ranking.py", ["_build_core_matrices"])
    path.write_text(source.replace('base_name="mars_matchup_report"', 'base_name="report_changed"'), encoding="utf-8")
    assert production.function_semantics("pipelines/deck_ranking.py", ["_build_core_matrices"]) == initial
    path.write_text(source.replace('"Candidate pool troppo piccolo "', '"Changed core behavior "'), encoding="utf-8")
    assert production.function_semantics("pipelines/deck_ranking.py", ["_build_core_matrices"]) != initial


def test_raw_acquisition_semantics_are_stale_in_offline_status(env):
    backend, _, _, make = env
    run_a(env)
    backend.versions["RAW_ACQUISITION"] += 1
    state = make().status()[0]
    assert state["counts"]["valid"] == 0
    assert all(r["state"] == "STALE" for r in state["raw"].values())
    backend.fetches.clear()
    run_a(env)
    assert backend.fetches == [("A", tid) for tid in backend.ids]


def test_dataframe_roundtrip_preserves_numeric_looking_canonical_ids():
    import pandas as pd
    from historical_rebuild.production import pack, unpack
    frame = pd.DataFrame([[float("nan"), 1.2345678901234567], [2.0, float("nan")]],
                         index=["001", "002"], columns=["001", "002"])
    pd.testing.assert_frame_equal(frame, unpack(pack(frame)))


def test_real_stage_fingerprints_config_changes_are_scoped():
    import yaml
    from historical_rebuild.production import BASE, ProductionBackend
    cfg = yaml.safe_load((BASE / "config/tcg.yaml").read_text(encoding="utf-8"))
    original = ProductionBackend(cfg)
    before = {s: original.semantics(s) for s in STAGES}
    cfg["mars"]["SEED"] = 123
    changed = ProductionBackend(cfg)
    assert {s for s in STAGES if before[s] != changed.semantics(s)} == {"MARS"}
    cfg["mars"]["SEED"] = 42
    cfg["nan_filter"]["max_nan_ratio"] = 0.2
    changed = ProductionBackend(cfg)
    assert {s for s in STAGES if before[s] != changed.semantics(s)} == {"CORE"}
    cfg["nan_filter"]["max_nan_ratio"] = 0.15
    cfg["source"]["tournament_api"]["eligibility"]["require_decklists"] = False
    changed = ProductionBackend(cfg)
    assert {s for s in STAGES if before[s] != changed.semantics(s)} == {"DISCOVERY", "TOURNAMENT_IDS_FROZEN"}


def test_cli_status_offline_does_not_even_construct_http_client(tmp_path, monkeypatch, capsys):
    import historical_rebuild.__main__ as cli
    from sources.limitless.tournament_api.client import LimitlessTournamentApiClient
    def forbidden(*args, **kwargs):
        pytest.fail("STATUS constructed the HTTP client")
    monkeypatch.setattr(LimitlessTournamentApiClient, "__init__", forbidden)
    monkeypatch.setattr(cli, "OUTPUT_ROOT", tmp_path / "Rebuild")
    source = tmp_path / "boundaries.json"
    source.write_text(json.dumps(boundaries()), encoding="utf-8")
    assert cli.main(["--boundaries", str(source), "status"]) == 0
    assert "0 / 3 windows DONE" in capsys.readouterr().out
    assert not (tmp_path / "Rebuild").exists()


def test_mars_adapter_preserves_production_small_meta_share_units(tmp_path, monkeypatch):
    """Sub-1% decks must be converted to fractions by the production adapter."""
    import pandas as pd
    import yaml
    import mars.pipeline
    from core.matrices import topmeta_post_alias
    from historical_rebuild.production import BASE, ProductionBackend, pack
    top = pd.DataFrame({"Deck": ["001", "002"], "Share_%": [0.70, 0.60], "Count": [7, 6]})
    seen = []
    def capture(wr, n, score, top_meta, cfg):
        seen.append(top_meta)
        return pd.DataFrame({"Deck": ["001"], "Score_%": [50.0]}), {"AUTO_K": {"K_used": 1.0}}, pd.DataFrame(), pd.DataFrame()
    monkeypatch.setattr(mars.pipeline, "run_mars", capture)
    cfg = yaml.safe_load((BASE / "config/tcg.yaml").read_text(encoding="utf-8"))
    backend = ProductionBackend(cfg)
    matrix = pack(pd.DataFrame([[0.0]], index=["001"], columns=["001"]))
    backend.execute("MARS", load_windows(boundaries())[0],
                    {"CORE": {"wr_matrix": matrix, "n_dir_matrix": matrix, "score_flat": matrix},
                     "NORMALIZATION": {"top": pack(top)}}, tmp_path)
    pd.testing.assert_frame_equal(seen[0], topmeta_post_alias(top, {}))
    assert seen[0]["Share_frac"].tolist() == pytest.approx([0.007, 0.006])
