"""Execute the public notebook's normal cells without production side effects."""

import ast
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = REPO_ROOT / "notebooks" / "live_ranking.ipynb"


def _code_cells():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    return [
        (cell["id"], "".join(cell["source"]))
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    ]


@pytest.mark.parametrize("profile,game", [("pocket", "POCKET"), ("tcg", "PTCG")])
@pytest.mark.parametrize("working_dir", [REPO_ROOT, NOTEBOOK_PATH.parent])
def test_run_all_calls_shared_live_pipeline_without_refresh_flags(
    monkeypatch, profile, game, working_dir
):
    calls = []
    sentinel = object()
    pipeline_module = ModuleType("pipelines.deck_ranking")

    def production_pipeline(**kwargs):
        calls.append(kwargs)
        return sentinel

    pipeline_module.run_deck_ranking = production_pipeline
    monkeypatch.setitem(sys.modules, "pipelines.deck_ranking", pipeline_module)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(working_dir)
    namespace = {}

    for cell_id, code in _code_cells():
        tree = ast.parse(code, filename=f"{NOTEBOOK_PATH}:{cell_id}")
        identifiers = {
            node.id.upper() for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        assert not identifiers.intersection(
            {"FORCE_REFRESH", "REFRESH_CATALOG", "UPDATE_SETS"}
        )
        exec(compile(tree, str(NOTEBOOK_PATH), "exec"), namespace)
        if cell_id == "select-game":
            namespace["GAME_PROFILE"] = profile

    assert len(calls) == 1
    assert calls[0] == {
        "base_dir": REPO_ROOT,
        "config_path": REPO_ROOT / "config" / f"{profile}.yaml",
        "run_scrape": True,
        "run_core": True,
        "run_mars": True,
        "run_heatmap": True,
        "run_report": True,
        "show_progress": True,
    }
    assert namespace["result"] is sentinel
    config = yaml.safe_load(calls[0]["config_path"].read_text(encoding="utf-8"))
    assert config["source"]["game"] == game
    assert config["source"]["acquisition"] == "tournament_api"
    if game == "PTCG":
        assert config["source"]["tournament_api"]["window"]["mode"] == "latest_completed"
        assert config["source"]["tournament_api"]["release_catalog"] == (
            "data/reference/ptcg_live_windows.json"
        )


def test_run_all_rejects_unknown_game_before_pipeline_import(monkeypatch):
    namespace = {"GAME_PROFILE": "unknown"}
    setup = dict(_code_cells())["load-production-pipeline"]
    with pytest.raises(ValueError, match="GAME_PROFILE"):
        exec(compile(setup, str(NOTEBOOK_PATH), "exec"), namespace)
