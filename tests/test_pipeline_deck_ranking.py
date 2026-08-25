from pathlib import Path
from types import SimpleNamespace
import json
import logging

import pandas as pd

from domain.expansions import Expansion
from pipelines.deck_ranking import (
    EmptyDecklistError,
    InsufficientRankingDataError,
    _output_profile,
    _run_mars_stage,
    _should_save_artifact,
    _should_save_timestamped_csv,
    run_deck_ranking,
)


def write_config(base: Path) -> Path:
    cfg_dir = base / "config"
    cfg_dir.mkdir()
    config_path = cfg_dir / "pocket.yaml"
    config_path.write_text(
        """
source:
  provider: limitless
  game: POCKET
  acquisition: legacy_html
scraping:
  decks_url: https://example.com/decks?game=POCKET
  cache_ttl_min: 720
  force_refresh: false
  request_delay_sec: 0
  timeout_sec: 1
  selenium:
    headless: true
top_meta:
  threshold_pct: 80
saving:
  filename_prefix_with_set: false
""",
        encoding="utf-8",
    )
    return config_path


def test_run_deck_ranking_initializes_without_scrape(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=False)

    assert result.expansion.code == "B3a"
    assert result.decks_url.endswith("set=B3a")
    assert result.paths.outputs == tmp_path / "outputs" / "POCKET" / "standard"
    assert result.diagnostics["source_scope"] == ["POCKET", "standard"]
    assert result.diagnostics["output_profile"] == "debug"
    assert result.frames == {}
    assert result.outputs == {}
    assert repr(result) == "DeckRankingResult(set='B3a', frames=0, outputs=0, diagnostics=2)"


def test_run_deck_ranking_uses_manual_source_format_scope(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "source:\n  provider: limitless\n  game: POCKET\n",
        "source:\n  provider: limitless\n  game: POCKET\n  format:\n    mode: code\n    code: expanded\n",
    )
    config_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=expanded&set=B3a",
            [],
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=False)

    assert result.paths.outputs == tmp_path / "outputs" / "POCKET" / "expanded"
    assert result.diagnostics["source_scope"] == ["POCKET", "expanded"]


def test_run_deck_ranking_uses_configured_output_dir(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "saving:\n  filename_prefix_with_set: false\n",
        "saving:\n  filename_prefix_with_set: false\npaths:\n  output_dir: custom_outputs\n",
    )
    config_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=False)

    assert result.paths.output_root == (tmp_path / "custom_outputs").resolve()
    assert result.paths.outputs == (tmp_path / "custom_outputs" / "POCKET" / "standard").resolve()


def test_run_deck_ranking_output_dir_argument_overrides_config(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "saving:\n  filename_prefix_with_set: false\n",
        "saving:\n  filename_prefix_with_set: false\npaths:\n  output_dir: config_outputs\n",
    )
    config_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )

    result = run_deck_ranking(
        base_dir=tmp_path,
        config_path=config_path,
        output_dir="runtime_outputs",
        run_scrape=False,
    )

    assert result.paths.output_root == (tmp_path / "runtime_outputs").resolve()
    assert result.paths.outputs == (tmp_path / "runtime_outputs" / "POCKET" / "standard").resolve()


def test_output_profile_policy_keeps_user_outputs_small():
    user_cfg = {
        "saving": {"output_profile": "user", "include_time_when_changed": True},
        "analysis": {"wildcard_pass": {"enabled": True}},
    }
    reproducible_cfg = {"saving": {"output_profile": "reproducible"}}
    debug_cfg = {"saving": {"output_profile": "debug", "include_time_when_changed": True}}

    assert _output_profile({}) == "debug"

    assert _should_save_artifact(user_cfg, "mars_ranking", pd.DataFrame({"Deck": ["A"]}))
    assert _should_save_artifact(user_cfg, "wildcard_candidates", pd.DataFrame({"Deck": ["A"]}))
    assert not _should_save_artifact(user_cfg, "wildcard_candidates", pd.DataFrame())
    assert not _should_save_artifact(user_cfg, "decklist_raw", pd.DataFrame({"Deck": ["A"]}))
    assert not _should_save_artifact(user_cfg, "score_flat", pd.DataFrame({"Deck A": ["A"]}))
    assert not _should_save_timestamped_csv(user_cfg, "mars_ranking")

    assert _should_save_artifact(reproducible_cfg, "decklist_raw", pd.DataFrame({"Deck": ["A"]}))
    assert _should_save_artifact(reproducible_cfg, "matchup_raw", pd.DataFrame({"Deck A": ["A"]}))
    assert not _should_save_artifact(reproducible_cfg, "wr_matrix", pd.DataFrame({"A": [0.5]}))

    assert _should_save_artifact(debug_cfg, "wr_matrix", pd.DataFrame({"A": [0.5]}))
    assert _should_save_timestamped_csv(debug_cfg, "wr_matrix")


