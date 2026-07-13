from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from storage.paths import init_paths, output_root_from_config, output_scope_from_config, source_scope_from_config
from storage.routing import (
    ExpansionRef,
    dest_for_key,
    dir_for_key,
    find_latest,
    resolve_auto_from_outputs,
    write_csv_versioned_setaware,
)
from utils.expansion_routing import dest_for_key as legacy_dest_for_key
from utils.io import init_paths as legacy_init_paths


def test_storage_paths_create_project_roots(tmp_path: Path):
    paths = init_paths(tmp_path)

    assert paths.base == tmp_path
    assert paths.output_root.is_dir()
    assert paths.outputs.is_dir()
    assert paths.cache.is_dir()
    assert paths.logs.is_dir()


def test_storage_paths_support_game_and_url_derived_format_scope(tmp_path: Path):
    cfg = {
        "source": {"game": "POCKET"},
        "scraping": {"decks_url": "https://example.com/decks?game=POCKET&format=standard"},
    }

    paths = init_paths(tmp_path, cfg)

    assert output_scope_from_config(cfg) == ("POCKET", "standard")
    assert source_scope_from_config({"source": {"game": "POCKET"}}) == ("POCKET",)
    assert paths.output_root == tmp_path / "outputs"
    assert paths.outputs == tmp_path / "outputs" / "POCKET" / "standard"
    assert paths.outputs.is_dir()


def test_storage_paths_support_manual_format_code_scope(tmp_path: Path):
    cfg = {
        "source": {"game": "POCKET", "format": {"mode": "code", "code": "expanded"}},
        "scraping": {"decks_url": "https://example.com/decks?game=POCKET&format=standard"},
    }

    paths = init_paths(tmp_path, cfg)

    assert output_scope_from_config(cfg) == ("POCKET", "expanded")
    assert paths.outputs == tmp_path / "outputs" / "POCKET" / "expanded"


def test_storage_paths_support_relative_custom_output_root(tmp_path: Path):
    cfg = {
        "paths": {"output_dir": "external_outputs"},
        "source": {"game": "POCKET"},
        "scraping": {"decks_url": "https://example.com/decks?game=POCKET&format=standard"},
    }

    paths = init_paths(tmp_path, cfg)

    assert output_root_from_config(tmp_path, cfg) == (tmp_path / "external_outputs").resolve()
    assert paths.output_root == (tmp_path / "external_outputs").resolve()
    assert paths.outputs == (tmp_path / "external_outputs" / "POCKET" / "standard").resolve()
    assert paths.outputs.is_dir()


def test_storage_paths_support_absolute_custom_output_root(tmp_path: Path):
    output_root = tmp_path / "outside_repo_outputs"
    cfg = {
        "paths": {"output_dir": str(output_root)},
        "source": {"game": "POCKET"},
        "scraping": {"decks_url": "https://example.com/decks?game=POCKET&format=standard"},
    }

    paths = init_paths(tmp_path / "repo", cfg)

    assert paths.output_root == output_root.resolve()
    assert paths.outputs == (output_root / "POCKET" / "standard").resolve()
    assert paths.outputs.is_dir()


def test_storage_routes_under_scoped_game_format_outputs(tmp_path: Path):
    paths = init_paths(
        tmp_path,
        {"source": {"game": "pocket"}},
        source_url="https://example.com/decks?game=POCKET&format=standard&set=B3a",
    )
    exp = ExpansionRef(code="B3a", name="Paradox Drive")

    dest = dest_for_key(paths, "score_flat", exp)

    assert dest == (
        tmp_path
        / "outputs"
        / "POCKET"
        / "standard"
        / "B3a__Paradox_Drive"
        / "matchups"
        / "scores"
    ).resolve()


