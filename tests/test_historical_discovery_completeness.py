"""D3-R1 discovery regressions: synthetic inputs only, no RAW/Core/MARS."""
from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import socket

import pytest
import requests
import yaml

from historical_rebuild.model import RebuildError, load_windows, stamp, utc
from historical_rebuild.production import BASE, ProductionBackend
from historical_rebuild.runner import Progress, Runner, STAGES
from historical_rebuild.store import read_result
from pipelines import limitless_api_acquisition as acquisition
from sources.limitless.tournament_api.client import LimitlessTournamentApiClient

PRODUCTION_CONFIG = BASE / "config/tcg.yaml"
HISTORICAL_CONFIG = BASE / "config/tcg_historical_rebuild.yaml"
BOUNDARIES = BASE / "data/approved/releases/tcg_live_historical_boundaries_reviewed.json"


@pytest.fixture(autouse=True)
def offline_and_no_downstream(monkeypatch):
    before = PRODUCTION_CONFIG.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("D3 tests cannot access the network or execute RAW/Core/MARS/publication")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    for method in ("acquire", "_normalize", "_core", "_mars", "_validate", "_report"):
        monkeypatch.setattr(ProductionBackend, method, forbidden)
    monkeypatch.setattr(acquisition, "_normalize_selected", forbidden)
    monkeypatch.setattr(acquisition, "_build_derivatives", forbidden)
    yield
    assert PRODUCTION_CONFIG.read_bytes() == before


