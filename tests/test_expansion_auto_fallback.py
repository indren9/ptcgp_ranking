from pathlib import Path
from types import SimpleNamespace
import os

import pandas as pd
import pytest

from scraper.sets.models import Expansion
from sources.limitless.pages.sets import resolve_expansion_and_url_from_config
from utils.expansion_routing import ExpansionRef, resolve_auto_from_outputs, write_csv_versioned_setaware


def forbid_browser_fallback(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Runtime set resolution must use the validated central catalog")

    monkeypatch.setattr("sources.limitless.pages.sets.chrome", forbidden)
    monkeypatch.setattr("sources.limitless.pages.sets.read_current_expansion_from_selenium", forbidden)


def test_resolve_auto_from_outputs_prefers_latest_output_dir(tmp_path: Path):
    old = tmp_path / "A1__Genetic_Apex"
    latest = tmp_path / "B3a__Paradox_Drive"
    old.mkdir()
    latest.mkdir()
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(latest, (1_700_000_100, 1_700_000_100))

    exp = resolve_auto_from_outputs(tmp_path)

    assert exp.code == "B3a"
    assert exp.name == "Paradox_Drive"


def test_resolve_expansion_and_url_rejects_empty_catalog_despite_existing_outputs(tmp_path: Path, monkeypatch):
    outputs = tmp_path / "outputs"
    (outputs / "B3a__Paradox_Drive").mkdir(parents=True)

    class DummySession:
        def close(self):
            return None

    forbid_browser_fallback(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", lambda *args, **kwargs: [])

    cfg = {"scraping": {"set": {"mode": "auto"}}}
    paths = SimpleNamespace(outputs=outputs, cache=tmp_path / "cache", logs=tmp_path / "logs")

    with pytest.raises(RuntimeError, match="exactly one validated current expansion"):
        resolve_expansion_and_url_from_config(cfg, paths, decks_url="https://example.com")


@pytest.mark.parametrize("set_mode", ["none", "format"])
def test_resolve_expansion_format_mode_keeps_url_without_set(tmp_path: Path, monkeypatch, set_mode):
    def forbidden(*args, **kwargs):
        pytest.fail("No set catalog or network session is needed in format-only mode")

    monkeypatch.setattr("sources.limitless.pages.sets.make_session", forbidden)
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", forbidden)

    cfg = {
        "source": {"game": "PTCG", "format": {"mode": "code", "code": "2016"}},
        "scraping": {"set": {"mode": set_mode, "code": ""}},
    }
    paths = SimpleNamespace(outputs=tmp_path / "outputs", cache=tmp_path / "cache", logs=tmp_path / "logs")

    exp, url, catalog = resolve_expansion_and_url_from_config(cfg, paths, decks_url="https://example.com/decks")

    assert exp.code is None
    assert "game=PTCG" in url
    assert "format=2016" in url
    assert "set=" not in url
    assert catalog == []


def test_auto_mode_uses_central_policy_once_without_manual_refresh(monkeypatch):
    class DummySession:
        def close(self):
            return None

    seen = []

    def fake_fetch_catalog(*args, **kwargs):
        seen.append(kwargs)
        return [SimpleNamespace(code="B3a", name="Paradox Drive", is_current=True)]

    forbid_browser_fallback(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)

    cfg = {"scraping": {"set": {"mode": "auto"}}}
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    exp, url, catalog = resolve_expansion_and_url_from_config(cfg, paths, decks_url="https://example.com")

    assert len(seen) == 1
    assert "ttl_override" not in seen[0]
    assert seen[0]["execution_mode"] == "live"
    assert exp.code == "B3a"
    assert url.endswith("set=B3a")
    assert catalog[0].code == "B3a"


def test_resolve_expansion_uses_manual_format_for_catalog_url(monkeypatch):
    class DummySession:
        def close(self):
            return None

    seen = {}

    def fake_fetch_catalog(*args, **kwargs):
        seen["decks_url"] = kwargs.get("decks_url")
        return []

    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)

    cfg = {
        "source": {"game": "POCKET", "format": {"mode": "code", "code": "expanded"}},
        "scraping": {"set": {"mode": "code", "code": "B3a"}},
    }
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    exp, url, catalog = resolve_expansion_and_url_from_config(
        cfg,
        paths,
        require_in_catalog=False,
        decks_url="https://example.com/decks?game=POCKET&format=standard",
    )

    assert exp.code == "B3a"
    assert "format=expanded" in seen["decks_url"]
    assert "format=expanded" in url
    assert catalog == []


def test_resolve_expansion_preserves_tcg_game_and_rotation_for_catalog_url(monkeypatch):
    class DummySession:
        def close(self):
            return None

    seen = {}

    def fake_fetch_catalog(*args, **kwargs):
        seen["decks_url"] = kwargs.get("decks_url")
        return []

    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)

    cfg = {
        "source": {"game": "PTCG", "format": {"mode": "code", "code": "standard"}},
        "scraping": {"set": {"mode": "code", "code": "CRI"}},
    }
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    exp, url, catalog = resolve_expansion_and_url_from_config(
        cfg,
        paths,
        require_in_catalog=False,
        decks_url="https://play.limitlesstcg.com/decks?game=PTCG&set=CRI&format=standard&rotation=2026",
    )

    assert exp.code == "CRI"
    assert "game=PTCG" in seen["decks_url"]
    assert "rotation=2026" in seen["decks_url"]
    assert "game=PTCG" in url
    assert "rotation=2026" in url
    assert "set=CRI" in url
    assert catalog == []


