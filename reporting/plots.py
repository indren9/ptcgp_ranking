from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from storage.paths import init_paths
from storage.routing import dir_for_key
from storage.writers import save_plot_dual


def _font_sizes(k: int) -> tuple[float, float, float]:
    if k <= 8:
        return 11, 11, 14
    if k <= 10:
        return 10, 10, 14
    if k <= 12:
        return 9.5, 9.5, 13.5
    if k <= 15:
        return 9.0, 9.0, 13
    if k <= 20:
        return 8.0, 8.0, 12.5
    if k <= 30:
        return 7.5, 7.5, 12
    return 7.0, 7.0, 11.5


def show_wr_heatmap(
    ranking: pd.DataFrame,
    *,
    wr: pd.DataFrame,
    top_n: int = 20,
    mask_mirror: bool = False,
    annot: bool = False,
    fmt: str = ".1f",
    cmap: str = "RdBu_r",
    center: float = 50.0,
    vmin: float = 0.0,
    vmax: float = 100.0,
    figsize: Tuple[float, float] = (12, 10),
    title: Optional[str] = None,
    na_color: str = "white",
    save: bool = False,
    save_dir: Path | None = None,
    save_fmt: str = "png",
    save_dpi: int = 300,
) -> Tuple[plt.Figure, plt.Axes, pd.DataFrame]:
    """
    Draw a directional win-rate heatmap ordered by ranking.

    Returns the matplotlib figure, axes, and the WR submatrix actually plotted.
    """
    if "Deck" not in ranking.columns:
        raise ValueError("ranking must contain the 'Deck' column.")

    total = len(ranking)
    if total < 2:
        raise ValueError("At least 2 decks are required to draw the heatmap.")

    k = max(2, min(int(top_n), total))
    decks = ranking["Deck"].astype(str).head(k).tolist()

    wr_aligned = wr.copy()
    wr_aligned.index = wr_aligned.index.astype(str)
    wr_aligned.columns = wr_aligned.columns.astype(str)
    wr_sub = wr_aligned.reindex(index=decks, columns=decks)
    np.fill_diagonal(wr_sub.values, np.nan)

    mask = wr_sub.isna().to_numpy()
    if mask_mirror:
        mask |= np.triu(np.ones_like(mask, dtype=bool), k=1)

    sns.set_theme(style="white")
    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)

    cmap_obj = sns.color_palette(cmap, as_cmap=True)
    try:
        cmap_obj.set_bad(na_color)
    except Exception:
        pass

    tick_fs, annot_fs, title_fs = _font_sizes(k)

    sns.heatmap(
        wr_sub,
        mask=mask,
        ax=ax,
        cmap=cmap_obj,
        vmin=vmin,
        vmax=vmax,
        center=center,
        square=True,
        cbar_kws={"label": "Winrate %"},
        linewidths=0,
        linecolor=None,
        annot=annot,
        fmt=fmt,
        annot_kws={"fontsize": annot_fs} if annot else None,
    )

    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right", fontsize=tick_fs)
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0, fontsize=tick_fs)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(False)

    if title is None:
        title = f"WR heatmap - Top-{k} (ranking order)" if k < total else f"WR heatmap - Full ranking ({total})"
    ax.set_title(title, pad=12, fontsize=title_fs)

    if save:
        if save_dir is None:
            paths = init_paths(Path.cwd())
            save_dir = dir_for_key(paths, "heatmap_topN", exp=None)
        save_plot_dual(fig, base_dir=save_dir, prefix="wr_heatmap", tag=f"T{k}", fmt=save_fmt, dpi=save_dpi)

    return fig, ax, wr_sub


__all__ = ["show_wr_heatmap"]