def test_run_deck_ranking_scrape_stage_writes_contract_outputs(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>60%</td><td>3</td></tr>
                <tr><td>2</td><td><a href="/deck/2">Mewtwo</a></td><td>40%</td><td>2</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {
                        "Deck A": "Pikachu",
                        "Deck B": "Mewtwo",
                        "W": 1,
                        "L": 1,
                        "T": 0,
                        "N": 2,
                        "Winrate": 50.0,
                    }
                ]
            ),
            1,
            0,
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)

    assert set(result.frames) == {
        "decklist_raw",
        "top_meta_decklist",
        "matchup_raw",
        "score_flat",
        "wr_matrix",
        "n_dir_matrix",
        "nan_diagnostics_pre_filter",
        "nan_filter_simulation",
        "wildcard_candidates",
    }
    assert result.diagnostics["decklist_cache_hit"] is True
    assert result.diagnostics["decklist_rows"] == 2
    assert result.diagnostics["top_meta_rows"] == 2
    assert result.diagnostics["matchup_url_count"] == 2
    assert result.diagnostics["matchup_pages"] == 1
    assert result.diagnostics["matchup_cache_hits"] == 0
    assert result.diagnostics["matchup_scrape_timing"] == {}
    assert result.diagnostics["axis0_count"] == 2
    assert result.diagnostics["axis_kept_count"] == 2
    assert result.diagnostics["nan_diagnostics_pre_filter"]["axis_count"] == 2
    assert "nan_ratio" in result.diagnostics["nan_diagnostics_pre_filter"]
    assert result.diagnostics["nan_filter"]["mode"] == "fixed"
    assert result.diagnostics["nan_filter"]["applied_max_nan_ratio"] == 0.15
    assert result.diagnostics["wildcard_full_scrape"] is False
    assert result.diagnostics["estimated_polite_delay_seconds"] == 0.0
    assert "nan_diagnostics_pre_filter" in result.frames
    assert result.outputs["decklist_raw"].name == "decklist_raw_latest.csv"
    assert "POCKET" in result.outputs["decklist_raw"].parts
    assert "standard" in result.outputs["decklist_raw"].parts
    assert result.outputs["top_meta_decklist"].exists()
    assert result.outputs["matchup_raw"].exists()
    assert result.outputs["score_flat"].exists()
    assert result.outputs["wr_matrix"].exists()
    assert result.outputs["n_dir_matrix"].exists()
    assert result.outputs["nan_diagnostics_pre_filter"].exists()
    assert result.outputs["wildcard_candidates"].exists()


