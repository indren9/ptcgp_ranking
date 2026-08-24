from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from domain.expansions import Expansion
from pipelines import deck_ranking as production
from pipelines.limitless_api_acquisition import run_limitless_api_acquisition


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "data" / "reference" / "pocket_releases.json"
STARTED = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 22, 12, 5, tzinfo=UTC)


def _write_config(tmp_path: Path, *, acquisition: str | None, api: dict | None = None) -> Path:
    source = {
        "provider": "limitless",
        "game": "POCKET",
        "format": {"mode": "code", "code": "standard"},
    }
    if acquisition is not None:
        source["acquisition"] = acquisition
    if api is not None:
        source["tournament_api"] = api
    cfg = {
        "source": source,
        "scraping": {
            "decks_url": "https://play.limitlesstcg.com/decks?game=POCKET&format=standard",
            "set": {"mode": "code", "code": "B3b"},
        },
        "top_meta": {"threshold_pct": 100.0},
        "analysis": {"candidate_pool": {"share_pct": 100.0}, "wildcard_pass": {"enabled": False}},
        "alias": {"apply": True, "file": "config/alias_map.json"},
        "nan_filter": {"mode": "fixed", "max_nan_ratio": 1.0, "min_nan_allowed": 1, "use_ceil": False},
        "saving": {"output_profile": "debug", "include_time_when_changed": False, "filename_prefix_with_set": False},
        "paths": {"output_dir": str(tmp_path / "outputs")},
        "logging": {"level": "INFO"},
    }
    path = tmp_path / f"{acquisition or 'missing'}_config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("selector", [None, "legacy_html"])
def test_legacy_missing_or_explicit_selector_uses_legacy_path(monkeypatch, tmp_path, selector):
    cfg_path = _write_config(tmp_path, acquisition=selector)
    exp = Expansion(code="B3b", name="Everyday Wonders", is_current=False)
    source_url = "https://play.limitlesstcg.com/decks?game=POCKET&format=standard&set=B3b"
    top = pd.DataFrame([{"Deck": "A", "Share_%": 50.0}, {"Deck": "B", "Share_%": 50.0}])
    sparse = pd.DataFrame(
        [
            {"Deck A": "A", "Deck B": "B", "W": 2, "L": 1, "T": 0, "N": 3, "Winrate": 66.67},
            {"Deck A": "B", "Deck B": "A", "W": 1, "L": 2, "T": 0, "N": 3, "Winrate": 33.33},
        ]
    )
    seen = {}

    monkeypatch.setattr(
        production,
        "resolve_expansion_and_url_from_config",
        lambda *args, **kwargs: (exp, source_url, []),
    )
    monkeypatch.setattr(production, "build_decks_url_for_expansion", lambda *args, **kwargs: source_url)
    monkeypatch.setattr(
        production,
        "_scrape_decklists_and_matchups",
        lambda **kwargs: ({"top_meta_decklist": top, "matchup_raw": sparse}, {}, {}, exp),
    )
    monkeypatch.setattr(
        production,
        "run_limitless_api_acquisition",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("API path must not run")),
    )

    def fake_core(**kwargs):
        seen.update(kwargs)
        return {}, {}, {}

    monkeypatch.setattr(production, "_build_core_matrices", fake_core)
    result = production.run_deck_ranking(
        base_dir=REPO_ROOT,
        config_path=cfg_path,
        run_scrape=True,
        run_core=True,
        configure_logs=False,
    )

    assert production._acquisition_source(result.cfg) == "legacy_html"
    assert seen["df_matchup_raw"] is sparse
    assert seen["preserve_zero_evidence"] is False
    assert seen["alias_index_override"] is None


def test_invalid_acquisition_source_fails_explicitly(tmp_path):
    cfg_path = _write_config(tmp_path, acquisition="not-a-source")
    with pytest.raises(ValueError, match="source.acquisition"):
        production.run_deck_ranking(
            base_dir=REPO_ROOT,
            config_path=cfg_path,
            run_scrape=False,
            configure_logs=False,
        )


