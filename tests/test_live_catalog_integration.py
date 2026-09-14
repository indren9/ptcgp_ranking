from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from domain.expansions import Expansion
from pipelines import deck_ranking as production
from scripts import tcg_live_latest_completed_job as tcg_job
from storage.paths import init_paths


ROOT = Path(__file__).resolve().parents[1]
POCKET_RELEASES = ROOT / "data/reference/pocket_releases.json"
TCG_RELEASES = ROOT / "data/reference/ptcg_live_windows.json"
STARTED = datetime(2026, 9, 14, 12, tzinfo=UTC)


def _pocket_config(*, mode: str = "auto", code: str = "") -> dict:
    return {
        "source": {
            "provider": "limitless",
            "game": "POCKET",
            "acquisition": "tournament_api",
            "format": {"mode": "code", "code": "standard"},
            "tournament_api": {"release_catalog": str(POCKET_RELEASES)},
        },
        "scraping": {
            "decks_url": "https://play.limitlesstcg.com/decks?game=POCKET&format=standard",
            "set": {"mode": mode, "code": code},
        },
        "publication": {"onedrive": {"enabled": False}},
    }


def _current(code: str = "B4a") -> list[Expansion]:
    return [Expansion(code=code, name="Team Rocket's Ambition", is_current=True)]


def _forbid_refresh(*args, **kwargs):
    raise AssertionError("this path must not refresh the live expansion catalog")


def _prepare(tmp_path, cfg, *, live=True, started=STARTED):
    return production._prepare_live_catalog_context(
        base=tmp_path,
        cfg=cfg,
        paths=init_paths(tmp_path, cfg),
        acquisition_started_at=started,
        live=live,
    )


def test_pocket_auto_freezes_refreshed_selection_for_context_and_scope(monkeypatch, tmp_path):
    cfg = _pocket_config()
    original = deepcopy(cfg)
    original_catalog = POCKET_RELEASES.read_bytes()
    calls = []

    def refresh(*args, **kwargs):
        calls.append((args, kwargs))
        return _current()

    monkeypatch.setattr(production, "ensure_catalog_fresh", refresh)
    prepared = _prepare(tmp_path, cfg)
    expansion, url, _, catalog_path = production._api_catalog_context(
        base=tmp_path, cfg=prepared, acquisition_started_at=STARTED
    )
    scope = production._api_resolved_scope_from_config(
        base=tmp_path, cfg=prepared, acquisition_started_at=STARTED
    )

    assert len(calls) == 1
    assert production._api_set_params(prepared) == ("code", "B4a")
    assert expansion.code == scope.set_code == "B4a"
    assert "set=B4a" in url
    assert catalog_path == POCKET_RELEASES.resolve()
    assert scope.start_datetime == datetime(2026, 8, 27, 1, tzinfo=UTC)
    assert scope.end_datetime == STARTED
    assert cfg == original
    assert POCKET_RELEASES.read_bytes() == original_catalog


def test_unknown_refreshed_current_fails_instead_of_using_stale_canonical_auto(monkeypatch, tmp_path):
    cfg = _pocket_config()
    original = deepcopy(cfg)
    monkeypatch.setattr(production, "ensure_catalog_fresh", lambda *a, **k: _current("B5"))

    with pytest.raises(ValueError, match="(?i)(canonical|release|window|catalog)"):
        _prepare(tmp_path, cfg)

    assert cfg == original


def test_refreshed_future_release_cannot_define_a_live_window(monkeypatch, tmp_path):
    cfg = _pocket_config()
    monkeypatch.setattr(production, "ensure_catalog_fresh", lambda *a, **k: _current())

    with pytest.raises(ValueError, match="(?i)(release|window|future|catalog)"):
        _prepare(tmp_path, cfg, started=datetime(2026, 8, 26, 12, tzinfo=UTC))


@pytest.mark.parametrize("api_window", [False, True])
def test_explicit_code_keeps_selection_without_catalog_discovery(monkeypatch, tmp_path, api_window):
    cfg = _pocket_config(mode="code", code="B3b")
    if api_window:
        cfg["source"]["tournament_api"]["window"] = {"mode": "code", "code": "B4"}
    original = deepcopy(cfg)
    monkeypatch.setattr(production, "ensure_catalog_fresh", _forbid_refresh)

    prepared = _prepare(tmp_path, cfg)
    expansion, _, _, _ = production._api_catalog_context(
        base=tmp_path, cfg=prepared, acquisition_started_at=STARTED
    )

    assert expansion.code == ("B4" if api_window else "B3b")
    assert prepared == original
    assert cfg == original


