from pathlib import Path
import logging
import yaml

from domain.expansions import Expansion
from pipelines.deck_ranking import EmptyDecklistError, InsufficientRankingDataError
from sources.limitless.pages.sets import FormatSetsCatalogEntry
from scripts.run_pocket_all_sets import (
    BatchRunResult,
    _format_codes,
    _summarize_outcomes,
    apply_format_code,
    apply_output_dir,
    config_for_format_only,
    config_for_set,
    main,
    resolve_format_codes,
    run_all_sets,
    select_expansions,
)


def test_config_for_set_preserves_game_and_forces_code_mode_without_mutating_original():
    cfg = {"source": {"game": "PTCG"}, "scraping": {"set": {"mode": "auto", "code": ""}}}

    out = config_for_set(cfg, "B3b")

    assert out["source"]["game"] == "PTCG"
    assert out["scraping"]["set"] == {"mode": "code", "code": "B3b"}
    assert cfg["source"]["game"] == "PTCG"
    assert cfg["scraping"]["set"]["mode"] == "auto"
    assert "paths" not in cfg


def test_apply_output_dir_overrides_paths_without_mutating_original():
    cfg = {"paths": {"output_dir": "outputs"}}

    out = apply_output_dir(cfg, Path("custom_outputs"))

    assert out["paths"]["output_dir"] == "custom_outputs"
    assert cfg["paths"]["output_dir"] == "outputs"


def test_apply_format_code_forces_manual_source_format_without_mutating_original():
    cfg = {"source": {"game": "PTCG", "format": {"mode": "auto", "code": ""}}}

    out = apply_format_code(cfg, "expanded")

    assert out["source"]["format"] == {"mode": "code", "code": "expanded"}
    assert cfg["source"]["format"] == {"mode": "auto", "code": ""}


def test_apply_format_code_can_preserve_official_format_name():
    cfg = {"source": {"game": "PTCG"}}

    out = apply_format_code(cfg, "2016", format_name="Worlds 2016 (XY-STS)")

    assert out["source"]["format"] == {
        "mode": "code",
        "code": "2016",
        "name": "Worlds 2016 (XY-STS)",
    }


def test_config_for_format_only_disables_set_resolution_without_mutating_original():
    cfg = {"source": {"game": "PTCG"}, "scraping": {"set": {"mode": "auto", "code": "CRI"}}}

    out = config_for_format_only(cfg)

    assert out["scraping"]["set"] == {"mode": "format", "code": ""}
    assert cfg["scraping"]["set"] == {"mode": "auto", "code": "CRI"}


def test_format_codes_prefers_multi_format_list():
    assert _format_codes(format_code=None, formats=None) == [None]
    assert _format_codes(format_code="expanded", formats=None) == ["expanded"]
    assert _format_codes(format_code=None, formats="standard,expanded") == ["standard", "expanded"]


def test_summarize_outcomes_can_omit_global_details(caplog):
    outcomes = [
        BatchRunResult(expansion=Expansion(code="A1", name="Genetic Apex"), ok=True),
        BatchRunResult(expansion=Expansion(code="B3", name="Pulsing Aura"), ok=True, skipped=True, error="empty"),
    ]

    logger = logging.getLogger("ptcgp")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger="ptcgp"):
            failed = _summarize_outcomes(outcomes, selected_count=2, label="all-formats", include_details=False)
    finally:
        logger.removeHandler(caplog.handler)

    assert failed is False
    assert "[BATCH DONE all-formats] ok=1 skipped=1 failed=0 selected=2" in caplog.text
    assert "[BATCH SKIP]" not in caplog.text


