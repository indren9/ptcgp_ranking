"""D3-R2: ordered eligibility, fail-closed freeze and scoped offline resume."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from itertools import product
import json
import socket

import pytest
import requests
import yaml

from acquisition.selection import select_tournaments
from historical_rebuild.model import RebuildError, load_windows, stamp, utc
from historical_rebuild.production import BASE, ProductionBackend
from historical_rebuild.runner import Progress, Runner, STAGES
from historical_rebuild.store import read_result


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("D3-R2 tests cannot access network, RAW, Core, MARS or publication")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    for method in ("acquire", "_normalize", "_core", "_mars", "_report"):
        monkeypatch.setattr(ProductionBackend, method, forbidden)


@pytest.fixture
def context():
    cfg = yaml.safe_load((BASE / "config/tcg_historical_rebuild.yaml").read_text(encoding="utf-8"))
    backend = ProductionBackend(cfg)
    window = load_windows(json.loads((BASE / "data/approved/releases/tcg_live_historical_boundaries_reviewed.json").read_bytes()))[0]
    return backend, window


def record(window, **fields):
    return dict(id="synthetic-valid", game="PTCG", format="STANDARD", date=window.start_utc,
                is_public=True, is_online=True, platform="PTCGL", decklists=True) | fields


def classify(context, rows):
    backend, window = context
    return select_tournaments(rows, scope=backend._scope(window), eligibility=backend.eligibility)


@pytest.mark.parametrize("online,platform,expected", [
    (False, None, "wrong_channel"), (False, "OTHER", "wrong_channel"),
    (True, None, "invalid_record"), (True, "OTHER", "wrong_platform"),
    (True, "PTCGL", None), (True, " ptcgl ", None),
])
def test_channel_precedes_platform_without_weakening_membership(context, online, platform, expected):
    row = record(context[1], is_online=online)
    if platform is None:
        row.pop("platform")
    else:
        row["platform"] = platform
    result = classify(context, [row])
    assert result.tournament_ids == ((row["id"],) if expected is None else ())
    assert {k: v for k, v in result.exclusion_counts.items() if v} == ({} if expected is None else {expected: 1})


@pytest.mark.parametrize("value", [None, "true", "false", 0, 1, [], {}])
def test_missing_or_malformed_online_remains_invalid(context, value):
    row = record(context[1], is_online=value)
    if value is None:
        row.pop("is_online")
    result = classify(context, [row])
    assert result.tournament_ids == () and result.exclusion_counts["invalid_record"] == 1


@pytest.mark.parametrize("value", [None, "", " ", [], {}, 1, False])
def test_potentially_eligible_invalid_platform_blocks(context, value):
    result = classify(context, [record(context[1], platform=value)])
    assert result.tournament_ids == () and result.exclusion_counts["invalid_record"] == 1


@pytest.mark.parametrize("reason,fields", [
    ("wrong_game", {"game": "POCKET"}), ("wrong_format", {"format": "EXPANDED"}),
    ("outside_window", {"date": "2020-01-01T00:00:00Z"}),
    ("not_public", {"is_public": False}), ("wrong_channel", {"is_online": False}),
    ("wrong_platform", {"platform": "OTHER"}),
])
@pytest.mark.parametrize("malformed", [False, True])
def test_later_fields_cannot_override_earlier_exclusion(context, reason, fields, malformed):
    row = record(context[1], **fields)
    later = {
        "wrong_game": ("is_public", "is_online", "platform", "decklists"),
        "wrong_format": ("is_public", "is_online", "platform", "decklists"),
        "outside_window": ("is_public", "is_online", "platform", "decklists"),
        "not_public": ("is_online", "platform", "decklists"),
        "wrong_channel": ("platform", "decklists"), "wrong_platform": ("decklists",),
    }[reason]
    for key in later:
        if malformed:
            row[key] = {"malformed": True}
        else:
            row.pop(key)
    result = classify(context, [row])
    assert result.tournament_ids == ()
    assert {k: v for k, v in result.exclusion_counts.items() if v} == {reason: 1}


@pytest.mark.parametrize("key,value", [
    ("id", None), ("date", None), ("date", "not-a-date"), ("date", "2023-04-01T12:00:00"),
    ("game", None), ("game", ""), ("game", []), ("format", {}),
    ("is_public", None), ("is_public", "true"), ("decklists", None), ("decklists", 1),
])
def test_required_evidence_never_becomes_included(context, key, value):
    row = record(context[1], **{key: value})
    if value is None:
        row.pop(key)
    result = classify(context, [row])
    assert result.tournament_ids == () and result.exclusion_counts["invalid_record"] == 1


def test_invalid_core_date_is_not_hidden_by_wrong_game(context):
    result = classify(context, [record(context[1], game="POCKET", date="bad")])
    assert result.exclusion_counts["invalid_record"] == 1


def test_well_formed_membership_and_priority_are_unchanged(context):
    window = context[1]
    rows, expected, counts = [], [], Counter()
    for i, (game, fmt, inside, public, online, platform, decks) in enumerate(product(
            ("PTCG", "POCKET"), ("STANDARD", "EXPANDED"), (True, False),
            (True, False), (True, False), ("PTCGL", "OTHER"), (True, False))):
        row = record(window, id=f"synthetic-{i:03d}", game=game, format=fmt,
                     date=window.start_utc if inside else window.end_utc, is_public=public,
                     is_online=online, platform=platform, decklists=decks)
        rows.append(row)
        # Frozen pre-fix exclusion chain, for fully formed evidence only.
        reasons = [(game != "PTCG", "wrong_game"), (fmt != "STANDARD", "wrong_format"),
                   (not inside, "outside_window"), (not public, "not_public"),
                   (not online, "wrong_channel"), (platform != "PTCGL", "wrong_platform"),
                   (not decks, "decklists_disabled")]
        reason = next((reason for excluded, reason in reasons if excluded), None)
        if reason is None:
            expected.append(row["id"])
        else:
            counts[reason] += 1
    result = classify(context, rows)
    assert result.tournament_ids == tuple(expected)
    assert {k: v for k, v in result.exclusion_counts.items() if v} == counts
    assert classify(context, reversed(rows)) == result


class DetailsClient:
    def __init__(self, window, invalid=False):
        self.calls = []
        self.rows = [dict(id="synthetic-valid", game="PTCG", format="STANDARD", date=window.start_utc,
                          isPublic=True, isOnline=True, platform="PTCGL", decklists=True),
                     dict(id="synthetic-offline", game="PTCG", format="STANDARD", date=window.start_utc,
                          isPublic=True, isOnline=invalid, decklists=True)]

    def list_tournaments(self, **kwargs):
        self.calls.append("discovery")
        return deepcopy(self.rows)

    def get_tournament_details(self, tid, **kwargs):
        self.calls.append(tid)
        return deepcopy(next(row for row in self.rows if row["id"] == tid))


@pytest.mark.parametrize("invalid", [False, True])
def test_historical_freeze_uses_shared_selector_and_remains_fail_closed(context, tmp_path, invalid):
    backend, window = context
    backend.client = DetailsClient(window, invalid=invalid)
    inputs = {"DISCOVERY": {"candidate_ids": [row["id"] for row in backend.client.rows]}}
    if invalid:
        with pytest.raises(RebuildError, match="invalid eligibility evidence"):
            backend._freeze(window, inputs, tmp_path)
    else:
        frozen = backend._freeze(window, inputs, tmp_path)
        assert [row["id"] for row in frozen["tournaments"]] == ["synthetic-valid"]
        assert frozen["exclusion_counts"]["wrong_channel"] == 1
        assert frozen["exclusion_counts"]["invalid_record"] == 0


@pytest.mark.parametrize("dependency", ["adapter", "config", "files", "functions", "packages", "python", "contract"])
def test_discovery_compatibility_never_ignores_actual_dependency_changes(context, dependency):
    backend, _ = context
    current = backend.semantics("DISCOVERY")
    previous = json.loads(json.dumps(current))
    previous["files"]["acquisition/selection.py"] = "0" * 64
    assert backend.can_reuse_semantics("DISCOVERY", previous, current)
    assert not backend.can_reuse_semantics("TOURNAMENT_IDS_FROZEN", previous, current)
    changed = deepcopy(current)
    if dependency == "files":
        changed["files"]["acquisition/scope.py"] = "1" * 64
    else:
        changed[dependency] = {"changed": True}
    assert not backend.can_reuse_semantics("DISCOVERY", previous, changed)


@pytest.mark.parametrize("mutation", ["missing", "malformed"])
def test_discovery_compatibility_requires_prior_fingerprint_provenance(context, mutation):
    backend, _ = context
    current = backend.semantics("DISCOVERY")
    previous = deepcopy(current)
    if mutation == "missing":
        previous["files"].pop("acquisition/selection.py")
    else:
        previous["files"]["acquisition/selection.py"] = "bad"
    assert not backend.can_reuse_semantics("DISCOVERY", previous, current)
    assert not backend.can_reuse_semantics("DISCOVERY", None, current)


@pytest.mark.parametrize("change", ["selector_only", "corrupt_discovery", "window", "discovery_config"])
def test_resume_reuses_only_safe_discovery_and_preserves_generations(context, tmp_path, monkeypatch, change):
    backend, window = context
    backend.client = DetailsClient(window)
    previous = {stage: deepcopy(backend.semantics(stage)) for stage in STAGES}
    for stage in ("DISCOVERY", "TOURNAMENT_IDS_FROZEN"):
        previous[stage]["files"]["acquisition/selection.py"] = "0" * 64
    # Synthetic predecessor checkpoint: previous selector fingerprint and the
    # verified old FREEZE failure. Nothing is copied from or written to real SVI.
    monkeypatch.setattr(backend, "semantics", lambda stage: previous[stage])

    def legacy_freeze(*args, **kwargs):
        raise RebuildError("invalid eligibility evidence; cannot freeze a complete selection")

    monkeypatch.setattr(backend, "_freeze", legacy_freeze)
    root = tmp_path / "synthetic-rebuild"
    old = Runner(root, [window], backend, Progress(StringIO()))
    with pytest.raises(RebuildError, match="invalid eligibility evidence"):
        old.run("next")
    prior = old.status()[0]
    cfg = deepcopy(backend.cfg)
    if change == "corrupt_discovery":
        artifact = prior["stages"]["DISCOVERY"]["artifact"]
        (root / window.window_id / artifact["path"] / "result.json").write_bytes(b"corrupt fixture")
    elif change == "window":
        window = replace(window, end_utc=stamp(utc(window.end_utc) + timedelta(days=1)))
    elif change == "discovery_config":
        cfg["source"]["tournament_api"]["discovery_max_pages"] += 1
    audit = {p: p.read_bytes() for p in root.rglob("result.json")}
    checkpoint = root / window.window_id / "state/current.json"
    raw_checkpoint = checkpoint.read_bytes()
    fresh = ProductionBackend(cfg, client=DetailsClient(window))
    runner = Runner(root, [window], fresh, Progress(StringIO()))
    status = runner.status()[0]
    assert checkpoint.read_bytes() == raw_checkpoint
    assert status["stages"]["TOURNAMENT_IDS_FROZEN"]["status"] == "STALE"
    assert all(status["stages"][s]["status"] == "PENDING" for s in STAGES[3:])
    if change == "selector_only":
        assert status["stages"]["WINDOW_READY"] == prior["stages"]["WINDOW_READY"]
        assert status["stages"]["DISCOVERY"] == prior["stages"]["DISCOVERY"]
        assert status["current_stage"] == "TOURNAMENT_IDS_FROZEN"
    else:
        assert status["stages"]["DISCOVERY"]["status"] == "STALE"

    def stop_before_raw(*args, **kwargs):
        raise RebuildError("synthetic stop before RAW")

    monkeypatch.setattr(Runner, "_acquire", stop_before_raw)
    with pytest.raises(RebuildError, match="synthetic stop before RAW"):
        runner.run("next")
    resumed = runner.status()[0]
    assert resumed["stages"]["DISCOVERY"]["status"] == "VALID"
    assert resumed["stages"]["TOURNAMENT_IDS_FROZEN"]["status"] == "VALID"
    assert ("discovery" in fresh.client.calls) == (change != "selector_only")
    if change == "selector_only":
        assert resumed["stages"]["DISCOVERY"] == prior["stages"]["DISCOVERY"]
    frozen = read_result(root / window.window_id, resumed["stages"]["TOURNAMENT_IDS_FROZEN"]["artifact"])
    assert [row["id"] for row in frozen["tournaments"]] == ["synthetic-valid"]
    assert resumed["raw"] == {}
    assert all(p.read_bytes() == value for p, value in audit.items())