def test_run_deck_ranking_infers_auto_set_from_tcg_decklist_html(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("game: POCKET", "game: PTCG")
    text = text.replace("https://example.com/decks?game=POCKET", "https://example.com/decks?game=PTCG")
    config_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code=None, name=None, is_current=True),
            f"{decks_url}&format=standard",
            [],
        ),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <select id="game"><option value="PTCG" selected>Pokémon TCG</option></select>
            <select id="format"><option value="standard" selected>Standard</option></select>
            <select id="set">
              <optgroup label="2026 (TEF-on)">
                <option data-set="CRI" data-rotation="2026" selected>Chaos Rising</option>
              </optgroup>
            </select>
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/decks/dragapult?format=standard&rotation=2026&set=CRI">Dragapult</a></td><td>60%</td><td>3</td></tr>
                <tr><td>2</td><td><a href="/decks/zoroark?format=standard&rotation=2026&set=CRI">N's Zoroark</a></td><td>40%</td><td>2</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"Deck A": "Dragapult", "Deck B": "N's Zoroark", "W": 1, "L": 1, "T": 0, "N": 2, "Winrate": 50.0}
                ]
            ),
            2,
            0,
            {},
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)

    assert result.expansion == Expansion(code="CRI", name="Chaos Rising", is_current=True, rotation="2026")
    assert result.decks_url.endswith("game=PTCG&format=standard&set=CRI&rotation=2026")
    assert result.diagnostics["set_resolution_source"] == "decklist-html"
    assert result.outputs["decklist_raw"].name == "decklist_raw_latest.csv"
    assert "CRI__Chaos_Rising" in result.outputs["decklist_raw"].parts
    assert "Dragapult" not in result.outputs["decklist_raw"].parts


def test_run_deck_ranking_falls_back_when_tcg_standard_rotation_has_empty_decklist(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("game: POCKET", "game: PTCG")
    text = text.replace("https://example.com/decks?game=POCKET", "https://example.com/decks?game=PTCG")
    text = text.replace(
        "source:\n  provider: limitless\n  game: PTCG\n",
        "source:\n  provider: limitless\n  game: PTCG\n  format:\n    mode: code\n    code: standard\n",
    )
    config_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="MEG", name="Mega Evolution", rotation="2025"),
            f"{decks_url}&format=standard&set=MEG&rotation=2025",
            [],
        ),
    )

    seen_urls = []

    def fake_scrape_decklist_html(url, *args, **kwargs):
        seen_urls.append(url)
        if "rotation=2026" in url:
            return (
                """
                <table>
                  <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
                  <tbody>
                    <tr><td>1</td><td><a href="/decks/gardevoir?format=standard&rotation=2026&set=MEG">Gardevoir</a></td><td>60%</td><td>3</td></tr>
                    <tr><td>2</td><td><a href="/decks/dragapult?format=standard&rotation=2026&set=MEG">Dragapult</a></td><td>40%</td><td>2</td></tr>
                  </tbody>
                </table>
                """,
                False,
            )
        return ("<html><body>No deck table</body></html>", False)

    monkeypatch.setattr("pipelines.deck_ranking.scrape_decklist_html", fake_scrape_decklist_html)

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"Deck A": "Gardevoir", "Deck B": "Dragapult", "W": 1, "L": 1, "T": 0, "N": 2, "Winrate": 50.0}
                ]
            ),
            2,
            0,
            {},
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)

    assert any("rotation=2025" in url for url in seen_urls)
    assert any("rotation=2026" in url for url in seen_urls)
    assert result.expansion == Expansion(code="MEG", name="Mega Evolution", is_current=False, rotation="2026")
    assert result.decks_url.endswith("game=PTCG&format=standard&set=MEG&rotation=2026")


def test_run_deck_ranking_raises_empty_decklist_error_when_all_fallbacks_are_empty(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("game: POCKET", "game: PTCG")
    text = text.replace("https://example.com/decks?game=POCKET", "https://example.com/decks?game=PTCG")
    text = text.replace(
        "source:\n  provider: limitless\n  game: PTCG\n",
        "source:\n  provider: limitless\n  game: PTCG\n  format:\n    mode: code\n    code: expanded\n",
    )
    config_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="MEG", name="Mega Evolution", rotation="2025"),
            f"{decks_url}&format=expanded&set=MEG&rotation=2025",
            [],
        ),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: ("<html><body>No deck table</body></html>", False),
    )

    try:
        run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)
    except EmptyDecklistError as exc:
        assert "Decklist vuota" in str(exc)
        assert any("rotation=2025" in url for url in exc.urls)
        assert any("format=expanded&set=MEG" in url and "rotation=" not in url for url in exc.urls)
        assert not any("rotation=2026" in url for url in exc.urls)
    else:
        raise AssertionError("Expected EmptyDecklistError")


