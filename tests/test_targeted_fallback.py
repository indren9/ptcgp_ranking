"""R2 fixtures are synthetic proofs, NEVER historical release observations."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import socket

import pytest

from historical_boundaries.catalog import LEDGER, CANDIDATE, PREVIEW
from historical_boundaries import targeted_fallback as r2


@pytest.fixture(autouse=True)
def no_network_or_rebuild(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("R2 tests must never acquire history or run MARS")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    from historical_rebuild.runner import Runner
    monkeypatch.setattr(Runner, "run", forbidden)


@pytest.fixture
def ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8"))


@pytest.fixture
def plan(ledger):
    return next(p for p in r2.plans_from_ledger(ledger) if p.code == "SSP")


@pytest.fixture
def safe_audit():
    return r2.Feasibility("PROVEN_CARD_PRESENT_BY", "IMMUTABLE_AT_OBSERVATION", True,
                          ("synthetic-temporal-proof-not-real-history",))


def bundle(tid="one", present="17:35:00", *, target="SSP", date="17:30:00"):
    deck = {"pokemon": [{"count": 1, "name": "Synthetic Card", "set": target, "number": "001"}]}
    details = dict(id=tid, game="PTCG", platform="PTCGL", isOnline=True, decklists=True,
                   format="STANDARD", date="2024-11-07T" + date + "Z")
    proof = dict(tournament_id=tid, player="fixture", deck_sha256=r2.digest(deck),
        present_by_utc="2024-11-07T" + present + "Z", timestamp_semantics="PROVEN_CARD_PRESENT_BY",
        historical_edits="IMMUTABLE_AT_OBSERVATION", valid_play_on_ptcgl=True,
        evidence_ids=["synthetic-archived-proof"])
    return dict(details=details, standings=[dict(player="fixture", decklist=deck)],
                temporal_attestations={"fixture": proof})


class Provider:
    def __init__(self, *bundles):
        self.bundles = {b["details"]["id"]: b for b in bundles}
        self.list_calls = []
        self.fetch_calls = []
        self.fail_once = None

    def enumerate_interval(self, *args):
        self.list_calls.append(args)
        return [dict(id=b["details"]["id"], date=b["details"]["date"]) for b in self.bundles.values()]

    def fetch(self, tid):
        self.fetch_calls.append(tid)
        if self.fail_once == tid:
            self.fail_once = None
            raise RuntimeError("synthetic interruption")
        return deepcopy(self.bundles[tid])


def test_coherent_first_party_prohibits_fallback(plan, safe_audit, tmp_path):
    p = replace(plan, official_status="MACHINE_VERIFIED", candidates=(plan.candidates[0],))
    provider = Provider(bundle())
    result = r2.run_fallback(p, safe_audit, provider, root=tmp_path)
    assert result["fallback_outcome"] == "NOT_REQUIRED"
    assert result["authority_class"] == "MACHINE_VERIFIED_SOURCE"
    assert not provider.list_calls and not provider.fetch_calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("changes", [dict(code="TEF"), dict(code="../PRE"),
    dict(first_party_exhausted=False), dict(official_status="DATE_CONFIRMED"), dict(evidence_ids=())])
def test_only_configured_unresolved_researched_expansions(plan, safe_audit, tmp_path, changes):
    provider = Provider(bundle())
    with pytest.raises(ValueError):
        r2.run_fallback(replace(plan, **changes), safe_audit, provider, root=tmp_path)
    assert not provider.list_calls


def test_positive_bound_human_candidate_stops_early(plan, safe_audit, tmp_path):
    provider = Provider(bundle(), bundle("two", date="17:45:00"))
    result = r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert provider.fetch_calls == ["one"]
    assert provider.list_calls == [("SSP", *plan.interval)]
    assert result["status"] == "HUMAN_REVIEW_CANDIDATE"
    assert result["authority_class"] == "LIMITLESS_CORROBORATED"
    assert result["candidates_eliminated"] == ["2024-11-07T18:00:00Z"]
    assert result["proposed_start_utc"] == "2024-11-07T17:00:00Z"
    assert result["empirical_upper_bound_utc"] == "2024-11-07T17:35:00Z"
    assert result["reviewed"] is False
    positive = result["earliest_positive"]
    for field in ("tournament_id", "tournament_timestamp", "player", "card_id", "card_name",
                  "card_set", "target_expansion", "deck_sha256", "raw_sha256", "temporal_evidence_ids"):
        assert positive[field]


@pytest.mark.parametrize("present", ["18:00:00", "18:20:00"])
def test_observation_at_or_after_both_candidates_inconclusive(plan, safe_audit, tmp_path, present):
    result = r2.run_fallback(plan, safe_audit, Provider(bundle(present=present)), root=tmp_path)
    assert result["candidates_eliminated"] == []
    assert result["candidates_remaining"] == list(plan.candidates)
    assert result["proposed_start_utc"] is None
    assert result["fallback_outcome"] == "INCONCLUSIVE"


def test_no_observations_never_eliminates(plan, safe_audit, tmp_path):
    result = r2.run_fallback(plan, safe_audit, Provider(), root=tmp_path)
    assert result["candidates_remaining"] == list(plan.candidates)
    assert result["empirical_upper_bound_utc"] is None


def test_archetype_name_cannot_substitute_target_card(plan, safe_audit, tmp_path):
    raw = bundle(target="SCR")
    raw["details"]["name"] = "SSP Surging Sparks"
    raw["standings"][0]["deck"] = {"name": "SSP Synthetic Card", "id": "SSP"}
    result = r2.run_fallback(plan, safe_audit, Provider(raw), root=tmp_path)
    assert result["earliest_positive"] is None
    assert result["candidates_eliminated"] == []


@pytest.mark.parametrize("field,value", [("set", "PRE"), ("number", None), ("name", ""),
                                        ("count", 0), ("count", True)])
def test_exact_card_set_number_name_and_positive_count_required(plan, field, value):
    raw = bundle()
    raw["standings"][0]["decklist"]["pokemon"][0][field] = value
    raw["temporal_attestations"]["fixture"]["deck_sha256"] = r2.digest(raw["standings"][0]["decklist"])
    assert r2.inspect_deck(plan, raw, raw["standings"][0])[0] == []


@pytest.mark.parametrize("semantics", ["ORGANIZER_SCHEDULED_START", "CREATION_TIME", "REGISTRATION_CLOSE", "UNKNOWN"])
def test_timestamp_semantics_stop_before_any_download(plan, safe_audit, tmp_path, semantics):
    provider = Provider(bundle())
    result = r2.run_fallback(plan, replace(safe_audit, timestamp_semantics=semantics), provider, root=tmp_path)
    assert result["fallback_outcome"] == "INCONCLUSIVE_PREFLIGHT"
    assert result["tournaments_inspected"] == result["decklists_inspected"] == 0
    assert not provider.list_calls and not provider.fetch_calls


@pytest.mark.parametrize("edits,outcome", [("RETROACTIVE_EDITS_UNSAFE", "UNSAFE"), ("NOT_ESTABLISHED", "INCONCLUSIVE_PREFLIGHT")])
def test_historical_edit_risk_fails_closed(plan, safe_audit, tmp_path, edits, outcome):
    provider = Provider(bundle())
    result = r2.run_fallback(plan, replace(safe_audit, historical_edits=edits), provider, root=tmp_path)
    assert result["fallback_outcome"] == outcome
    assert not provider.fetch_calls and result["proposed_start_utc"] is None


def test_unsafe_individual_deck_stops_entire_search(plan, safe_audit, tmp_path):
    raw = bundle()
    raw["temporal_attestations"]["fixture"]["historical_edits"] = "RETROACTIVE_EDITS_UNSAFE"
    provider = Provider(raw, bundle("two", date="17:45:00"))
    result = r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert provider.fetch_calls == ["one"]
    assert result["fallback_outcome"] == "UNSAFE" and result["proposed_start_utc"] is None


@pytest.mark.parametrize("field,value", [("timestamp_semantics", "ORGANIZER_SCHEDULED_START"),
    ("tournament_id", "another"), ("player", "another"), ("deck_sha256", "0" * 64),
    ("valid_play_on_ptcgl", False), ("evidence_ids", []), ("present_by_utc", "2024-11-07T17:35:00")])
def test_individual_temporal_binding_cannot_be_assumed(plan, field, value):
    raw = bundle()
    raw["temporal_attestations"]["fixture"][field] = value
    assert r2.inspect_deck(plan, raw, raw["standings"][0])[0] == []


@pytest.mark.parametrize("field,value", [("platform", "PTCGO"), ("game", "POCKET"),
    ("isOnline", False), ("decklists", False), ("format", "CUSTOM")])
def test_only_valid_live_tournament_can_support_use(plan, field, value):
    raw = bundle()
    raw["details"][field] = value
    assert r2.inspect_deck(plan, raw, raw["standings"][0])[0] == []


def test_limitless_cannot_invent_timestamp_when_all_candidates_excluded(plan, safe_audit, tmp_path):
    result = r2.run_fallback(plan, safe_audit, Provider(bundle(present="16:59:00")), root=tmp_path)
    assert result["candidates_remaining"] == []
    assert result["proposed_start_utc"] is None
    assert result["status"] == "OFFICIAL_CONFLICT"


def test_resume_skips_completed_valid_evidence(plan, safe_audit, tmp_path, monkeypatch):
    provider = Provider(bundle(target="SCR"), bundle("two", date="17:45:00"))
    provider.fail_once = "two"
    with pytest.raises(RuntimeError, match="interruption"):
        r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    inspected = []
    original = r2.inspect_deck
    def spy(plan, raw, row):
        inspected.append(raw["details"]["id"])
        return original(plan, raw, row)
    monkeypatch.setattr(r2, "inspect_deck", spy)
    result = r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert len(provider.list_calls) == 1 and provider.fetch_calls == ["one", "two", "two"]
    assert inspected == ["two"]
    again = r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert again == result and inspected == ["two"]
    state = json.loads(Path(result["checkpoint"]).read_text())
    assert len(state["raw_hashes"]) == 2 and len(state["inspected_decks"]) == 2
    assert state["earliest_positive"] == result["earliest_positive"]


def test_resume_after_partial_deck_inspection(plan, safe_audit, tmp_path, monkeypatch):
    raw = bundle(target="SCR")
    raw["standings"].append(dict(player="second", decklist={}))
    provider = Provider(raw)
    original = r2.inspect_deck
    def interrupt(plan, raw, row):
        if row["player"] == "second":
            raise RuntimeError("interrupted deck inspection")
        return original(plan, raw, row)
    monkeypatch.setattr(r2, "inspect_deck", interrupt)
    with pytest.raises(RuntimeError):
        r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    calls = []
    def resume(plan, raw, row):
        calls.append(row["player"])
        return original(plan, raw, row)
    monkeypatch.setattr(r2, "inspect_deck", resume)
    result = r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert calls == ["second"] and provider.fetch_calls == ["one"]
    assert result["decklists_inspected"] == 2


def test_resume_honors_stop_after_positive_checkpoint(plan, safe_audit, tmp_path, monkeypatch):
    provider = Provider(bundle(), bundle("two", date="17:45:00"))
    original = r2.write_json
    def interrupt_after_write(path, value):
        original(path, value)
        if path.name == "checkpoint.json" and value.get("positive_observations"):
            raise RuntimeError("power loss after positive checkpoint")
    monkeypatch.setattr(r2, "write_json", interrupt_after_write)
    with pytest.raises(RuntimeError):
        r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    monkeypatch.setattr(r2, "write_json", original)
    result = r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert result["status"] == "HUMAN_REVIEW_CANDIDATE"
    assert provider.fetch_calls == ["one"]


@pytest.mark.parametrize("corrupt", ["checkpoint", "raw"])
def test_corrupt_cached_evidence_fails_closed(plan, safe_audit, tmp_path, corrupt):
    from pathlib import Path
    provider = Provider(bundle())
    result = r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    path = Path(result["checkpoint"])
    if corrupt == "raw":
        path = next((path.parent / "raw").glob("*.json"))
    path.write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="integrity|digest"):
        r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert provider.fetch_calls == ["one"]


def test_progress_deterministic_and_nonessential(plan, safe_audit, tmp_path):
    a, b = [], []
    first = r2.run_fallback(plan, safe_audit, Provider(bundle()), root=tmp_path / "a", emit=a.append)
    second = r2.run_fallback(plan, safe_audit, Provider(bundle()), root=tmp_path / "b", emit=b.append)
    assert a == b and "EXCLUDED" in a[-1]
    def broken(_):
        raise RuntimeError("display unavailable")
    third = r2.run_fallback(plan, safe_audit, Provider(bundle()), root=tmp_path / "c", emit=broken)
    for result in (first, second, third):
        result.pop("checkpoint")
    assert first == second == third


def test_unbounded_discovery_prohibited(plan, safe_audit, tmp_path):
    provider = Provider(bundle())
    result = r2.run_fallback(plan, replace(safe_audit, bounded_query=False), provider, root=tmp_path)
    assert result["fallback_outcome"] == "INCONCLUSIVE_PREFLIGHT" and not provider.list_calls


@pytest.mark.parametrize("date", ["16:59:00", "18:00:00", "18:01:00"])
def test_out_of_interval_provider_data_rejected_before_fetch(plan, safe_audit, tmp_path, date):
    provider = Provider(bundle(date=date))
    with pytest.raises(ValueError, match="outside"):
        r2.run_fallback(plan, safe_audit, provider, root=tmp_path)
    assert not provider.fetch_calls


def test_official_candidate_derivation_excludes_event_clocks(ledger):
    plans = r2.plans_from_ledger(ledger)
    assert [p.code for p in plans] == list(r2.TARGETS)
    for p, date in zip(plans, ("2024-01-25", "2024-11-07", "2025-01-16")):
        assert p.candidates == (date + "T17:00:00Z", date + "T18:00:00Z")
    statements = {s["statement_id"]: s for s in ledger["temporal_statements"]}
    assert all("full_expansion" in statements[sid]["proposition_kinds"] for p in plans for sid in p.evidence_ids)
    assert any("conditional" in note for note in plans[0].derivation)
    assert any("literal 2024" in note for note in plans[2].derivation)


def test_real_preflight_retains_review_false_and_never_runs_mars(ledger, tmp_path):
    before = [p.read_bytes() for p in (LEDGER, CANDIDATE, PREVIEW)]
    for plan in r2.plans_from_ledger(ledger):
        result = r2.run_fallback(plan, r2.public_api_audit(["documentation"]), None, root=tmp_path)
        assert result["status"] == plan.official_status
        assert result["reviewed"] is False and result["tournaments_inspected"] == 0
        assert result["proposed_start_utc"] is None
    assert before == [p.read_bytes() for p in (LEDGER, CANDIDATE, PREVIEW)]


def test_committed_r2_artifact_replays_without_network(tmp_path):
    from historical_boundaries.r2 import ARTIFACT, build
    actual = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(build(actual["research"], root=tmp_path)))
    assert actual == expected
    assert actual["verdict"] == "NOT_READY" and actual["reviewed"] is False
    assert actual["tournament_api_requests"] == 0
    assert {r["expansion"] for r in actual["results"]} == set(r2.TARGETS)
    assert all(r["earliest_positive"] is None for r in actual["results"])


def test_changed_research_requires_reaudit(tmp_path):
    from historical_boundaries.r2 import ARTIFACT, build
    research = json.loads(ARTIFACT.read_text(encoding="utf-8"))["research"]
    research["reviewed"] = True
    with pytest.raises(ValueError, match="integrity"):
        build(research, root=tmp_path)
