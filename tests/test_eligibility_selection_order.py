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
def test_historical_freeze_uses_shared_selector_and_excludes_noneligible(context, tmp_path, invalid):
    backend, window = context
    backend.client = DetailsClient(window, invalid=invalid)
    inputs = {"DISCOVERY": {"candidate_ids": [row["id"] for row in backend.client.rows]}}
    frozen = backend._freeze(window, inputs, tmp_path)
    assert [row["id"] for row in frozen["tournaments"]] == ["synthetic-valid"]
    assert "synthetic-offline" not in [row["id"] for row in frozen["tournaments"]]
    if invalid:
        assert frozen["exclusion_counts"]["invalid_record"] == 1
        assert frozen["invalid_record_ids"] == ["synthetic-offline"]
        assert frozen["eligibility_evidence_coverage"]["percent"] == 50.0
    else:
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


# D3-R3 extends the contract above; all D3-R2 regressions remain unchanged.
@pytest.mark.parametrize("fields,missing,reason", [
    ({"is_online": False}, ("platform",), "wrong_channel"),
    ({"is_online": True}, ("platform",), "invalid_record"),
    ({"is_online": True, "decklists": False},
     ("platform",), "decklists_disabled"),
    ({"is_online": True, "platform": "CAM", "decklists": False},
     (), "wrong_platform"),
    ({"is_online": False, "decklists": False},
     ("platform",), "wrong_channel"),
    ({"is_online": "unknown"}, (), "invalid_record"),
    ({}, ("is_online",), "invalid_record"),
    ({"is_online": "unknown", "decklists": False},
     (), "decklists_disabled"),
    ({"decklists": False}, ("is_online",), "decklists_disabled"),
    ({"is_public": False, "is_online": "unknown", "decklists": "bad"},
     ("platform",), "not_public"),
    ({"is_public": "unknown"}, (), "invalid_record"),
    ({}, ("is_public",), "invalid_record"),
    ({}, ("platform", "decklists"), "invalid_record"),
    ({"decklists": "false"}, ("platform",), "invalid_record"),
    ({"game": [], "decklists": False}, (), "invalid_record"),
    ({"date": "bad", "decklists": False}, (), "invalid_record"),
])
def test_definitive_exclusion_required_cases(context, fields, missing, reason):
    row = record(context[1], **fields)
    for key in missing:
        row.pop(key)
    result = classify(context, [row])
    assert result.tournament_ids == ()
    assert {k: v for k, v in result.exclusion_counts.items() if v} == {
        reason: 1
    }


def test_simple_reorder_would_change_well_formed_priority(context):
    row = record(context[1], platform="CAM", decklists=False)
    result = classify(context, [row])
    assert result.exclusion_counts["wrong_platform"] == 1
    assert result.exclusion_counts["decklists_disabled"] == 0
    # A decklists-first chain would choose the latter, violating D3-R2.


def test_all_eligibility_outcome_combinations_preserve_positive_proof(context):
    fields = ("is_public", "is_online", "platform", "decklists")
    reasons = (
        "not_public", "wrong_channel", "wrong_platform", "decklists_disabled"
    )
    # PASS / EXCLUDE / missing / malformed, independently for every criterion.
    # Includes every fully formed combination and all uncertain mixtures.
    for states in product(range(4), repeat=4):
        row = record(context[1])
        for key, state in zip(fields, states):
            if state == 1:
                row[key] = "CAM" if key == "platform" else False
            elif state == 2:
                row.pop(key)
            elif state == 3:
                row[key] = {"malformed": True}
        result = classify(context, [row])
        if 1 in states:
            reason = reasons[states.index(1)]
        elif any(state >= 2 for state in states):
            reason = "invalid_record"
        else:
            reason = None
        assert result.tournament_ids == (
            (row["id"],) if reason is None else ()
        ), states
        assert {k: v for k, v in result.exclusion_counts.items() if v} == (
            {} if reason is None else {reason: 1}
        ), states


@pytest.mark.parametrize("field,value", [
    ("id", None), ("id", " "), ("id", ["bad"]),
    ("id", {"bad": "identity"}), ("id", True),
    ("game", None), ("game", []), ("game", ""),
    ("format", []), ("format", {}),
    ("date", None), ("date", "bad"), ("date", "2023-09-07T23:00:00"),
])
def test_malformed_core_cannot_be_hidden_by_decklists_false(
    context, field, value
):
    row = record(context[1], decklists=False, **{field: value})
    result = classify(context, [row])
    assert result.tournament_ids == ()
    assert result.exclusion_counts["invalid_record"] == 1
    assert result.exclusion_counts["decklists_disabled"] == 0