def test_run_deck_ranking_dev_fast_scrape_env_disables_matchup_delay(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "request_delay_sec: 0",
            "request_delay_sec: 5\n  request_delay_jitter_frac: 0.25",
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PTCGP_I_KNOW_FAST_SCRAPE_IS_FOR_DEV_ONLY", "1")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>60%</td><td>3</td></tr>
                <tr><td>2</td><td><a href="/deck/2">Mewtwo</a></td><td>40%</td><td>2</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    captured = {}

    def fake_scrape_matchups(*args, **kwargs):
        captured["rate_limit_seconds"] = kwargs["rate_limit_seconds"]
        captured["rate_limit_jitter_frac"] = kwargs["rate_limit_jitter_frac"]
        return (
            pd.DataFrame(
                [
                    {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 1, "L": 1, "T": 0, "N": 2, "Winrate": 50.0}
                ]
            ),
            1,
            0,
            {},
        )

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr("pipelines.deck_ranking.scrape_matchups", fake_scrape_matchups)

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)

    assert captured == {"rate_limit_seconds": 0.0, "rate_limit_jitter_frac": 0.0}
    assert result.diagnostics["developer_fast_scrape"] is True


def test_run_deck_ranking_accepts_dynamic_nan_filter_null_min_axis(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + """
nan_filter:
  mode: dynamic
  max_nan_ratio: 0.15
  min_nan_allowed: 1
  use_ceil: false
  dynamic:
    min_nan_ratio: 0.15
    max_nan_ratio: 0.50
    step: 0.05
    target_share_pct: 80.0
    min_axis_count: null
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>60%</td><td>3</td></tr>
                <tr><td>2</td><td><a href="/deck/2">Mewtwo</a></td><td>40%</td><td>2</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 1, "L": 1, "T": 0, "N": 2, "Winrate": 50.0}
                ]
            ),
            1,
            0,
            {},
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)

    assert result.diagnostics["nan_filter"]["mode"] == "dynamic"
    assert result.diagnostics["nan_filter"]["min_axis_count"] is None
    assert result.frames["nan_filter_simulation"].empty is False


def test_candidate_pool_limits_core_axis_without_limiting_scrape(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace("threshold_pct: 80", "threshold_pct: null")
        + """
analysis:
  candidate_pool:
    share_pct: 80.0
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>60%</td><td>3</td></tr>
                <tr><td>2</td><td><a href="/deck/2">Mewtwo</a></td><td>25%</td><td>2</td></tr>
                <tr><td>3</td><td><a href="/deck/3">Charizard</a></td><td>15%</td><td>1</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 2, "L": 1, "T": 0, "N": 3, "Winrate": 66.7},
                    {"Deck A": "Pikachu", "Deck B": "Charizard", "W": 1, "L": 1, "T": 0, "N": 2, "Winrate": 50.0},
                    {"Deck A": "Mewtwo", "Deck B": "Charizard", "W": 1, "L": 2, "T": 0, "N": 3, "Winrate": 33.3},
                ]
            ),
            3,
            0,
            {},
        ),
    )

    result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)

    assert result.diagnostics["top_meta_rows"] == 3
    assert result.diagnostics["matchup_url_count"] == 3
    assert result.diagnostics["axis_all_count"] == 3
    assert result.diagnostics["axis0_count"] == 2
    assert result.diagnostics["candidate_pool"] == {
        "enabled": True,
        "share_pct": 80.0,
        "axis_all_count": 3,
        "axis_candidate_count": 2,
        "share_candidate_%": 85.0,
        "dropped_by_pool_count": 1,
    }
    assert result.frames["wr_matrix"].shape == (2, 2)
    assert set(result.frames["wr_matrix"].index) == {"Pikachu", "Mewtwo"}
    assert result.diagnostics["nan_diagnostics_pre_filter"]["axis_count"] == 3
    assert "wildcard_candidates" in result.frames
    assert result.outputs["wildcard_candidates"].exists()


