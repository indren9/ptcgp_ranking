from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.expansions import Expansion
from sources.limitless import catalog_refresh as refresh


NOW = datetime(2026, 9, 14, 14, 0, tzinfo=UTC)
POCKET = {
    "source": {"game": "POCKET", "format": {"mode": "code", "code": "standard"}},
    "scraping": {"decks_url": "https://play.limitlesstcg.com/decks?game=POCKET"},
}
OLD = [Expansion("B4", "Clashing Currents", True), Expansion("B3b", "Shrouded Fable", False)]
NEW = [Expansion("B4a", "New Expansion", True), Expansion("B4", "Clashing Currents", False)]


def paths(tmp_path):
    return SimpleNamespace(cache=tmp_path / "cache")


def cache_path(tmp_path, cfg=POCKET, *, decks_url=None):
    return refresh.catalog_cache_path(paths(tmp_path), refresh.catalog_context(cfg, decks_url))


def seed(tmp_path, *, cfg=POCKET, expansions=OLD, when=None, decks_url=None):
    return refresh.ensure_catalog_fresh(
        cfg, paths(tmp_path), now=when or NOW - timedelta(hours=2), decks_url=decks_url,
        fetcher=lambda *_args, **_kwargs: expansions,
    )


def no_network(*_args, **_kwargs):
    pytest.fail("Catalog cache unexpectedly attempted network access")


def failed_fetch(*_args, **_kwargs):
    raise TimeoutError("isolated refresh failure")


def last_diagnostic(caplog):
    return [record for record in caplog.records if record.name == "ptcgp.catalog_refresh"][-1]


def test_absent_cache_fetches_and_persists_validated_catalog(tmp_path, caplog):
    calls = []

    def fetcher(session, *, decks_url):
        calls.append((session, decks_url))
        assert not cache_path(tmp_path).exists()
        return NEW

    with caplog.at_level(logging.INFO):
        result = refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=fetcher)

    assert result == NEW
    assert calls == [(None, "https://play.limitlesstcg.com/decks?format=standard&game=POCKET")]
    payload = json.loads(cache_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["context"]["game"] == "POCKET"
    assert payload["fetched_at"] == NOW.isoformat()
    assert payload["expansions"][0]["code"] == "B4a"
    diagnostic = last_diagnostic(caplog)
    assert diagnostic.catalog_refresh == "REFRESHED"
    assert diagnostic.network_refresh is True
    assert diagnostic.current_expansion == "B4a"


@pytest.mark.parametrize("age", [timedelta(), timedelta(minutes=59, seconds=59, microseconds=999999)])
def test_fresh_cache_uses_zero_network_and_keeps_bytes(tmp_path, monkeypatch, caplog, age):
    seed(tmp_path, when=NOW - age)
    previous = cache_path(tmp_path).read_bytes()
    monkeypatch.setattr(refresh, "make_session", no_network)
    with caplog.at_level(logging.INFO):
        result = refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=no_network)
    assert result == OLD
    assert cache_path(tmp_path).read_bytes() == previous
    assert last_diagnostic(caplog).catalog_refresh == "FRESH_CACHE"
    assert last_diagnostic(caplog).network_refresh is False


@pytest.mark.parametrize("age", [timedelta(minutes=60), timedelta(days=8)])
def test_stale_cache_refreshes_at_fixed_threshold_despite_old_ttl_config(tmp_path, age):
    seed(tmp_path, when=NOW - age)
    previous = cache_path(tmp_path).read_bytes()
    cfg = deepcopy(POCKET)
    cfg["scraping"].update({"force_refresh": False, "cache_ttl_min": 999999, "expansions_cache": {"ttl_days_fixed": 365, "jitter_frac": 0.99}})
    result = refresh.ensure_catalog_fresh(cfg, paths(tmp_path), now=NOW, fetcher=lambda *_args, **_kwargs: NEW)
    assert result == NEW
    assert cache_path(tmp_path).read_bytes() != previous
    assert json.loads(cache_path(tmp_path).read_text(encoding="utf-8"))["fetched_at"] == NOW.isoformat()


def test_force_refresh_cannot_bypass_fresh_cache(tmp_path):
    seed(tmp_path, when=NOW - timedelta(minutes=2))
    cfg = deepcopy(POCKET)
    cfg["scraping"].update({"force_refresh": True, "expansions_cache": {"ttl_days_fixed": 0}})
    assert refresh.ensure_catalog_fresh(cfg, paths(tmp_path), now=NOW, fetcher=no_network) == OLD