class FakeClient:
    rate_limit_observations = ()

    def __init__(self):
        self.discovery = [
            {"id": "t1", "game": "POCKET", "format": "STANDARD", "name": "T1", "date": "2026-07-05T12:00:00Z", "players": 2},
            {"id": "t2", "game": "POCKET", "format": "STANDARD", "name": "T2", "date": "2026-07-15T12:00:00Z", "players": 2},
        ]

    @staticmethod
    def _details(tid: str, date: str):
        return {
            "id": tid,
            "game": "POCKET",
            "format": "STANDARD",
            "name": tid,
            "date": date,
            "players": 2,
            "organizer": {"id": 7, "name": "Fixture Org"},
            "platform": "PTCGP",
            "decklists": True,
            "isPublic": True,
            "isOnline": True,
            "phases": [{"phase": 1, "type": "SWISS", "rounds": 1, "mode": "BO1"}],
            "bannedCards": [],
            "specialRules": [],
        }

    @staticmethod
    def _standings(a, b):
        return [
            {"player": "p1", "placing": 1, "record": {"wins": 1, "losses": 0, "ties": 0}, "decklist": {"cards": []}, "deck": {"id": a[0], "name": a[1]}, "drop": None},
            {"player": "p2", "placing": 2, "record": {"wins": 0, "losses": 1, "ties": 0}, "decklist": {"cards": []}, "deck": {"id": b[0], "name": b[1]}, "drop": None},
        ]

    def list_tournaments(self, **kwargs):
        return list(self.discovery)

    def get_tournament_details(self, tid, **kwargs):
        date = "2026-07-05T12:00:00Z" if tid == "t1" else "2026-07-15T12:00:00Z"
        return self._details(tid, date)

    def get_tournament_standings(self, tid, **kwargs):
        if tid == "t1":
            return self._standings(("id-1", "Deck One"), ("id-2", "Twin Name"))
        return self._standings(("id-1", "Deck One"), ("id-3", "Twin Name"))

    def get_tournament_pairings(self, tid, **kwargs):
        return [{"phase": 1, "round": 1, "table": 1, "player1": "p1", "player2": "p2", "winner": "p1" if tid == "t1" else 0}]


def test_offline_api_replay_feeds_dense_id_keyed_production_core(tmp_path):
    raw_store = tmp_path / "raw_store"
    run_limitless_api_acquisition(
        game="POCKET",
        format="STANDARD",
        set_mode="code",
        set_code="B3b",
        acquisition_started_at=STARTED,
        execution_mode="live",
        raw_store_root=raw_store,
        release_catalog=CATALOG_PATH,
        client=FakeClient(),
        run_id="seed-live",
        software_git_revision="1b1305e",
        reuse_latest_raw=False,
        now_fn=lambda: NOW,
    )

    cfg_path = _write_config(
        tmp_path,
        acquisition="tournament_api",
        api={
            "execution_mode": "offline",
            "replay_run_id": "seed-live",
            "raw_store_root": str(raw_store),
            "cache_root": str(tmp_path / "cache"),
            "release_catalog": str(CATALOG_PATH),
        },
    )
    result = production.run_deck_ranking(
        base_dir=REPO_ROOT,
        config_path=cfg_path,
        run_scrape=True,
        run_core=True,
        run_mars=False,
        configure_logs=False,
    )

    assert result.diagnostics["acquisition_source"] == "tournament_api"
    assert result.diagnostics["tournament_api_execution_mode"] == "offline"
    assert result.diagnostics["tournament_api_network_calls"] == 0
    assert result.diagnostics["duplicate_display_names"] == {"Twin Name": ["id-2", "id-3"]}

    mapping = result.frames["deck_identity_map"]
    assert mapping.to_dict("records") == [
        {"Deck ID": "id-1", "Deck": "Deck One"},
        {"Deck ID": "id-2", "Deck": "Twin Name"},
        {"Deck ID": "id-3", "Deck": "Twin Name"},
    ]
    assert set(result.frames["top_meta_decklist"]["Deck"]) == {"id-1", "id-2", "id-3"}
    assert len(result.frames["matchup_raw"]) == 4
    assert len(result.frames["dense_score"]) == 6

    axis = list(result.frames["wr_matrix"].index)
    score = result.frames["score_flat"]
    assert len(score) == len(axis) * (len(axis) - 1)
    assert set(score["Deck A"]).issubset({"id-1", "id-2", "id-3"})
    zero = score[(score["Deck A"] == "id-2") & (score["Deck B"] == "id-3")].iloc[0]
    assert (int(zero["W"]), int(zero["L"]), int(zero["T"]), int(zero["N"])) == (0, 0, 0, 0)
    assert pd.isna(zero.WR_dir)
    assert float(result.frames["n_dir_matrix"].loc["id-2", "id-3"]) == 0.0
    assert pd.isna(result.frames["wr_matrix"].loc["id-2", "id-3"])

    manifest_path = result.outputs["run_manifest"]
    manifest_raw = manifest_path.read_text(encoding="utf-8")
    assert "player_id" not in manifest_raw.lower()
    payload = json.loads(manifest_raw)
    assert payload["diagnostics"]["deck_identity_count"] == 3
    assert payload["diagnostics"]["deck_identity_map"][1] == {"deck_id": "id-2", "deck_name": "Twin Name"}