@pytest.mark.parametrize("game", ["POCKET", "PTCG"])
def test_scope_exclusion_still_requires_valid_core_date(context, game):
    row = record(context[1], game=game, date="bad", decklists=False)
    result = classify(context, [row])
    assert result.exclusion_counts["invalid_record"] == 1


@pytest.mark.parametrize("require_online", [None, False, True])
@pytest.mark.parametrize("require_public,require_decklists", [
    (False, False), (False, True), (True, False), (True, True)
])
def test_disabled_filters_and_reverse_channel_policy_keep_membership(
    context, require_online, require_public, require_decklists
):
    backend, window = context
    policy = replace(
        backend.eligibility, require_online=require_online,
        require_public=require_public, require_decklists=require_decklists,
        allowed_platforms=None,
    )
    for public, online, decks in product((False, True), repeat=3):
        row = record(
            window, is_public=public, is_online=online,
            decklists=decks, platform=None,
        )
        result = select_tournaments(
            [row], scope=backend._scope(window), eligibility=policy
        )
        expected = (
            (not require_public or public)
            and (require_online is None or online == require_online)
            and (not require_decklists or decks)
        )
        assert result.tournament_ids == ((row["id"],) if expected else ())


@pytest.mark.parametrize("field", ["is_public", "decklists"])
def test_disabled_boolean_filters_keep_existing_evidence_requirement(
    context, field
):
    backend, window = context
    policy = replace(
        backend.eligibility, require_public=False, require_decklists=False
    )
    row = record(window)
    row.pop(field)
    result = select_tournaments(
        [row], scope=backend._scope(window), eligibility=policy
    )
    assert result.exclusion_counts["invalid_record"] == 1
    assert result.tournament_ids == ()


@pytest.mark.parametrize("decklists,reason", [
    (False, "decklists_disabled"), (True, "invalid_record"),
    (None, "invalid_record"), ("false", "invalid_record"),
])
def test_historical_freeze_obf_pattern_is_general_and_excluded(
    context, tmp_path, decklists, reason
):
    backend, window = context
    # Synthetic details with the verified OBF field pattern, without any real
    # ID, special window handling, API request or real checkpoint modification.
    backend.client = DetailsClient(window, invalid=True)
    backend.client.rows[1]["platform"] = None
    backend.client.rows[1]["decklists"] = decklists
    inputs = {
        "DISCOVERY": {
            "candidate_ids": [row["id"] for row in backend.client.rows]
        }
    }
    result = backend._freeze(window, inputs, tmp_path)
    assert [row["id"] for row in result["tournaments"]] == ["synthetic-valid"]
    assert result["exclusion_counts"][reason] == 1
    if reason == "invalid_record":
        assert result["invalid_record_count"] == 1
        assert result["invalid_record_ids"] == ["synthetic-offline"]
    else:
        assert result["invalid_record_count"] == 0
        assert result["exclusion_counts"]["invalid_record"] == 0

# D3-R4: invalid evidence remains excluded but is no longer a window-wide blocker.
def test_d3r4_acquisition_failure_identity_mismatch_and_zero_eligible_block(context, tmp_path):
    backend, window = context

    failing = DetailsClient(window, invalid=True)
    original_get = failing.get_tournament_details
    def fail_one(tid, **kwargs):
        if tid == "synthetic-offline":
            raise requests.Timeout("synthetic detail failure")
        return original_get(tid, **kwargs)
    failing.get_tournament_details = fail_one
    backend.client = failing
    inputs = {"DISCOVERY": {"candidate_ids": [row["id"] for row in failing.rows]}}
    with pytest.raises(RebuildError, match="synthetic detail failure"):
        backend._freeze(window, inputs, tmp_path)

    mismatch = DetailsClient(window, invalid=True)
    mismatch_get = mismatch.get_tournament_details
    def wrong_identity(tid, **kwargs):
        row = mismatch_get(tid, **kwargs)
        return ({**row, "id": "wrong-detail-id"}
                if tid == "synthetic-offline" else row)
    mismatch.get_tournament_details = wrong_identity
    backend.client = mismatch
    with pytest.raises(RebuildError, match="identity mismatch"):
        backend._freeze(window, {"DISCOVERY": {"candidate_ids": ["synthetic-valid", "synthetic-offline"]}}, tmp_path)

    zero = DetailsClient(window, invalid=True)
    zero.rows = [zero.rows[1]]
    backend.client = zero
    with pytest.raises(RebuildError, match="no eligible tournaments"):
        backend._freeze(
            window,
            {"DISCOVERY": {"candidate_ids": ["synthetic-offline"]}},
            tmp_path,
        )


