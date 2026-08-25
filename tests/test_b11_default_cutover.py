from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from domain.expansions import Expansion
from pipelines import deck_ranking as production


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("source_cfg", "expected"),
    [
        ({"game": "POCKET"}, "tournament_api"),
        (
            {"game": "POCKET", "acquisition": "tournament_api"},
            "tournament_api",
        ),
        (
            {"game": "POCKET", "acquisition": "legacy_html"},
            "legacy_html",
        ),
        ({"game": "TCG"}, "legacy_html"),
    ],
)
def test_acquisition_resolution_is_pocket_scoped(source_cfg, expected):
    assert production._acquisition_source({"source": source_cfg}) == expected


def test_pocket_yaml_default_is_tournament_api():
    cfg = yaml.safe_load(
        (REPO_ROOT / "config" / "pocket.yaml").read_text(encoding="utf-8")
    )
    assert cfg["source"]["game"] == "POCKET"
    assert cfg["source"]["acquisition"] == "tournament_api"


def test_invalid_source_fails_explicitly():
    cfg = {
        "source": {
            "game": "POCKET",
            "acquisition": "invalid-source",
        }
    }

    with pytest.raises(ValueError, match="source.acquisition"):
        production._acquisition_source(cfg)


def test_api_failure_has_no_silent_legacy_fallback(monkeypatch, tmp_path):
    cfg = {
        "source": {
            "provider": "limitless",
            "game": "POCKET",
            "acquisition": "tournament_api",
            "format": {"mode": "code", "code": "standard"},
        },
        "scraping": {
            "set": {"mode": "code", "code": "B3b"},
        },
        "saving": {
            "output_profile": "debug",
            "include_time_when_changed": False,
            "filename_prefix_with_set": False,
        },
        "paths": {
            "output_dir": str(tmp_path / "outputs"),
        },
        "logging": {
            "level": "INFO",
        },
    }

    cfg_path = tmp_path / "api_failure.yaml"
    cfg_path.write_text(
        yaml.safe_dump(cfg, sort_keys=False),
        encoding="utf-8",
    )

    exp = Expansion(
        code="B3b",
        name="Everyday Wonders",
        is_current=False,
    )

    source_url = (
        "https://play.limitlesstcg.com/tournaments"
        "?game=POCKET&format=STANDARD&set=B3b"
    )

    monkeypatch.setattr(
        production,
        "_api_catalog_context",
        lambda **kwargs: (
            exp,
            source_url,
            [],
            REPO_ROOT / "data" / "reference" / "pocket_releases.json",
        ),
    )

    def fail_api(**kwargs):
        raise RuntimeError("synthetic API failure")

    monkeypatch.setattr(
        production,
        "_run_tournament_api_acquisition_for_production",
        fail_api,
    )

    def legacy_must_not_run(*args, **kwargs):
        raise AssertionError("legacy fallback must not run")

    monkeypatch.setattr(
        production,
        "resolve_expansion_and_url_from_config",
        legacy_must_not_run,
    )
    monkeypatch.setattr(
        production,
        "_scrape_decklists_and_matchups",
        legacy_must_not_run,
    )

    with pytest.raises(RuntimeError, match="synthetic API failure"):
        production.run_deck_ranking(
            base_dir=REPO_ROOT,
            config_path=cfg_path,
            run_scrape=True,
            run_core=False,
            configure_logs=False,
        )
