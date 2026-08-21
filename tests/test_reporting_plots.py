import matplotlib.pyplot as plt
import pandas as pd
import pytest

from reporting.plots import show_wr_heatmap


def sample_ranking() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Deck": "Pikachu", "Score_%": 60.0},
            {"Deck": "Mewtwo", "Score_%": 55.0},
            {"Deck": "Charizard", "Score_%": 50.0},
        ]
    )


def sample_wr() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [None, 55.0, 48.0],
            [45.0, None, 52.0],
            [52.0, 48.0, None],
        ],
        index=["Pikachu", "Mewtwo", "Charizard"],
        columns=["Pikachu", "Mewtwo", "Charizard"],
    )


def test_show_wr_heatmap_returns_clamped_submatrix():
    fig, ax, wr_sub = show_wr_heatmap(sample_ranking(), wr=sample_wr(), top_n=99, annot=False)
    try:
        assert wr_sub.index.tolist() == ["Pikachu", "Mewtwo", "Charizard"]
        assert wr_sub.columns.tolist() == ["Pikachu", "Mewtwo", "Charizard"]
        assert pd.isna(wr_sub.loc["Pikachu", "Pikachu"])
        assert ax.get_title() == "WR heatmap - Full ranking (3)"
        assert ax.get_xlabel() == "Opponent (column)"
        assert ax.get_ylabel() == "Deck (row)"
        assert any("Read row → column" in text.get_text() for text in ax.texts)
    finally:
        plt.close(fig)


def test_show_wr_heatmap_requires_deck_column():
    with pytest.raises(ValueError, match="Deck"):
        show_wr_heatmap(pd.DataFrame({"Name": ["Pikachu", "Mewtwo"]}), wr=sample_wr())


def test_utils_display_show_wr_heatmap_is_compatible_wrapper():
    from utils.display import show_wr_heatmap as legacy_show_wr_heatmap

    fig, _, wr_sub = legacy_show_wr_heatmap(sample_ranking(), wr=sample_wr(), top_n=2)
    try:
        assert wr_sub.shape == (2, 2)
    finally:
        plt.close(fig)


def test_show_wr_heatmap_wraps_long_labels_and_can_hide_note():
    ranking = pd.DataFrame(
        [
            {"Deck": "A very long competitive deck archetype"},
            {"Deck": "Another unusually long deck archetype"},
        ]
    )
    wr = pd.DataFrame(
        [[None, 90.0], [10.0, None]],
        index=ranking["Deck"],
        columns=ranking["Deck"],
    )

    fig, ax, _ = show_wr_heatmap(
        ranking,
        wr=wr,
        top_n=2,
        label_wrap_width=14,
        show_orientation_note=False,
    )
    try:
        assert "\n" in ax.get_xticklabels()[0].get_text()
        assert not any("Read row → column" in text.get_text() for text in ax.texts)
        colorbar = fig.axes[-1]
        assert colorbar.get_ylabel() == "Observed win rate (%)"
    finally:
        plt.close(fig)