def test_fetch_error_preserves_lkg_bytes_and_structured_warning(tmp_path, caplog):
    seed(tmp_path)
    previous = cache_path(tmp_path).read_bytes()
    with caplog.at_level(logging.INFO):
        result = refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=failed_fetch)
    assert result == OLD
    assert cache_path(tmp_path).read_bytes() == previous
    diagnostic = last_diagnostic(caplog)
    assert diagnostic.levelno == logging.WARNING
    assert diagnostic.catalog_refresh == "FALLBACK_LKG"
    assert diagnostic.game == "POCKET"
    assert diagnostic.catalog_age_seconds == 7200
    assert diagnostic.last_successful_refresh_at == (NOW - timedelta(hours=2)).isoformat()
    assert diagnostic.refresh_error_class == "TimeoutError"


@pytest.mark.parametrize("malformed", [
    [],
    [Expansion("B4", "Clashing Currents", False)],
    [Expansion("B4", "Clashing Currents", True), Expansion("B4a", "New Expansion", True)],
    [Expansion("B4", "Clashing Currents", True), Expansion("B4", "Duplicate", False)],
    [Expansion("CRI", "TCG metadata", True)],
    [Expansion("../../B4", "Bad code", True)],
    [Expansion("B4", " ", True)],
    [Expansion("B4", "Control\nName", True)],
    [Expansion("B4", "Clashing Currents", "true")],
    [Expansion("B4", "Clashing Currents", True, "2026")],
    [SimpleNamespace(code="B4", name="Untyped row", is_current=True)],
])
def test_malformed_fetch_never_destroys_lkg(tmp_path, caplog, malformed):
    seed(tmp_path)
    previous = cache_path(tmp_path).read_bytes()
    result = refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=lambda *_args, **_kwargs: malformed)
    assert result == OLD
    assert cache_path(tmp_path).read_bytes() == previous
    assert last_diagnostic(caplog).refresh_error_class == "CatalogValidationError"


def test_absent_cache_and_fetch_error_fail_closed(tmp_path, caplog):
    with pytest.raises(refresh.CatalogRefreshError, match="refresh failed.*TimeoutError") as error:
        refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=failed_fetch)
    assert isinstance(error.value.__cause__, TimeoutError)
    assert not cache_path(tmp_path).exists()
    assert last_diagnostic(caplog).catalog_refresh == "FAIL_CLOSED"
    assert last_diagnostic(caplog).levelno == logging.ERROR


def test_absent_cache_and_malformed_fetch_fail_closed_without_fabrication(tmp_path):
    with pytest.raises(refresh.CatalogRefreshError, match="CatalogValidationError"):
        refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=lambda *_args, **_kwargs: [Expansion("B4", "Name", False)])
    assert not cache_path(tmp_path).exists()


def test_atomic_replace_occurs_only_after_complete_validated_temp_write(tmp_path, monkeypatch):
    seed(tmp_path)
    target = cache_path(tmp_path)
    previous = target.read_bytes()
    original_replace = refresh.os.replace
    observed = []

    def inspect_replace(source, destination):
        source = Path(source)
        assert source.parent == target.parent
        assert source != target
        assert target.read_bytes() == previous
        payload = json.loads(source.read_text(encoding="utf-8"))
        assert payload["expansions"][0]["code"] == "B4a"
        observed.append(source)
        original_replace(source, destination)

    monkeypatch.setattr(refresh.os, "replace", inspect_replace)
    assert refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=lambda *_args, **_kwargs: NEW) == NEW
    assert len(observed) == 1
    assert not observed[0].exists()
    assert not list(target.parent.glob("*.tmp"))


def test_atomic_replace_failure_preserves_old_cache_and_cleans_only_own_temp(tmp_path, monkeypatch, caplog):
    seed(tmp_path)
    target = cache_path(tmp_path)
    previous = target.read_bytes()
    unrelated = target.parent / "another-process.tmp"
    unrelated.write_text("leave intact", encoding="utf-8")

    def fail_replace(*_args):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(refresh.os, "replace", fail_replace)
    assert refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=lambda *_args, **_kwargs: NEW) == OLD
    assert target.read_bytes() == previous
    assert list(target.parent.glob("*.tmp")) == [unrelated]
    assert last_diagnostic(caplog).refresh_error_class == "PermissionError"


def test_corrupt_cache_is_not_lkg_and_is_preserved_when_refresh_fails(tmp_path):
    target = cache_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"{broken JSON")
    with pytest.raises(refresh.CatalogRefreshError):
        refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=failed_fetch)
    assert target.read_bytes() == b"{broken JSON"


