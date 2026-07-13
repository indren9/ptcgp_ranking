import numpy as np
import pandas as pd

from core.nan_filter import choose_dynamic_nan_filter, filter_wr_nan_iterative


def test_dynamic_nan_filter_selects_lowest_iterative_threshold_meeting_targets():
    axis = ["A", "B", "C", "D", "E"]
    wr = pd.DataFrame(np.nan, index=axis, columns=axis)
    np.fill_diagonal(wr.values, np.nan)
    observed = {
        "A": ["B", "C", "D", "E"],
        "B": ["A", "C", "D", "E"],
        "C": ["A", "B", "D"],
        "D": ["A", "B"],
        "E": ["A"],
    }
    for deck, opponents in observed.items():
        for opponent in opponents:
            wr.loc[deck, opponent] = 50.0

    top_meta = pd.DataFrame(
        {
            "Deck": axis,
            "Share_%": [30.0, 25.0, 20.0, 15.0, 10.0],
        }
    )

    selected, sim, diag = choose_dynamic_nan_filter(
        wr,
        top_meta,
        min_nan_ratio=0.0,
        max_nan_ratio=0.75,
        step=0.25,
        target_share_pct=85.0,
        min_axis_count=4,
        min_nan_allowed=0,
    )

    assert selected == 0.25
    assert diag["reason"] == "target_met"
    assert diag["selected_axis_count"] == 4
    assert diag["selected_share_kept_%"] == 90.0
    assert sim.loc[sim["max_nan_ratio"] == 0.0, "axis_count"].iloc[0] == 3


def test_dynamic_nan_filter_falls_back_to_best_available_within_bounds():
    axis = ["A", "B", "C"]
    wr = pd.DataFrame(
        [
            [np.nan, 50.0, np.nan],
            [50.0, np.nan, np.nan],
            [np.nan, np.nan, np.nan],
        ],
        index=axis,
        columns=axis,
    )
    top_meta = pd.DataFrame({"Deck": axis, "Share_%": [40.0, 35.0, 25.0]})

    selected, sim, diag = choose_dynamic_nan_filter(
        wr,
        top_meta,
        min_nan_ratio=0.0,
        max_nan_ratio=0.0,
        step=0.25,
        target_share_pct=95.0,
        min_axis_count=3,
        min_nan_allowed=0,
    )

    assert selected == 0.0
    assert diag["reason"] == "best_available_within_bounds"
    assert not sim.empty


def test_dynamic_nan_filter_allows_null_min_axis_count():
    axis = ["A", "B", "C"]
    wr = pd.DataFrame(
        [
            [np.nan, 50.0, np.nan],
            [50.0, np.nan, np.nan],
            [np.nan, np.nan, np.nan],
        ],
        index=axis,
        columns=axis,
    )
    top_meta = pd.DataFrame({"Deck": axis, "Share_%": [60.0, 30.0, 10.0]})

    selected, sim, diag = choose_dynamic_nan_filter(
        wr,
        top_meta,
        min_nan_ratio=0.0,
        max_nan_ratio=0.5,
        step=0.5,
        target_share_pct=80.0,
        min_axis_count=None,
        min_nan_allowed=0,
    )

    assert selected == 0.0
    assert diag["min_axis_count"] is None
    assert diag["selected_axis_count"] == 2
    assert diag["selected_share_kept_%"] == 90.0
    assert sim.loc[sim["max_nan_ratio"] == 0.0, "target_axis_met"].iloc[0] == True


def test_filter_wr_nan_iterative_contract_still_returns_matrix_and_dropped_order():
    wr = pd.DataFrame(
        [
            [np.nan, 50.0, np.nan],
            [50.0, np.nan, np.nan],
            [np.nan, np.nan, np.nan],
        ],
        index=["A", "B", "C"],
        columns=["A", "B", "C"],
    )

    kept, dropped = filter_wr_nan_iterative(wr, max_nan_ratio=0.0, min_nan_allowed=0)

    assert list(kept.index) == ["A", "B"]
    assert dropped == ["C"]
