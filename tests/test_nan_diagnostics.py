import numpy as np
import pandas as pd

from core.nan_diagnostics import build_nan_diagnostics


def test_nan_diagnostics_reports_coverage_share_and_volume():
    wr = pd.DataFrame(
        [
            [np.nan, 50.0, np.nan],
            [55.0, np.nan, 60.0],
            [np.nan, 40.0, np.nan],
        ],
        index=["Pikachu", "Mewtwo", "Charizard"],
        columns=["Pikachu", "Mewtwo", "Charizard"],
    )
    n_dir = pd.DataFrame(
        [
            [np.nan, 10, 0],
            [11, np.nan, 7],
            [0, 8, np.nan],
        ],
        index=wr.index,
        columns=wr.columns,
    )
    top_meta = pd.DataFrame(
        {
            "Deck": ["Pikachu", "Mewtwo", "Charizard"],
            "Share_%": [50.0, 30.0, 20.0],
        }
    )

    df, summary = build_nan_diagnostics(wr, n_dir, top_meta)

    pikachu = df[df["Deck"] == "Pikachu"].iloc[0]
    mewtwo = df[df["Deck"] == "Mewtwo"].iloc[0]

    assert pikachu["observed_opponents"] == 1
    assert pikachu["nan_count"] == 1
    assert pikachu["nan_ratio"] == 0.5
    assert pikachu["coverage_%"] == 50.0
    assert pikachu["total_matches"] == 10
    assert mewtwo["observed_opponents"] == 2
    assert mewtwo["nan_ratio"] == 0.0
    assert summary["axis_count"] == 3
    assert summary["share_total_%"] == 100.0
    assert "p50" in summary["nan_ratio"]


def test_nan_diagnostics_handles_empty_matrix():
    df, summary = build_nan_diagnostics(pd.DataFrame())

    assert df.empty
    assert summary == {"axis_count": 0}