@pytest.mark.parametrize("field,value", [
    ("fetched_at", "2026-09-15T14:00:00+00:00"),
    ("fetched_at", "2026-09-14T12:00:00"),
    ("fetched_at", "not-a-time"),
    ("schema_version", 999),
    ("context", {"game": "PTCG"}),
])
def test_invalid_envelope_is_not_lkg(tmp_path, field, value):
    seed(tmp_path)
    target = cache_path(tmp_path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload[field] = value
    target.write_text(json.dumps(payload), encoding="utf-8")
    previous = target.read_bytes()
    with pytest.raises(refresh.CatalogRefreshError):
        refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=failed_fetch)
    assert target.read_bytes() == previous


def test_pocket_and_tcg_cache_identity_and_payload_cannot_cross_contaminate(tmp_path):
    seed(tmp_path)
    tcg = deepcopy(POCKET)
    tcg["source"]["game"] = "PTCG"
    tcg_rows = [Expansion("CRI", "Crimson Invasion", True, "2026")]
    assert cache_path(tmp_path, tcg) != cache_path(tmp_path)
    assert refresh.ensure_catalog_fresh(tcg, paths(tmp_path), now=NOW, fetcher=lambda *_args, **_kwargs: tcg_rows) == tcg_rows
    assert json.loads(cache_path(tmp_path).read_text(encoding="utf-8"))["expansions"][0]["code"] == "B4"
    # Renaming or copying a Pocket envelope into the TCG filename is rejected.
    cache_path(tmp_path, tcg).write_bytes(cache_path(tmp_path).read_bytes())
    with pytest.raises(refresh.CatalogRefreshError):
        refresh.ensure_catalog_fresh(tcg, paths(tmp_path), now=NOW, fetcher=failed_fetch)


def test_format_rotation_and_endpoint_are_distinct_contexts(tmp_path):
    tcg = {"source": {"game": "PTCG"}}
    urls = [
        "https://play.limitlesstcg.com/decks?game=PTCG&format=standard&rotation=2026",
        "https://play.limitlesstcg.com/decks?game=PTCG&format=standard&rotation=2025",
        "https://play.limitlesstcg.com/decks?game=PTCG&format=expanded",
        "https://mirror.example/decks?game=PTCG&format=standard&rotation=2026",
    ]
    assert len({cache_path(tmp_path, tcg, decks_url=url) for url in urls}) == len(urls)
    permuted = "https://play.limitlesstcg.com/decks?rotation=2026&format=standard&set=CRI&game=PTCG"
    assert cache_path(tmp_path, tcg, decks_url=permuted) == cache_path(tmp_path, tcg, decks_url=urls[0])


@pytest.mark.parametrize("expansion", [Expansion("CRI", "TCG", True), Expansion("CRI", "TCG", True, "X"), Expansion("CRI", "TCG", True, "2025")])
def test_tcg_standard_rotation_must_be_valid_and_match_explicit_context(tmp_path, expansion):
    cfg = {"source": {"game": "PTCG"}, "scraping": {"decks_url": "https://play.limitlesstcg.com/decks?rotation=2026"}}
    with pytest.raises(refresh.CatalogRefreshError):
        refresh.ensure_catalog_fresh(cfg, paths(tmp_path), now=NOW, fetcher=lambda *_args, **_kwargs: [expansion])


@pytest.mark.parametrize("mode", ["offline", "replay", "frozen", "frozen-snapshot", "test", "fixture", "test_fixture"])
def test_non_live_modes_use_stale_frozen_cache_without_network_or_writes(tmp_path, monkeypatch, caplog, mode):
    seed(tmp_path, when=NOW - timedelta(days=50))
    previous = cache_path(tmp_path).read_bytes()
    monkeypatch.setattr(refresh, "make_session", no_network)
    with caplog.at_level(logging.INFO):
        result = refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, execution_mode=mode, fetcher=no_network)
    assert result == OLD
    assert cache_path(tmp_path).read_bytes() == previous
    assert last_diagnostic(caplog).catalog_refresh == "FROZEN_CACHE"
    assert last_diagnostic(caplog).network_refresh is False


@pytest.mark.parametrize("mode", ["offline", "replay", "frozen", "test"])
def test_non_live_without_valid_snapshot_fails_closed_with_zero_network(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(refresh, "make_session", no_network)
    with pytest.raises(refresh.CatalogRefreshError, match="network refresh is disabled"):
        refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, execution_mode=mode, fetcher=no_network)
    assert not paths(tmp_path).cache.exists()