def test_shadow_runs_same_set_into_disjoint_roots_without_overwrite(monkeypatch, tmp_path):
    exp = Expansion(code="B3b", name="Everyday Wonders", is_current=False)
    source_url = "https://play.limitlesstcg.com/decks?game=POCKET&format=standard&set=B3b"
    legacy_top = pd.DataFrame(
        [
            {"Deck": "Legacy A", "Share_%": 50.0},
            {"Deck": "Legacy B", "Share_%": 50.0},
        ]
    )
    legacy_sparse = pd.DataFrame(
        [
            {"Deck A": "Legacy A", "Deck B": "Legacy B", "W": 2, "L": 1, "T": 0, "N": 3, "Winrate": 66.67},
            {"Deck A": "Legacy B", "Deck B": "Legacy A", "W": 1, "L": 2, "T": 0, "N": 3, "Winrate": 33.33},
        ]
    )
    monkeypatch.setattr(
        production,
        "resolve_expansion_and_url_from_config",
        lambda *args, **kwargs: (exp, source_url, []),
    )
    monkeypatch.setattr(production, "build_decks_url_for_expansion", lambda *args, **kwargs: source_url)
    monkeypatch.setattr(
        production,
        "_scrape_decklists_and_matchups",
        lambda **kwargs: (
            {"top_meta_decklist": legacy_top, "matchup_raw": legacy_sparse},
            {},
            {},
            exp,
        ),
    )

    legacy_cfg = _write_config(tmp_path, acquisition="legacy_html")
    legacy_root = tmp_path / "shadow" / "legacy_html"
    legacy_result = production.run_deck_ranking(
        base_dir=REPO_ROOT,
        config_path=legacy_cfg,
        output_dir=legacy_root,
        run_scrape=True,
        run_core=True,
        run_mars=False,
        configure_logs=False,
    )
    legacy_score = legacy_result.outputs["score_flat"]
    legacy_score_before = legacy_score.read_bytes()

    raw_store = tmp_path / "raw_store_shadow"
    run_limitless_api_acquisition(
        game="POCKET",
        format="STANDARD",
        set_mode="code",
        set_code="B3b",
        acquisition_started_at=STARTED,
        execution_mode="live",
        raw_store_root=raw_store,
        release_catalog=CATALOG_PATH,
        client=FakeClient(),
        run_id="seed-shadow",
        software_git_revision="1b1305e",
        reuse_latest_raw=False,
        now_fn=lambda: NOW,
    )
    api_cfg = _write_config(
        tmp_path,
        acquisition="tournament_api",
        api={
            "execution_mode": "offline",
            "replay_run_id": "seed-shadow",
            "raw_store_root": str(raw_store),
            "cache_root": str(tmp_path / "cache_shadow"),
            "release_catalog": str(CATALOG_PATH),
        },
    )
    api_root = tmp_path / "shadow" / "tournament_api"
    api_result = production.run_deck_ranking(
        base_dir=REPO_ROOT,
        config_path=api_cfg,
        output_dir=api_root,
        run_scrape=True,
        run_core=True,
        run_mars=False,
        configure_logs=False,
    )

    assert legacy_result.expansion.code == api_result.expansion.code == "B3b"
    assert legacy_result.paths.output_root == legacy_root.resolve()
    assert api_result.paths.output_root == api_root.resolve()
    assert legacy_result.paths.outputs.parts[-2:] == ("POCKET", "standard")
    assert api_result.paths.outputs.parts[-2:] == ("POCKET", "standard")

    api_score = api_result.outputs["score_flat"]
    assert legacy_score != api_score
    assert legacy_score.is_relative_to(legacy_root.resolve())
    assert api_score.is_relative_to(api_root.resolve())
    assert legacy_score.read_bytes() == legacy_score_before

    common_keys = {"score_flat", "wr_matrix", "n_dir_matrix", "run_manifest"}
    legacy_paths = {legacy_result.outputs[key].resolve() for key in common_keys}
    api_paths = {api_result.outputs[key].resolve() for key in common_keys}
    assert legacy_paths.isdisjoint(api_paths)
    assert all(path.exists() for path in legacy_paths | api_paths)
    assert api_result.diagnostics["tournament_api_network_calls"] == 0