def test_wildcard_pass_flags_excluded_decks_with_core_evidence(tmp_path, monkeypatch, caplog):
    config_path = write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace("request_delay_sec: 0", "request_delay_sec: 5")
        .replace("threshold_pct: 80", "threshold_pct: null")
        + """
analysis:
  candidate_pool:
    share_pct: 80.0
  wildcard_pass:
    enabled: true
    min_coverage_vs_core_pct: 50.0
    min_n_vs_core: 5
nan_filter:
  mode: fixed
  max_nan_ratio: 0.0
  min_nan_allowed: 0
  use_ceil: false
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (
            Expansion(code="B3a", name="Paradox Drive"),
            f"{decks_url}&format=standard&set=B3a",
            [],
        ),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>40%</td><td>4</td></tr>
                <tr><td>2</td><td><a href="/deck/2">Mewtwo</a></td><td>30%</td><td>3</td></tr>
                <tr><td>3</td><td><a href="/deck/3">Charizard</a></td><td>15%</td><td>2</td></tr>
                <tr><td>4</td><td><a href="/deck/4">Wild Card</a></td><td>5%</td><td>1</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 3, "L": 2, "T": 0, "N": 5, "Winrate": 60.0},
                    {"Deck A": "Pikachu", "Deck B": "Charizard", "W": 2, "L": 2, "T": 0, "N": 4, "Winrate": 50.0},
                    {"Deck A": "Mewtwo", "Deck B": "Charizard", "W": 2, "L": 3, "T": 0, "N": 5, "Winrate": 40.0},
                    {"Deck A": "Wild Card", "Deck B": "Pikachu", "W": 2, "L": 1, "T": 0, "N": 3, "Winrate": 66.7},
                    {"Deck A": "Wild Card", "Deck B": "Mewtwo", "W": 2, "L": 1, "T": 0, "N": 3, "Winrate": 66.7},
                ]
            ),
            4,
            0,
            {},
        ),
    )

    logger = logging.getLogger("ptcgp")
    logger.addHandler(caplog.handler)
    try:
        caplog.set_level(logging.WARNING, logger="ptcgp")
        result = run_deck_ranking(base_dir=tmp_path, config_path=config_path, run_scrape=True)
    finally:
        logger.removeHandler(caplog.handler)

    wildcards = result.frames["wildcard_candidates"]
    assert "Wild Card" in set(wildcards["Deck"])
    row = wildcards[wildcards["Deck"] == "Wild Card"].iloc[0]
    assert row["coverage_vs_core_%"] == 66.6667
    assert row["observed_core_opponents"] == 2
    assert row["core_opponents"] == 3
    assert row["N_vs_core"] == 6
    assert row["WR_vs_core_weighted_%"] == 66.6667
    assert result.diagnostics["wildcard_candidates"]["candidate_count"] == 1
    assert result.diagnostics["wildcard_full_scrape"] is True
    assert result.diagnostics["estimated_polite_delay_seconds"] == 20.0
    assert "wildcard_pass enabled with top_meta.threshold_pct=null" in caplog.text


def test_run_deck_ranking_mars_stage_saves_ranking(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (Expansion(code="B3a", name="Paradox Drive"), f"{decks_url}&set=B3a", []),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>60%</td><td>3</td></tr>
                <tr><td>2</td><td><a href="/deck/2">Mewtwo</a></td><td>40%</td><td>2</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 1, "L": 1, "T": 0, "N": 2, "Winrate": 50.0}
                ]
            ),
            1,
            0,
        ),
    )

    def fake_run_mars_core(filtered_wr, n_dir, score_flat, top_meta_df, cfg):
        ranking = pd.DataFrame(
            [
                {"Deck": "Pikachu", "Score_%": 55.0, "MAS_%": 55.0, "LB_%": 50.0, "BT_%": 60.0},
                {"Deck": "Mewtwo", "Score_%": 45.0, "MAS_%": 45.0, "LB_%": 40.0, "BT_%": 50.0},
            ]
        )
        ranking.index = pd.Index([1, 2], name="Rank")
        return ranking, {"AUTO_K": {"K_used": 1.0}}, pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr("pipelines.deck_ranking.run_mars_core", fake_run_mars_core)

    class FakeFig:
        pass

    monkeypatch.setattr(
        "pipelines.deck_ranking.show_wr_heatmap",
        lambda *args, **kwargs: (FakeFig(), None, pd.DataFrame([[None, 50.0], [50.0, None]], index=["Pikachu", "Mewtwo"], columns=["Pikachu", "Mewtwo"])),
    )

    def fake_save_plot_dual(fig, base_dir, prefix, tag, fmt="png", dpi=300, also_versioned=True):
        base_dir.mkdir(parents=True, exist_ok=True)
        versioned = base_dir / f"{prefix}_{tag}_fake.{fmt}" if also_versioned else None
        latest = base_dir / f"{prefix}_latest.{fmt}"
        if versioned is not None:
            versioned.write_text("plot", encoding="utf-8")
        latest.write_text("plot", encoding="utf-8")
        return versioned, latest

    monkeypatch.setattr("pipelines.deck_ranking.save_plot_dual", fake_save_plot_dual)

    def fake_write_mars_matchup_report(**kwargs):
        out_dir = kwargs["out_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        versioned = out_dir / "report_fake.xlsx"
        latest = out_dir / "report_latest.xlsx"
        versioned.write_text("report", encoding="utf-8")
        latest.write_text("report", encoding="utf-8")
        return versioned, latest, {"T": len(kwargs["filtered_wr"])}

    monkeypatch.setattr("pipelines.deck_ranking.write_mars_matchup_report", fake_write_mars_matchup_report)

    result = run_deck_ranking(
        base_dir=tmp_path,
        config_path=config_path,
        run_scrape=True,
        run_mars=True,
        run_heatmap=True,
        run_report=True,
    )

    assert result.frames["mars_ranking"].loc[1, "Deck"] == "Pikachu"
    assert result.outputs["mars_ranking"].name.startswith("mars_ranking_")
    assert result.outputs["mars_ranking"].suffix == ".csv"
    assert result.outputs["mars_ranking"].exists()
    assert result.outputs["heatmap_topN_latest"].name == "wr_heatmap_latest.png"
    assert result.outputs["heatmap_topN_latest"].exists()
    assert result.outputs["report_latest"].name == "report_latest.xlsx"
    assert result.outputs["report_latest"].exists()
    assert result.frames["heatmap_wr_sub"].shape == (2, 2)
    assert result.diagnostics["mars_rows"] == 2
    assert result.diagnostics["mars_diag"]["AUTO_K"]["K_used"] == 1.0
    assert result.diagnostics["heatmap_top_n"] == 2
    assert result.diagnostics["report_meta"]["T"] == 2
    assert result.diagnostics["report_k_used"] == 1.0


def test_user_output_profile_writes_latest_report_heatmap_and_manifest(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "saving:\n  filename_prefix_with_set: false\n",
        "saving:\n  output_profile: user\n  filename_prefix_with_set: false\n",
    )
    config_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "pipelines.deck_ranking.resolve_expansion_and_url_from_config",
        lambda cfg, paths, decks_url: (Expansion(code="B3a", name="Paradox Drive"), f"{decks_url}&set=B3a", []),
    )
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_decklist_html",
        lambda *args, **kwargs: (
            """
            <table>
              <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
              <tbody>
                <tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>60%</td><td>3</td></tr>
                <tr><td>2</td><td><a href="/deck/2">Mewtwo</a></td><td>40%</td><td>2</td></tr>
              </tbody>
            </table>
            """,
            True,
        ),
    )

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("pipelines.deck_ranking.make_session", lambda **kwargs: DummySession())
    monkeypatch.setattr(
        "pipelines.deck_ranking.scrape_matchups",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 1, "L": 1, "T": 0, "N": 2, "Winrate": 50.0}
                ]
            ),
            1,
            0,
        ),
    )

    def fake_run_mars_core(filtered_wr, n_dir, score_flat, top_meta_df, cfg):
        ranking = pd.DataFrame(
            [
                {"Deck": "Pikachu", "Score_%": 55.0, "MAS_%": 55.0, "LB_%": 50.0, "BT_%": 60.0},
                {"Deck": "Mewtwo", "Score_%": 45.0, "MAS_%": 45.0, "LB_%": 40.0, "BT_%": 50.0},
            ]
        )
        ranking.index = pd.Index([1, 2], name="Rank")
        return ranking, {"AUTO_K": {"K_used": 1.0}}, pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr("pipelines.deck_ranking.run_mars_core", fake_run_mars_core)
    monkeypatch.setattr(
        "pipelines.deck_ranking.show_wr_heatmap",
        lambda *args, **kwargs: (
            SimpleNamespace(),
            None,
            pd.DataFrame([[None, 50.0], [50.0, None]], index=["Pikachu", "Mewtwo"], columns=["Pikachu", "Mewtwo"]),
        ),
    )

    seen_plot_kwargs = {}

    def fake_save_plot_dual(fig, base_dir, prefix, tag, fmt="png", dpi=300, also_versioned=True):
        seen_plot_kwargs["also_versioned"] = also_versioned
        base_dir.mkdir(parents=True, exist_ok=True)
        latest = base_dir / f"{prefix}_latest.{fmt}"
        latest.write_text("plot", encoding="utf-8")
        return None, latest

    monkeypatch.setattr("pipelines.deck_ranking.save_plot_dual", fake_save_plot_dual)

    seen_report_kwargs = {}

    def fake_write_mars_matchup_report(**kwargs):
        seen_report_kwargs.update(kwargs)
        out_dir = kwargs["out_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        latest = out_dir / "mars_matchup_report_latest.xlsx"
        latest.write_text("report", encoding="utf-8")
        return None, latest, {"T": len(kwargs["filtered_wr"])}

    monkeypatch.setattr("pipelines.deck_ranking.write_mars_matchup_report", fake_write_mars_matchup_report)

    result = run_deck_ranking(
        base_dir=tmp_path,
        config_path=config_path,
        run_scrape=True,
        run_mars=True,
        run_heatmap=True,
        run_report=True,
    )

    assert result.diagnostics["output_profile"] == "user"
    assert seen_plot_kwargs["also_versioned"] is False
    assert seen_report_kwargs["also_versioned"] is False
    assert seen_report_kwargs["keep_legend_image"] is False
    assert "heatmap_topN" not in result.outputs
    assert result.outputs["heatmap_topN_latest"].name == "wr_heatmap_latest.png"
    assert "report" not in result.outputs
    assert result.outputs["report_latest"].name == "mars_matchup_report_latest.xlsx"
    assert result.outputs["run_manifest"].name == "run_manifest_latest.json"
    assert result.outputs["run_manifest"].exists()
    manifest = json.loads(result.outputs["run_manifest"].read_text(encoding="utf-8"))
    assert Path(manifest["outputs"]["run_manifest"]) == result.outputs["run_manifest"]
    assert "score_flat" not in result.outputs
    assert "wr_matrix" not in result.outputs
    assert "matchup_raw" not in result.outputs


def test_run_mars_stage_converts_missing_score_flat_to_insufficient_data(tmp_path, monkeypatch):
    paths = SimpleNamespace(base=tmp_path)
    cfg = {"mars": {}, "alias": {"apply": False}}
    wr = pd.DataFrame([[None, 50.0], [50.0, None]], index=["Pikachu", "Mewtwo"], columns=["Pikachu", "Mewtwo"])
    n_dir = pd.DataFrame([[None, 2], [2, None]], index=["Pikachu", "Mewtwo"], columns=["Pikachu", "Mewtwo"])
    top_meta = pd.DataFrame({"Deck": ["Pikachu", "Mewtwo"], "Share": ["60%", "40%"]})

    try:
        _run_mars_stage(
            cfg=cfg,
            paths=paths,
            exp=Expansion(code="B3a", name="Paradox Drive"),
            score_df=pd.DataFrame(),
            wr_matrix=wr,
            n_dir_matrix=n_dir,
            top_meta_df=top_meta,
        )
    except InsufficientRankingDataError as exc:
        assert "Missing post-filter score_flat" in str(exc)
    else:
        raise AssertionError("Expected InsufficientRankingDataError")