def test_scoped_legacy_cache_remains_usable_then_migrates_after_valid_refresh(tmp_path):
    legacy = paths(tmp_path).cache / "expansions_pocket_standard.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"fetched_at": (NOW - timedelta(minutes=30)).replace(tzinfo=None).isoformat(), "burst_until": None, "expansions": [asdict(expansion) for expansion in OLD]}), encoding="utf-8")
    legacy_bytes = legacy.read_bytes()
    assert refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=no_network) == OLD
    assert not cache_path(tmp_path).exists()
    assert refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW + timedelta(hours=1), fetcher=lambda *_args, **_kwargs: NEW) == NEW
    assert cache_path(tmp_path).exists()
    assert legacy.read_bytes() == legacy_bytes


@pytest.mark.parametrize("legacy_name,expansions", [
    ("expansions_pocket.json", OLD),
    ("expansions_ptcg_standard.json", OLD),
    ("expansions_pocket_standard.json", [Expansion("CRI", "Wrong game", True, "2026")]),
    ("expansions_pocket_expanded.json", OLD),
])
def test_unscoped_cross_game_or_cross_format_legacy_cache_is_never_lkg(tmp_path, legacy_name, expansions):
    legacy = paths(tmp_path).cache / legacy_name
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"fetched_at": NOW.replace(tzinfo=None).isoformat(), "expansions": [asdict(expansion) for expansion in expansions]}), encoding="utf-8")
    with pytest.raises(refresh.CatalogRefreshError):
        refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, fetcher=failed_fetch)


def test_explicit_rotation_never_adopts_legacy_rotation_ambiguous_cache(tmp_path):
    legacy = paths(tmp_path).cache / "expansions_ptcg_standard.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"fetched_at": NOW.replace(tzinfo=None).isoformat(), "expansions": [asdict(Expansion("CRI", "TCG", True, "2026"))]}), encoding="utf-8")
    cfg = {"source": {"game": "PTCG"}, "scraping": {"decks_url": "https://play.limitlesstcg.com/decks?rotation=2026"}}
    with pytest.raises(refresh.CatalogRefreshError):
        refresh.ensure_catalog_fresh(cfg, paths(tmp_path), now=NOW, fetcher=failed_fetch)


def test_real_shared_http_parser_and_owned_session_lifecycle(tmp_path, monkeypatch):
    class Session:
        closed = False
        calls = []

        def get(self, url, timeout):
            self.calls.append((url, timeout))
            return SimpleNamespace(raise_for_status=lambda: None, text='<select id="set"><option value="B4a" selected>New Expansion</option><option value="B4">Clashing Currents</option></select>')

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(refresh, "make_session", lambda **_kwargs: session)
    assert refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW) == NEW
    assert len(session.calls) == 1
    assert session.closed


def test_caller_owned_session_is_not_closed(tmp_path):
    session = SimpleNamespace(close=no_network)
    assert refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, session=session, fetcher=lambda actual_session, **_kwargs: NEW if actual_session is session else []) == NEW


def test_own_session_is_closed_on_fetch_failure(tmp_path, monkeypatch):
    session = SimpleNamespace(get=failed_fetch, close=lambda: setattr(session, "closed", True), closed=False)
    monkeypatch.setattr(refresh, "make_session", lambda **_kwargs: session)
    with pytest.raises(refresh.CatalogRefreshError):
        refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW)
    assert session.closed


@pytest.mark.parametrize("bad_option", [
    '<option value="B4a">Conflicting duplicate</option>',
    '<option value="NOT A CODE">Malformed row</option>',
])
def test_malformed_http_selector_preserves_lkg_without_changing_public_parser(tmp_path, bad_option):
    from sources.limitless.pages.sets import parse_expansions_from_html

    seed(tmp_path)
    before = cache_path(tmp_path).read_bytes()
    html = '<select id="set"><option value="B4a" selected>New Expansion</option>' + bad_option + '</select>'
    session = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(text=html, raise_for_status=lambda: None))

    assert refresh.ensure_catalog_fresh(POCKET, paths(tmp_path), now=NOW, session=session) == OLD
    assert cache_path(tmp_path).read_bytes() == before
    # Opt-in runtime strictness leaves the independent exporter defaults intact.
    assert parse_expansions_from_html(html) == [Expansion("B4a", "New Expansion", True)]
