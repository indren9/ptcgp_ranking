from datetime import UTC, datetime
from pathlib import Path

import yaml

from pipelines.deck_ranking import (
    ACQUISITION_TOURNAMENT_API,
    _acquisition_source,
    _api_catalog_context,
    _api_eligibility_from_config,
    _api_format_from_config,
    _api_resolved_scope_from_config,
    _api_set_params,
)
from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot,
    resolve_release,
)


CONFIG_PATH = Path("config/tcg.yaml")
CATALOG_PATH = Path("data/reference/ptcg_live_windows.json")
RUN_AT = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


def _cfg():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_tcg_production_uses_tournament_api_and_live_standard():
    cfg = _cfg()

    assert _acquisition_source(cfg) == ACQUISITION_TOURNAMENT_API
    assert _api_format_from_config(cfg) == "STANDARD"
    assert _api_set_params(cfg) == ("latest_completed", None)

    eligibility = _api_eligibility_from_config(cfg)

    assert eligibility is not None
    assert eligibility.policy_id == "ptcg_live_standard_v1"
    assert eligibility.game == "PTCG"
    assert eligibility.allowed_formats == ("STANDARD",)
    assert eligibility.require_public is True
    assert eligibility.require_decklists is True
    assert eligibility.require_online is True
    assert eligibility.allowed_platforms == ("PTCGL",)


def test_tcg_live_catalog_resolves_latest_completed_cri():
    catalog = load_release_catalog_snapshot(CATALOG_PATH)

    selected = resolve_release(
        catalog,
        mode="latest_completed",
        acquisition_started_at=RUN_AT,
    )

    assert selected.code == "CRI"
    assert selected.release_datetime == datetime(
        2026, 5, 21, tzinfo=UTC
    )
    assert selected.next_release_datetime == datetime(
        2026, 7, 16, tzinfo=UTC
    )


def test_tcg_production_scope_matches_frozen_t4_window():
    cfg = _cfg()

    scope = _api_resolved_scope_from_config(
        base=Path.cwd(),
        cfg=cfg,
        acquisition_started_at=RUN_AT,
    )

    assert scope.policy_id == "ptcg_tcgl_expansion_window_v1"
    assert scope.game == "PTCG"
    assert scope.format == "STANDARD"
    assert scope.set_code == "CRI"
    assert scope.set_name == "Chaos Rising Live window"
    assert scope.start_datetime == datetime(
        2026, 5, 21, tzinfo=UTC
    )
    assert scope.end_datetime == datetime(
        2026, 7, 16, tzinfo=UTC
    )
    assert (
        scope.catalog_version
        == "tcgl-expansion-boundaries-2026-09-04-v1"
    )


def test_tcg_production_operational_settings_are_frozen():
    cfg = _cfg()
    api = cfg["source"]["tournament_api"]

    assert api["cache_ttl_min"] == 0
    assert api["min_request_interval_seconds"] == 4.0
    assert api["discovery_page_size"] == 50
    assert api["discovery_max_pages"] == 100
    assert api["reuse_latest_raw"] is True


def test_catalog_context_uses_cri_for_latest_completed():
    cfg = _cfg()

    expansion, _, _, catalog_path = _api_catalog_context(
        base=Path.cwd(),
        cfg=cfg,
        acquisition_started_at=RUN_AT,
    )

    assert expansion.code == "CRI"
    assert catalog_path == CATALOG_PATH.resolve()