def test_d3r4_freeze_audit_is_deterministic_and_reconciled(context, tmp_path):
    backend, window = context
    backend.client = DetailsClient(window, invalid=True)
    backend.client.rows = [
        record(window, id="z-valid") | {"isPublic": True, "isOnline": True},
        record(window, id="b-invalid") | {"isPublic": True, "isOnline": True},
        record(window, id="a-offline") | {"isPublic": True, "isOnline": False},
        record(window, id="c-nodecks") | {"isPublic": True, "isOnline": True, "decklists": False},
    ]
    for row in backend.client.rows:
        row.pop("is_public", None); row.pop("is_online", None)
        if row["id"] in {"b-invalid", "a-offline", "c-nodecks"}:
            row.pop("platform", None)
    ids = [row["id"] for row in backend.client.rows]
    first = backend._freeze(window, {"DISCOVERY": {"candidate_ids": ids}}, tmp_path)
    second = backend._freeze(window, {"DISCOVERY": {"candidate_ids": list(reversed(ids))}}, tmp_path)
    for key in (
        "candidate_count", "eligible_count", "exclusion_counts",
        "invalid_record_count", "invalid_record_ids", "invalid_record_evidence",
        "classifications", "eligibility_evidence_coverage",
        "classification_provenance",
    ):
        assert first[key] == second[key]
    assert first["candidate_count"] == 4
    assert first["eligible_count"] == 1
    assert [row["id"] for row in first["tournaments"]] == ["z-valid"]
    assert first["invalid_record_ids"] == ["b-invalid"]
    assert first["invalid_record_count"] == 1
    assert first["exclusion_counts"]["wrong_channel"] == 1
    assert first["exclusion_counts"]["decklists_disabled"] == 1
    assert first["eligibility_evidence_coverage"]["covered_count"] == 3
    assert first["eligibility_evidence_coverage"]["candidate_count"] == 4
    assert first["eligibility_evidence_coverage"]["percent"] == 75.0
    assert first["eligible_count"] + sum(first["exclusion_counts"].values()) == 4
    evidence = first["invalid_record_evidence"][0]
    assert evidence["id"] == "b-invalid"
    assert evidence["selection_record"]["platform"] is None
    assert first["classification_provenance"]["eligibility_policy"]["policy_id"] == backend.eligibility.policy_id

def test_d3r4_pre_freeze_compatibility_is_narrow_and_fail_closed(context, tmp_path):
    from historical_rebuild.production import PRE_D3_R4_FREEZE_ADAPTER
    from historical_rebuild.store import artifact, write_json

    backend, _ = context
    current = backend.semantics("TOURNAMENT_IDS_FROZEN")
    previous = deepcopy(current)
    previous["contract"] = 1
    previous["adapter"] = PRE_D3_R4_FREEZE_ADAPTER
    assert backend.can_reuse_semantics("TOURNAMENT_IDS_FROZEN", previous, current)

    unknown = deepcopy(previous)
    unknown["adapter"] = "0" * 64
    assert not backend.can_reuse_semantics("TOURNAMENT_IDS_FROZEN", unknown, current)

    root = tmp_path / "window"
    generation = root / "state" / "tournament_ids_frozen" / "legacy"
    generation.mkdir(parents=True)
    result_path = generation / "result.json"
    write_json(result_path, {"tournaments": [], "exclusion_counts": {"invalid_record": 0}})
    ref = artifact(root, generation)
    valid = {"status": "VALID", "artifact": ref}
    assert backend.can_reuse_checkpoint("TOURNAMENT_IDS_FROZEN", root, valid)
    assert not backend.can_reuse_checkpoint(
        "TOURNAMENT_IDS_FROZEN", root, {"status": "FAILED", "artifact": ref}
    )
    generation_bad = root / "state" / "tournament_ids_frozen" / "legacy-invalid"
    generation_bad.mkdir(parents=True)
    write_json(
        generation_bad / "result.json",
        {"tournaments": [], "exclusion_counts": {"invalid_record": 1}},
    )
    bad_ref = artifact(root, generation_bad)
    assert not backend.can_reuse_checkpoint(
        "TOURNAMENT_IDS_FROZEN", root,
        {"status": "VALID", "artifact": bad_ref},
    )