@pytest.fixture
def cfg():
    return yaml.safe_load(PRODUCTION_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture
def window():
    return load_windows(json.loads(BOUNDARIES.read_bytes()))[0]


def row(tid, date, **fields):
    return dict(id=tid, game="PTCG", format="STANDARD", date=date, **fields)


def duplicates(window, raw_count, unique_count):
    # Generated synthetic IDs, all newer than the window. No live ID/count oracle.
    date = stamp(utc(window.end_utc) + timedelta(days=1))
    unique = [row(f"synthetic-{i}", date) for i in range(unique_count)]
    return unique + [unique[i % unique_count] for i in range(raw_count - unique_count)]


class SyntheticClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def list_tournaments(self, **kwargs):
        self.calls.append(("list", kwargs))
        return list(self.rows)

    def get_tournament_details(self, tid, **kwargs):
        self.calls.append(("details", tid))
        source = next(r for r in self.rows if r["id"] == tid)
        return dict(source, isPublic=True, decklists=True, isOnline=True, platform="PTCGL")


@pytest.mark.parametrize("raw_count,unique_count", [(5000, 4996), (100, 99), (50, 1)])
def test_duplicate_removal_cannot_prove_exhaustion(cfg, window, raw_count, unique_count):
    raw = duplicates(window, raw_count, unique_count)
    canonical, removed = acquisition._canonical_discovery_records(raw)
    assert len(raw) == raw_count and len(canonical) == unique_count
    assert removed == raw_count - unique_count
    with pytest.raises(acquisition.DiscoveryWindowIncompleteError, match="did not reach the scope start"):
        acquisition._discovery_candidates(canonical, scope=ProductionBackend(cfg)._scope(window),
                                          page_size=50, raw_record_count=len(raw))


@pytest.mark.parametrize("raw_count,unique_count", [(49, 1), (99, 50), (4999, 4996)])
def test_genuinely_short_raw_page_proves_exhaustion(cfg, window, raw_count, unique_count):
    raw = duplicates(window, raw_count, unique_count)
    canonical, _ = acquisition._canonical_discovery_records(raw)
    ids, diagnostics = acquisition._discovery_candidates(canonical, scope=ProductionBackend(cfg)._scope(window),
                                                        page_size=50, raw_record_count=len(raw))
    assert ids == () and diagnostics["candidate_count"] == 0
    assert diagnostics["discovery_rows"] == unique_count  # existing output contract


@pytest.mark.parametrize("caller", ["historical", "shared_live"])
def test_both_callers_fail_closed_before_details_on_5000_raw_4996_unique(cfg, window, tmp_path, caller):
    client = SyntheticClient(duplicates(window, 5000, 4996))
    backend = ProductionBackend(cfg, client=client)
    with pytest.raises(acquisition.DiscoveryWindowIncompleteError, match="increase discovery_max_pages"):
        if caller == "historical":
            backend._discover(window, {}, tmp_path)
        else:
            acquisition.run_limitless_api_acquisition(
                game="PTCG", format="STANDARD", resolved_scope=backend._scope(window),
                acquisition_started_at=utc(window.end_utc), raw_store_root=tmp_path / "raw-store",
                client=client, eligibility=backend.eligibility, discovery_page_size=50,
                discovery_max_pages=100, software_git_revision="synthetic-offline", run_id="synthetic-d3")
    assert [call[0] for call in client.calls] == ["list"]
    assert client.calls[0][1]["page_size"] == 50
    assert client.calls[0][1]["max_pages"] == 100
    assert client.calls[0][1]["use_cache"] is False


def test_full_raw_pages_reaching_start_preserve_candidate_scope(cfg, window):
    start, end = utc(window.start_utc), utc(window.end_utc)
    raw = [row("synthetic-before", stamp(start - timedelta(seconds=1))),
           row("synthetic-start", stamp(start)), row("synthetic-inside", stamp(start + timedelta(days=1))),
           row("synthetic-end", stamp(end))]
    raw.append(dict(raw[2], id="synthetic-wrong-game", game="POCKET"))
    raw *= 10
    canonical, _ = acquisition._canonical_discovery_records(raw)
    ids, diagnostics = acquisition._discovery_candidates(canonical, scope=ProductionBackend(cfg)._scope(window),
                                                        page_size=50, raw_record_count=len(raw))
    assert ids == ("synthetic-inside", "synthetic-start")
    assert diagnostics["discovery_wrong_game_rows"] == 1


@pytest.mark.parametrize("raw_count,accepted", [(49, True), (50, False)])
def test_invalid_date_conservative_candidates_still_require_raw_exhaustion(cfg, window, raw_count, accepted):
    raw = [row("synthetic-invalid-date", "unknown")] * raw_count
    canonical, _ = acquisition._canonical_discovery_records(raw)
    kwargs = dict(scope=ProductionBackend(cfg)._scope(window), page_size=50, raw_record_count=len(raw))
    if accepted:
        ids, _ = acquisition._discovery_candidates(canonical, **kwargs)
        assert ids == ("synthetic-invalid-date",)
    else:
        with pytest.raises(acquisition.DiscoveryWindowIncompleteError, match="no valid dates before"):
            acquisition._discovery_candidates(canonical, **kwargs)


def test_historical_config_changes_only_two_operational_settings(cfg):
    historical = yaml.safe_load(HISTORICAL_CONFIG.read_text(encoding="utf-8"))
    expected = deepcopy(cfg)
    expected["source"]["tournament_api"].update(discovery_page_size=100000, discovery_max_pages=2)
    assert historical == expected
    assert cfg["source"]["tournament_api"]["discovery_page_size"] == 50
    assert cfg["source"]["tournament_api"]["discovery_max_pages"] == 100
    assert historical["source"]["tournament_api"]["min_request_interval_seconds"] == 4.0


@pytest.mark.parametrize("explicit", [False, True])
def test_historical_cli_default_and_explicit_override(tmp_path, monkeypatch, explicit):
    from historical_rebuild import __main__ as cli
    observed = []

    class StatusOnlyRunner:
        def __init__(self, root, windows, backend):
            observed.append(backend.cfg)

        def status(self):
            return []

    monkeypatch.setattr(cli, "Runner", StatusOnlyRunner)
    monkeypatch.setattr(cli, "OUTPUT_ROOT", tmp_path / "Rebuild")
    args = ["--boundaries", str(BOUNDARIES)]
    if explicit:
        args += ["--config", str(PRODUCTION_CONFIG)]
    assert cli.main([*args, "status"]) == 0
    expected = PRODUCTION_CONFIG if explicit else HISTORICAL_CONFIG
    assert observed == [yaml.safe_load(expected.read_text(encoding="utf-8"))]
    assert not (tmp_path / "Rebuild").exists()


@pytest.mark.parametrize("code", ["SVI", "PAL"])
def test_complete_historical_discovery_selects_synthetic_in_window_ids(tmp_path, code):
    window = next(w for w in load_windows(json.loads(BOUNDARIES.read_bytes())) if w.window_id == code)
    start, end = utc(window.start_utc), utc(window.end_utc)
    raw = [row("synthetic-first", stamp(start)), row("synthetic-second", stamp(start + timedelta(days=1))),
           row("synthetic-next-window", stamp(end)), row("synthetic-earlier", stamp(start - timedelta(days=1)))]
    client = SyntheticClient(raw)
    backend = ProductionBackend(yaml.safe_load(HISTORICAL_CONFIG.read_text(encoding="utf-8")), client=client)
    discovery = backend._discover(window, {}, tmp_path)
    assert discovery["candidate_ids"] == ["synthetic-first", "synthetic-second"]
    frozen = backend._freeze(window, {"DISCOVERY": discovery}, tmp_path)
    assert [t["id"] for t in frozen["tournaments"]] == discovery["candidate_ids"]
    assert client.calls[0][1] == dict(game="PTCG", format="STANDARD", page_size=100000, max_pages=2, use_cache=False)


@pytest.mark.parametrize("second_page", ["short", "full"])
def test_large_bounded_client_continuation_still_fails_closed(window, tmp_path, second_page):
    cfg = yaml.safe_load(HISTORICAL_CONFIG.read_text(encoding="utf-8"))
    count = cfg["source"]["tournament_api"]["discovery_page_size"]
    newer = row("synthetic-newer", stamp(utc(window.end_utc) + timedelta(days=1)))
    pages = [[newer] * count, [newer] if second_page == "short" else [newer] * count]

    class PagedClient(LimitlessTournamentApiClient):
        def __init__(self):
            self.calls = []

        def _request_json(self, path, *, params, use_cache):
            self.calls.append(params)
            assert path == "/tournaments" and use_cache is False
            return pages[params["page"] - 1]

    client = PagedClient()
    backend = ProductionBackend(cfg, client=client)
    if second_page == "short":
        assert backend._discover(window, {}, tmp_path)["candidate_ids"] == []
    else:
        with pytest.raises(acquisition.DiscoveryWindowIncompleteError):
            backend._discover(window, {}, tmp_path)
    assert [call["page"] for call in client.calls] == [1, 2]
    assert all(call["limit"] == count for call in client.calls)


class LegacyDiscoveryBackend(ProductionBackend):
    """Reproduce the old deduplicated-count error only in a temporary fixture."""
    def _discover(self, window, inputs, directory):
        raw = self._network("list_tournaments", game="PTCG", format="STANDARD", page_size=50, max_pages=100)
        canonical, _ = acquisition._canonical_discovery_records(raw)
        ids, diagnostics = acquisition._discovery_candidates(canonical, scope=self._scope(window),
                                                            page_size=50, raw_record_count=len(canonical))
        return {"candidate_ids": list(ids), "diagnostics": diagnostics}


def test_scoped_resume_replaces_discovery_and_freeze_preserving_audit(cfg, window, tmp_path, monkeypatch):
    legacy = LegacyDiscoveryBackend(cfg, client=SyntheticClient(duplicates(window, 5000, 4996)))
    root = tmp_path / "synthetic-rebuild"
    old_runner = Runner(root, [window], legacy, Progress(StringIO()))
    with pytest.raises(RebuildError, match="TOURNAMENT_IDS_FROZEN.*no eligible tournaments"):
        old_runner.run("next")
    prior = old_runner.status()[0]
    assert prior["status"] == "BLOCKED" and prior["current_stage"] == "TOURNAMENT_IDS_FROZEN"
    audit = {p: p.read_bytes() for p in root.rglob("result.json")}
    checkpoint = root / window.window_id / "state/current.json"
    before = checkpoint.read_bytes()
    client = SyntheticClient([row("synthetic-resumed", window.start_utc)])
    backend = ProductionBackend(yaml.safe_load(HISTORICAL_CONFIG.read_text(encoding="utf-8")), client=client)
    new_runner = Runner(root, [window], backend, Progress(StringIO()))
    assert {s for s in STAGES if old_runner.semantics[s] != new_runner.semantics[s]} == {"DISCOVERY"}
    state = new_runner.status()[0]
    assert state["current_stage"] == "DISCOVERY"
    assert state["stages"]["DISCOVERY"]["status"] == "STALE"
    assert state["stages"]["WINDOW_READY"] == prior["stages"]["WINDOW_READY"]
    assert checkpoint.read_bytes() == before  # status never edits audit evidence

    class StopBeforeRaw(Exception):
        pass

    def stop_before_raw(*args, **kwargs):
        raise StopBeforeRaw("synthetic stop after successful freeze; RAW must not execute")

    monkeypatch.setattr(Runner, "_acquire", stop_before_raw)
    with pytest.raises(RebuildError, match="synthetic stop after successful freeze"):
        new_runner.run("next")
    resumed = new_runner.status()[0]
    assert resumed["stages"]["WINDOW_READY"] == prior["stages"]["WINDOW_READY"]
    for stage in ("DISCOVERY", "TOURNAMENT_IDS_FROZEN"):
        assert resumed["stages"][stage]["status"] == "VALID"
        assert resumed["stages"][stage]["input_fingerprint"] != prior["stages"][stage]["input_fingerprint"]
    frozen = read_result(root / window.window_id, resumed["stages"]["TOURNAMENT_IDS_FROZEN"]["artifact"])
    assert [t["id"] for t in frozen["tournaments"]] == ["synthetic-resumed"]
    assert resumed["raw"] == {}
    assert all(p.read_bytes() == raw for p, raw in audit.items())
    assert [call[0] for call in client.calls] == ["list", "details"]