def test_resolve_expansion_uses_catalog_rotation_for_manual_tcg_set(monkeypatch):
    class DummySession:
        def close(self):
            return None

    def fake_fetch_catalog(*args, **kwargs):
        return [Expansion(code="ASC", name="Ascended Heroes", rotation="2025")]

    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)

    cfg = {
        "source": {"game": "PTCG", "format": {"mode": "code", "code": "standard"}},
        "scraping": {"set": {"mode": "code", "code": "ASC"}},
    }
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    exp, url, catalog = resolve_expansion_and_url_from_config(
        cfg,
        paths,
        decks_url="https://play.limitlesstcg.com/decks?game=PTCG&format=standard&rotation=2026",
    )

    assert exp == Expansion(code="ASC", name="Ascended Heroes", is_current=False, rotation="2025")
    assert "set=ASC" in url
    assert "rotation=2025" in url
    assert catalog[0].rotation == "2025"


def test_auto_mode_uses_minimal_tcg_url_for_central_catalog(monkeypatch):
    class DummySession:
        def close(self):
            return None

    seen = {}
    current_exp = Expansion(code="CRI", name="Chaos Rising", is_current=True, rotation="2026")

    def fake_fetch_catalog(*args, **kwargs):
        seen.setdefault("catalog_urls", []).append(kwargs.get("decks_url"))
        return [current_exp]

    forbid_browser_fallback(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)

    cfg = {
        "source": {"game": "PTCG", "format": {"mode": "auto", "code": ""}},
        "scraping": {"set": {"mode": "auto"}, "selenium": {"headless": True, "wait_sec": 1}},
    }
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    exp, url, catalog = resolve_expansion_and_url_from_config(
        cfg,
        paths,
        decks_url="https://play.limitlesstcg.com/decks?game=PTCG",
    )

    assert exp.code == "CRI"
    assert exp.name == "Chaos Rising"
    assert len(seen["catalog_urls"]) == 1
    assert all("game=PTCG" in catalog_url for catalog_url in seen["catalog_urls"])
    assert "game=POCKET" not in seen["catalog_urls"][0]
    assert "format=standard" in seen["catalog_urls"][0]
    assert "game=PTCG" in url
    assert "format=standard" in url
    assert "set=CRI" in url
    assert catalog == [current_exp]


def test_auto_mode_uses_validated_current_entry_without_second_discovery(monkeypatch):
    class DummySession:
        def close(self):
            return None

    def fake_fetch_catalog(*args, **kwargs):
        return [
            Expansion(code="A1", name="Genetic Apex", is_current=False),
            Expansion(code="B3a", name="Paradox Drive", is_current=True),
        ]

    forbid_browser_fallback(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)

    cfg = {"scraping": {"set": {"mode": "auto"}, "selenium": {"headless": True, "wait_sec": 1}}}
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    exp, url, catalog = resolve_expansion_and_url_from_config(cfg, paths, decks_url="https://example.com")

    assert exp.code == "B3a"
    assert exp.name == "Paradox Drive"
    assert url.endswith("set=B3a")
    assert catalog[0].code == "A1"


@pytest.mark.parametrize("current_flags", [(False, False), (True, True)])
def test_auto_mode_rejects_ambiguous_current_semantics(tmp_path, monkeypatch, current_flags):
    catalog = [
        Expansion(code="A1", name="Genetic Apex", is_current=current_flags[0]),
        Expansion(code="B3a", name="Paradox Drive", is_current=current_flags[1]),
    ]
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", lambda *args, **kwargs: catalog)
    forbid_browser_fallback(monkeypatch)
    paths = SimpleNamespace(outputs=tmp_path / "outputs", cache=tmp_path / "cache")

    with pytest.raises(RuntimeError, match="exactly one validated current expansion"):
        resolve_expansion_and_url_from_config({"scraping": {"set": {"mode": "auto"}}}, paths)


def test_offline_resolver_forwards_mode_without_creating_network_session(tmp_path, monkeypatch):
    seen = {}
    catalog = [Expansion(code="B3a", name="Paradox Drive", is_current=True)]

    def forbidden(**kwargs):
        pytest.fail("Offline resolution must not create a live HTTP session")

    def frozen_catalog(*args, **kwargs):
        seen.update(kwargs)
        return catalog

    monkeypatch.setattr("sources.limitless.pages.sets.make_session", forbidden)
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", frozen_catalog)
    forbid_browser_fallback(monkeypatch)
    paths = SimpleNamespace(outputs=tmp_path / "outputs", cache=tmp_path / "cache")

    exp, _, returned = resolve_expansion_and_url_from_config(
        {"scraping": {"set": {"mode": "auto"}}}, paths, execution_mode="offline"
    )

    assert exp.code == "B3a"
    assert returned == catalog
    assert seen["session"] is None
    assert seen["execution_mode"] == "offline"


def test_write_csv_uses_filename_prefix_with_set_key(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")
    df = pd.DataFrame({"Deck": ["Pikachu"], "Score": [100]})

    prefixed = write_csv_versioned_setaware(
        df,
        paths,
        "mars_ranking",
        exp,
        {"saving": {"filename_prefix_with_set": True}},
        changed=False,
    )
    assert prefixed.name == "B3a_mars_ranking_latest.csv"

    plain = write_csv_versioned_setaware(
        df,
        paths,
        "mars_ranking",
        exp,
        {"saving": {"filename_prefix_with_set": False}},
        changed=False,
    )
    assert plain.name == "mars_ranking_latest.csv"