def test_resolve_format_codes_all_uses_format_catalog(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("scripts.run_pocket_all_sets.discover_formats", lambda base_dir, cfg, refresh=False: ["standard", "expanded"])

    assert resolve_format_codes(tmp_path, {}, format_code=None, formats="all", refresh=True) == ["standard", "expanded"]


def test_resolve_format_codes_all_keeps_historical_formats(monkeypatch, tmp_path: Path):
    catalog = ["standard", "expanded", "2016", "glc"]
    monkeypatch.setattr(
        "scripts.run_pocket_all_sets.discover_formats",
        lambda base_dir, cfg, refresh=False: catalog,
    )

    assert resolve_format_codes(tmp_path, {}, format_code=None, formats="all", refresh=True) == catalog


def test_select_expansions_supports_order_filters_start_and_limit():
    catalog = [
        Expansion(code="B3b", name="Everyday Wonders"),
        Expansion(code="B3a", name="Paradox Drive"),
        Expansion(code="A1", name="Genetic Apex"),
    ]

    selected = select_expansions(catalog, only=["A1", "B3a", "B3b"], start_at="B3a", limit=2)

    assert [exp.code for exp in selected] == ["B3a", "A1"]
    assert [exp.code for exp in select_expansions(catalog, oldest_first=True)] == ["A1", "B3a", "B3b"]


def test_run_all_sets_writes_temp_configs_and_calls_pipeline(monkeypatch, tmp_path: Path):
    calls = []

    class DummyResult:
        outputs = {}
        frames = {}

    def fake_run_deck_ranking(**kwargs):
        temp_cfg = yaml.safe_load(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
        calls.append({**kwargs, "temp_cfg": temp_cfg})
        return DummyResult()

    monkeypatch.setattr("scripts.run_pocket_all_sets.run_deck_ranking", fake_run_deck_ranking)

    outcomes = run_all_sets(
        base_dir=tmp_path,
        cfg={"source": {"game": "POCKET"}, "scraping": {"set": {"mode": "auto"}}},
        expansions=[Expansion(code="A1", name="Genetic Apex"), Expansion(code="B3a", name="Paradox Drive")],
        heatmap_top_n=12,
        run_scrape=True,
        run_core=None,
        run_mars=True,
        run_heatmap=False,
        run_report=True,
        show_progress=True,
        output_dir=tmp_path / "batch_outputs",
    )

    assert [item.ok for item in outcomes] == [True, True]
    assert [Path(call["config_path"]).name for call in calls] == ["config_pocket_A1.yaml", "config_pocket_B3a.yaml"]
    assert all(call["base_dir"] == tmp_path for call in calls)
    assert all(call["output_dir"] == tmp_path / "batch_outputs" for call in calls)
    assert all(call["heatmap_top_n"] == 12 for call in calls)
    assert all(call["run_heatmap"] is False for call in calls)
    assert all("paths" not in call["temp_cfg"] for call in calls)


def test_run_all_sets_preserves_tcg_game_in_temp_configs(monkeypatch, tmp_path: Path):
    calls = []

    class DummyResult:
        outputs = {}
        frames = {}

    def fake_run_deck_ranking(**kwargs):
        temp_cfg = yaml.safe_load(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
        calls.append({**kwargs, "temp_cfg": temp_cfg})
        return DummyResult()

    monkeypatch.setattr("scripts.run_pocket_all_sets.run_deck_ranking", fake_run_deck_ranking)

    outcomes = run_all_sets(
        base_dir=tmp_path,
        cfg={
            "source": {"game": "PTCG"},
            "scraping": {
                "decks_url": "https://play.limitlesstcg.com/decks?game=PTCG",
                "set": {"mode": "auto"},
            },
        },
        expansions=[Expansion(code="CRI", name="Chaos Rising")],
        heatmap_top_n=12,
        run_scrape=True,
        run_core=None,
        run_mars=True,
        run_heatmap=False,
        run_report=True,
        show_progress=True,
    )

    assert [item.ok for item in outcomes] == [True]
    assert Path(calls[0]["config_path"]).name == "config_ptcg_CRI.yaml"
    assert calls[0]["temp_cfg"]["source"]["game"] == "PTCG"
    assert calls[0]["temp_cfg"]["scraping"]["set"] == {"mode": "code", "code": "CRI"}


def test_run_all_sets_supports_format_only_jobs(monkeypatch, tmp_path: Path):
    calls = []

    class DummyResult:
        outputs = {}
        frames = {}

    def fake_run_deck_ranking(**kwargs):
        temp_cfg = yaml.safe_load(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
        calls.append({**kwargs, "temp_cfg": temp_cfg})
        return DummyResult()

    monkeypatch.setattr("scripts.run_pocket_all_sets.run_deck_ranking", fake_run_deck_ranking)

    outcomes = run_all_sets(
        base_dir=tmp_path,
        cfg={"source": {"game": "PTCG", "format": {"mode": "code", "code": "2016"}}},
        expansions=[Expansion(code=None, name="Worlds 2016 (XY-STS)")],
        heatmap_top_n=12,
        run_scrape=True,
        run_core=None,
        run_mars=True,
        run_heatmap=False,
        run_report=True,
        show_progress=True,
    )

    assert [item.ok for item in outcomes] == [True]
    assert Path(calls[0]["config_path"]).name == "config_ptcg_format.yaml"
    assert calls[0]["temp_cfg"]["scraping"]["set"] == {"mode": "format", "code": ""}


def test_run_all_sets_marks_empty_decklist_as_skipped_and_continues(monkeypatch, tmp_path: Path):
    calls = []

    class DummyResult:
        outputs = {}
        frames = {}

    def fake_run_deck_ranking(**kwargs):
        temp_cfg = yaml.safe_load(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
        code = temp_cfg["scraping"]["set"]["code"]
        calls.append(code)
        if code == "MEG":
            raise EmptyDecklistError("Decklist vuota", urls=["https://example.com/decks?set=MEG"])
        return DummyResult()

    monkeypatch.setattr("scripts.run_pocket_all_sets.run_deck_ranking", fake_run_deck_ranking)

    outcomes = run_all_sets(
        base_dir=tmp_path,
        cfg={"source": {"game": "PTCG"}, "scraping": {"decks_url": "https://example.com/decks?game=PTCG"}},
        expansions=[
            Expansion(code="PFL", name="Phantasmal Flames"),
            Expansion(code="MEG", name="Mega Evolution"),
            Expansion(code="DRI", name="Destined Rivals"),
        ],
        heatmap_top_n=12,
        run_scrape=True,
        run_core=None,
        run_mars=True,
        run_heatmap=False,
        run_report=True,
        show_progress=True,
    )

    assert calls == ["PFL", "MEG", "DRI"]
    assert [(item.expansion.code, item.ok, item.skipped) for item in outcomes] == [
        ("PFL", True, False),
        ("MEG", True, True),
        ("DRI", True, False),
    ]


def test_run_all_sets_marks_insufficient_ranking_data_as_skipped_and_continues(monkeypatch, tmp_path: Path):
    calls = []

    class DummyResult:
        outputs = {}
        frames = {}

    def fake_run_deck_ranking(**kwargs):
        temp_cfg = yaml.safe_load(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
        code = temp_cfg["scraping"]["set"]["code"]
        calls.append(code)
        if code == "SSP":
            raise InsufficientRankingDataError("Missing post-filter score_flat")
        return DummyResult()

    monkeypatch.setattr("scripts.run_pocket_all_sets.run_deck_ranking", fake_run_deck_ranking)

    outcomes = run_all_sets(
        base_dir=tmp_path,
        cfg={"source": {"game": "PTCG"}, "scraping": {"decks_url": "https://example.com/decks?game=PTCG"}},
        expansions=[
            Expansion(code="PRE", name="Prismatic Evolutions"),
            Expansion(code="SSP", name="Surging Sparks"),
            Expansion(code="SCR", name="Stellar Crown"),
        ],
        heatmap_top_n=12,
        run_scrape=True,
        run_core=None,
        run_mars=True,
        run_heatmap=False,
        run_report=True,
        show_progress=True,
    )

    assert calls == ["PRE", "SSP", "SCR"]
    assert [(item.expansion.code, item.ok, item.skipped) for item in outcomes] == [
        ("PRE", True, False),
        ("SSP", True, True),
        ("SCR", True, False),
    ]


def test_main_runs_each_requested_format(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "tcg.yaml"
    config_path.write_text(
        """
source:
  game: PTCG
scraping:
  decks_url: https://example.com/decks?game=PTCG
  set:
    mode: auto
paths:
  output_dir: outputs
""",
        encoding="utf-8",
    )

    formats_seen = []

    def fake_discover(base_dir, cfg, *, refresh=False):
        formats_seen.append(cfg["source"]["format"]["code"])
        return [Expansion(code="CRI", name="Chaos Rising")]

    def fake_run_all_sets(**kwargs):
        cfg = kwargs["cfg"]
        formats_seen.append(f"run:{cfg['source']['format']['code']}")
        return [BatchRunResult(expansion=Expansion(code="CRI", name="Chaos Rising"), ok=True)]

    monkeypatch.setattr("scripts.run_pocket_all_sets.discover_expansions", fake_discover)
    monkeypatch.setattr("scripts.run_pocket_all_sets.run_all_sets", fake_run_all_sets)

    code = main(
        [
            "--base-dir",
            str(tmp_path),
            "--config",
            str(config_path),
            "--formats",
            "standard,expanded",
            "--dry-run",
        ]
    )

    assert code == 0
    assert formats_seen == ["standard", "run:standard", "expanded", "run:expanded"]


def test_main_explicit_format_without_sets_runs_format_only(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "tcg.yaml"
    config_path.write_text(
        """
source:
  game: PTCG
scraping:
  decks_url: https://example.com/decks?game=PTCG
  set:
    mode: auto
paths:
  output_dir: outputs
""",
        encoding="utf-8",
    )

    runs = []

    def fake_run_all_sets(**kwargs):
        cfg = kwargs["cfg"]
        runs.append((cfg["source"]["format"]["code"], [exp.code for exp in kwargs["expansions"]]))
        return [BatchRunResult(expansion=exp, ok=True) for exp in kwargs["expansions"]]

    monkeypatch.setattr("scripts.run_pocket_all_sets.discover_expansions", lambda base_dir, cfg, refresh=False: [])
    monkeypatch.setattr("scripts.run_pocket_all_sets.run_all_sets", fake_run_all_sets)

    code = main(
        [
            "--base-dir",
            str(tmp_path),
            "--config",
            str(config_path),
            "--formats",
            "2016",
            "--dry-run",
        ]
    )

    assert code == 0
    assert runs == [("2016", [None])]


def test_main_formats_all_uses_format_set_catalog_for_format_only_jobs(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "tcg.yaml"
    config_path.write_text(
        """
source:
  game: PTCG
scraping:
  decks_url: https://example.com/decks?game=PTCG
  set:
    mode: auto
paths:
  output_dir: outputs
""",
        encoding="utf-8",
    )

    runs = []
    catalog = [
        FormatSetsCatalogEntry(
            code="standard",
            name="Standard",
            expansions=[Expansion(code="CRI", name="Chaos Rising")],
        ),
        FormatSetsCatalogEntry(code="2016", name="Worlds 2016 (XY-STS)", expansions=[]),
    ]

    def fake_run_all_sets(**kwargs):
        cfg = kwargs["cfg"]
        fmt = cfg["source"]["format"]
        runs.append((fmt["code"], fmt.get("name"), [exp.code for exp in kwargs["expansions"]]))
        return [BatchRunResult(expansion=exp, ok=True) for exp in kwargs["expansions"]]

    monkeypatch.setattr("scripts.run_pocket_all_sets.discover_format_set_catalog", lambda base_dir, cfg, refresh=False: catalog)
    monkeypatch.setattr("scripts.run_pocket_all_sets.run_all_sets", fake_run_all_sets)

    code = main(
        [
            "--base-dir",
            str(tmp_path),
            "--config",
            str(config_path),
            "--formats",
            "all",
            "--dry-run",
        ]
    )

    assert code == 0
    assert runs == [("standard", "Standard", ["CRI"]), ("2016", "Worlds 2016 (XY-STS)", [None])]