@pytest.mark.parametrize("execution_mode", ["offline", "replay", "frozen", "fixture"])
def test_nonlive_modes_do_not_consult_todays_catalog(monkeypatch, tmp_path, execution_mode):
    cfg = _pocket_config()
    cfg["source"]["tournament_api"]["execution_mode"] = execution_mode
    original = deepcopy(cfg)
    monkeypatch.setattr(production, "ensure_catalog_fresh", _forbid_refresh)

    assert _prepare(tmp_path, cfg) == original
    assert cfg == original


def test_analysis_without_acquisition_does_not_refresh_catalog(monkeypatch, tmp_path):
    cfg = _pocket_config()
    monkeypatch.setattr(production, "ensure_catalog_fresh", _forbid_refresh)

    assert _prepare(tmp_path, cfg, live=False) == cfg


def test_production_auto_preflight_completes_before_acquisition(monkeypatch, tmp_path):
    cfg = _pocket_config()
    config_path = tmp_path / "pocket.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    config_bytes = config_path.read_bytes()
    events = []

    class AcquiredThroughPinnedContext(Exception):
        pass

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return STARTED if tz else STARTED.replace(tzinfo=None)

    def refresh(*args, **kwargs):
        events.append("catalog")
        return _current()

    def acquire(**kwargs):
        events.append("acquisition")
        assert production._api_set_params(kwargs["cfg"]) == ("code", "B4a")
        assert kwargs["acquisition_started_at"] == STARTED
        raise AcquiredThroughPinnedContext

    monkeypatch.setattr(production, "datetime", FixedDateTime)
    monkeypatch.setattr(production, "ensure_catalog_fresh", refresh)
    monkeypatch.setattr(production, "_run_tournament_api_acquisition_for_production", acquire)

    with pytest.raises(AcquiredThroughPinnedContext):
        production.run_deck_ranking(
            base_dir=tmp_path,
            config_path=config_path,
            run_scrape=True,
            run_core=False,
            run_mars=False,
            configure_logs=False,
        )

    assert events == ["catalog", "acquisition"]
    assert config_path.read_bytes() == config_bytes


def test_tcg_latest_completed_needs_no_generic_catalog(monkeypatch, tmp_path):
    cfg = yaml.safe_load((ROOT / "config/tcg.yaml").read_text(encoding="utf-8"))
    original = deepcopy(cfg)
    original_catalog = TCG_RELEASES.read_bytes()
    monkeypatch.setattr(production, "ensure_catalog_fresh", _forbid_refresh)
    prepared = production._prepare_live_catalog_context(
        base=ROOT,
        cfg=cfg,
        paths=init_paths(tmp_path, cfg),
        acquisition_started_at=STARTED,
    )
    scope = production._api_resolved_scope_from_config(
        base=ROOT, cfg=prepared, acquisition_started_at=STARTED
    )

    assert prepared == original
    assert production._api_set_params(prepared) == ("latest_completed", None)
    assert scope.set_code == "CRI"
    assert scope.start_datetime == datetime(2026, 5, 21, tzinfo=UTC)
    assert scope.end_datetime == datetime(2026, 7, 16, tzinfo=UTC)
    assert TCG_RELEASES.read_bytes() == original_catalog


def test_real_tcg_job_noop_has_no_catalog_or_backend_dependency(monkeypatch, tmp_path):
    original_catalog = TCG_RELEASES.read_bytes()
    monkeypatch.setattr(production, "ensure_catalog_fresh", _forbid_refresh)
    monkeypatch.setattr(tcg_job, "run_limitless_api_acquisition", _forbid_refresh)
    report = tcg_job.run_job(
        repo_root=ROOT,
        work_root=tmp_path,
        acquisition_started_at=STARTED,
        backend_factory=_forbid_refresh,
    )

    assert report["action"] == "noop"
    assert report["network_calls"] == 0
    assert report["published"] is False
    assert TCG_RELEASES.read_bytes() == original_catalog
