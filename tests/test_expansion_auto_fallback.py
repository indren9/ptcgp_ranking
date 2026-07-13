from pathlib import Path
from types import SimpleNamespace
import logging
import os
from contextlib import contextmanager

import pandas as pd

from scraper.sets.models import Expansion
from sources.limitless.pages.sets import resolve_expansion_and_url_from_config
from utils.expansion_routing import ExpansionRef, resolve_auto_from_outputs, write_csv_versioned_setaware


def stub_chrome(monkeypatch):
    @contextmanager
    def fake_chrome(*, headless=True, detach=False):
        yield SimpleNamespace()

    monkeypatch.setattr("sources.limitless.pages.sets.chrome", fake_chrome)


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


def test_resolve_expansion_and_url_falls_back_to_latest_outputs_when_catalog_is_empty(tmp_path: Path, monkeypatch):
    outputs = tmp_path / "outputs"
    (outputs / "B3a__Paradox_Drive").mkdir(parents=True)

    class DummySession:
        def close(self):
            return None

    stub_chrome(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", lambda *args, **kwargs: [])
    monkeypatch.setattr("sources.limitless.pages.sets.read_current_expansion_from_selenium", lambda *args, **kwargs: None)

    cfg = {"scraping": {"set": {"mode": "auto"}}}
    paths = SimpleNamespace(outputs=outputs, cache=tmp_path / "cache", logs=tmp_path / "logs")

    exp, url, catalog = resolve_expansion_and_url_from_config(cfg, paths, decks_url="https://example.com")

    assert exp.code == "B3a"
    assert exp.name == "Paradox_Drive"
    assert url.endswith("set=B3a")
    assert catalog == []


def test_auto_mode_forces_fresh_catalog_lookup(monkeypatch):
    class DummySession:
        def close(self):
            return None

    seen = {}

    def fake_fetch_catalog(*args, **kwargs):
        seen["ttl_override"] = kwargs.get("ttl_override")
        return [SimpleNamespace(code="B3a", name="Paradox Drive", is_current=True)]

    stub_chrome(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)
    monkeypatch.setattr("sources.limitless.pages.sets.read_current_expansion_from_selenium", lambda *args, **kwargs: None)

    cfg = {"scraping": {"set": {"mode": "auto"}}}
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    exp, url, catalog = resolve_expansion_and_url_from_config(cfg, paths, decks_url="https://example.com")

    assert seen["ttl_override"] == 0
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


def test_auto_mode_uses_minimal_tcg_url_for_live_site_lookup(monkeypatch):
    class DummySession:
        def close(self):
            return None

    seen = {}
    live_exp = Expansion(code="CRI", name="CRI - Celestial Guardians", is_current=True)

    def fake_fetch_catalog(*args, **kwargs):
        seen.setdefault("catalog_urls", []).append(kwargs.get("decks_url"))
        return []

    def fake_read_live(*args, **kwargs):
        seen["live_url"] = kwargs.get("decks_url")
        return live_exp

    stub_chrome(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)
    monkeypatch.setattr("sources.limitless.pages.sets.read_current_expansion_from_selenium", fake_read_live)

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
    assert exp.name == "Celestial Guardians"
    assert seen["catalog_urls"]
    assert all("game=PTCG" in catalog_url for catalog_url in seen["catalog_urls"])
    assert "game=POCKET" not in seen["live_url"]
    assert "format=standard" in seen["live_url"]
    assert "game=PTCG" in url
    assert "format=standard" in url
    assert "set=CRI" in url
    assert catalog == []


def test_auto_mode_prefers_live_site_over_catalog(monkeypatch, caplog):
    class DummySession:
        def close(self):
            return None

    live_exp = Expansion(code="B3a", name="B3a - Paradox Drive", is_current=True)

    def fake_fetch_catalog(*args, **kwargs):
        return [SimpleNamespace(code="A1", name="A1 - Genetic Apex", is_current=True)]

    stub_chrome(monkeypatch)
    monkeypatch.setattr("sources.limitless.pages.sets.make_session", lambda timeout=20: DummySession())
    monkeypatch.setattr("sources.limitless.pages.sets.fetch_catalog_with_policy", fake_fetch_catalog)
    monkeypatch.setattr("sources.limitless.pages.sets.read_current_expansion_from_selenium", lambda *args, **kwargs: live_exp)

    cfg = {"scraping": {"set": {"mode": "auto"}, "selenium": {"headless": True, "wait_sec": 1}}}
    paths = SimpleNamespace(outputs=Path("outputs"), cache=Path("cache/requests"), logs=Path("logs"))

    caplog.set_level(logging.DEBUG, logger="ptcgp.sets")
    exp, url, catalog = resolve_expansion_and_url_from_config(cfg, paths, decks_url="https://example.com")

    assert exp.code == "B3a"
    assert exp.name == "Paradox Drive"
    assert url.endswith("set=B3a")
    assert catalog[0].code == "A1"
    assert "[SET AUTO] source=live-site" in caplog.text


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