def test_resolve_auto_from_outputs_finds_nested_game_format_expansion(tmp_path: Path):
    nested = tmp_path / "outputs" / "POCKET" / "standard" / "B3a__Paradox_Drive"
    nested.mkdir(parents=True)

    exp = resolve_auto_from_outputs(tmp_path / "outputs")

    assert exp == ExpansionRef(code="B3a", name="Paradox_Drive")


def test_storage_routes_new_and_legacy_keys_to_same_directory(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")

    new_dest = dest_for_key(paths, "score_flat", exp)
    legacy_dest = dest_for_key(paths, "matchup_score_table", exp)

    assert new_dest == legacy_dest
    assert new_dest == (tmp_path / "outputs" / "B3a__Paradox_Drive" / "matchups" / "scores").resolve()


def test_storage_routes_nan_diagnostics_to_diagnostics_folder(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")

    dest = dest_for_key(paths, "nan_diagnostics_pre_filter", exp)
    sim_dest = dest_for_key(paths, "nan_filter_simulation", exp)

    assert dest == (tmp_path / "outputs" / "B3a__Paradox_Drive" / "diagnostics" / "nan_filter").resolve()
    assert sim_dest == dest


def test_storage_strips_set_code_prefix_from_expansion_folder(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3b", name="B3b - Everyday Wonders")

    dest = dest_for_key(paths, "mars_ranking", exp)

    assert dest == (tmp_path / "outputs" / "B3b__Everyday_Wonders" / "rankings" / "mars").resolve()


def test_storage_find_latest_prefers_prefixed_contract(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")
    out_dir = dest_for_key(paths, "mars_ranking", exp)
    prefixed = out_dir / "B3a_mars_ranking_latest.csv"
    plain = out_dir / "mars_ranking_latest.csv"
    plain.write_text("plain", encoding="utf-8")
    prefixed.write_text("prefixed", encoding="utf-8")

    found = find_latest(paths, "mars_ranking", exp, {"saving": {"filename_prefix_with_set": True}})

    assert found == prefixed


def test_storage_find_latest_reads_legacy_route_and_prefix(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")
    legacy_dir = tmp_path / "outputs" / "B3a__Paradox_Drive" / "Matrices" / "winrate"
    legacy_dir.mkdir(parents=True)
    legacy = legacy_dir / "B3a_filtered_wr_latest.csv"
    legacy.write_text("legacy", encoding="utf-8")

    found = find_latest(paths, "wr_matrix", exp, {"saving": {"filename_prefix_with_set": True}})

    assert found == legacy


def test_storage_write_csv_setaware_supports_new_config_key(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")
    df = pd.DataFrame({"Deck": ["Pikachu"], "Score": [100]})

    out = write_csv_versioned_setaware(
        df,
        paths,
        "mars_ranking",
        exp,
        {"saving": {"filename_prefix_with_set": True}},
        changed=False,
    )

    assert out.name == "B3a_mars_ranking_latest.csv"
    assert out.exists()


def test_storage_write_csv_setaware_defaults_to_unprefixed_names(tmp_path: Path):
    paths = SimpleNamespace(outputs=tmp_path / "outputs")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")
    df = pd.DataFrame({"Deck": ["Pikachu"], "Score": [100]})

    out = write_csv_versioned_setaware(
        df,
        paths,
        "wr_matrix",
        exp,
        {"saving": {"filename_prefix_with_set": False}},
        changed=False,
    )

    assert out == (tmp_path / "outputs" / "B3a__Paradox_Drive" / "matrices" / "winrate" / "winrate_matrix_latest.csv").resolve()
    assert out.exists()


def test_legacy_imports_delegate_to_storage(tmp_path: Path):
    storage_paths = init_paths(tmp_path / "storage")
    legacy_paths = legacy_init_paths(tmp_path / "legacy")
    exp = ExpansionRef(code="B3a", name="Paradox Drive")

    assert storage_paths.outputs.name == legacy_paths.outputs.name == "outputs"
    assert legacy_dest_for_key(legacy_paths, "score_flat", exp) == dir_for_key(legacy_paths, "score_flat", exp)
