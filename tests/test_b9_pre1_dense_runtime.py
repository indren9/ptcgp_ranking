from __future__ import annotations

import math

import pandas as pd

from core.consolidate import (
    apply_alias_and_aggregate,
    build_score_table_filtered,
    maxN_flat,
)
from core.matrices import build_matrices, n_dir_from_WL


def _dense_fixture() -> tuple[pd.DataFrame, list[str]]:
    axis = ["deck-a", "deck-b", "deck-c"]
    rows = [
        # decisive pair
        {"Deck A": "deck-a", "Deck B": "deck-b", "W": 3, "L": 1, "T": 0},
        {"Deck A": "deck-b", "Deck B": "deck-a", "W": 1, "L": 3, "T": 0},
        # explicit zero-evidence pair
        {"Deck A": "deck-a", "Deck B": "deck-c", "W": 0, "L": 0, "T": 0},
        {"Deck A": "deck-c", "Deck B": "deck-a", "W": 0, "L": 0, "T": 0},
        # explicit tie-only pair
        {"Deck A": "deck-b", "Deck B": "deck-c", "W": 0, "L": 0, "T": 2},
        {"Deck A": "deck-c", "Deck B": "deck-b", "W": 0, "L": 0, "T": 2},
    ]
    return pd.DataFrame(rows), axis


def _row(df: pd.DataFrame, a: str, b: str) -> pd.Series:
    return df[(df["Deck A"] == a) & (df["Deck B"] == b)].iloc[0]


def test_default_false_is_semantically_identical_to_explicit_false():
    dense, axis = _dense_fixture()
    default = build_score_table_filtered(dense, axis)
    explicit = build_score_table_filtered(dense, axis, preserve_zero_evidence=False)
    pd.testing.assert_frame_equal(default, explicit)


def test_zero_evidence_default_false_is_removed():
    dense, axis = _dense_fixture()
    out = build_score_table_filtered(dense, axis, preserve_zero_evidence=False)
    assert not ((out["Deck A"] == "deck-a") & (out["Deck B"] == "deck-c")).any()
    assert len(out) == 2


def test_zero_evidence_true_is_preserved_with_nan_wr():
    dense, axis = _dense_fixture()
    out = build_score_table_filtered(dense, axis, preserve_zero_evidence=True)
    row = _row(out, "deck-a", "deck-c")
    assert (int(row["W"]), int(row["L"]), int(row["T"]), int(row["N"])) == (0, 0, 0, 0)
    assert math.isnan(float(row["WR_dir"]))


def test_tie_only_true_preserves_t_and_n_with_nan_wr():
    dense, axis = _dense_fixture()
    out = build_score_table_filtered(dense, axis, preserve_zero_evidence=True)
    row = _row(out, "deck-b", "deck-c")
    assert (int(row["W"]), int(row["L"]), int(row["T"]), int(row["N"])) == (0, 0, 2, 2)
    assert math.isnan(float(row["WR_dir"]))


def test_decisive_true_is_unchanged():
    dense, axis = _dense_fixture()
    out = build_score_table_filtered(dense, axis, preserve_zero_evidence=True)
    row = _row(out, "deck-a", "deck-b")
    assert (int(row["W"]), int(row["L"]), int(row["T"]), int(row["N"])) == (3, 1, 0, 4)
    assert float(row["WR_dir"]) == 75.0


def test_dense_input_survives_legacy_consolidation_and_has_t_times_t_minus_one_rows():
    dense, axis = _dense_fixture()
    maxed = maxN_flat(dense)
    aggregated = apply_alias_and_aggregate(maxed, {})
    out = build_score_table_filtered(aggregated, axis, preserve_zero_evidence=True)

    assert len(out) == len(axis) * (len(axis) - 1) == 6
    assert not (out["Deck A"] == out["Deck B"]).any()
    assert set(zip(out["Deck A"], out["Deck B"])) == {
        (a, b) for a in axis for b in axis if a != b
    }


def test_dense_zero_evidence_and_tie_only_produce_wr_nan_and_n_dir_zero():
    dense, axis = _dense_fixture()
    out = build_score_table_filtered(dense, axis, preserve_zero_evidence=True)
    w, l, _, wr = build_matrices(out, axis)
    n_dir = n_dir_from_WL(w, l)

    assert pd.isna(wr.loc["deck-a", "deck-c"])
    assert pd.isna(wr.loc["deck-b", "deck-c"])
    assert float(n_dir.loc["deck-a", "deck-c"]) == 0.0
    assert float(n_dir.loc["deck-b", "deck-c"]) == 0.0


def test_pipeline_core_boundary_exposes_and_forwards_preserve_zero_evidence_flag():
    import ast
    from pathlib import Path

    source = Path("pipelines/deck_ranking.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_core_matrices"
    )
    kwonly = {arg.arg: default for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults)}
    assert "preserve_zero_evidence" in kwonly
    assert isinstance(kwonly["preserve_zero_evidence"], ast.Constant)
    assert kwonly["preserve_zero_evidence"].value is False

    calls = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_score_table_filtered"
    ]
    assert len(calls) == 1
    forwarded = {kw.arg: kw.value for kw in calls[0].keywords}
    assert "preserve_zero_evidence" in forwarded
    assert isinstance(forwarded["preserve_zero_evidence"], ast.Name)
    assert forwarded["preserve_zero_evidence"].id == "preserve_zero_evidence"
