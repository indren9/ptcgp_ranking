from __future__ import annotations
from typing import Sequence, Mapping, Optional, Tuple
from pathlib import Path

import pandas as pd
import numpy as np
from IPython.display import display
import matplotlib.pyplot as plt
import seaborn as sns

# IO helpers per routing/salvataggi
from utils.io import init_paths, _dest, save_plot_dual

__all__ = ["show_ranking", "show_wr_heatmap"]

DEFAULT_COLS: list[str] = [
    "Deck", "Score_%", "LB_%", "MAS_%", "BT_%", "SE_%",
    "N_eff", "Opp_used", "Opp_total", "Coverage_%",
]

DEFAULT_FMT: Mapping[str, str] = {
    "Score_%": "{:.2f}",
    "LB_%": "{:.2f}",
    "MAS_%": "{:.2f}",
    "BT_%": "{:.2f}",
    "SE_%": "{:.2f}",
    "Coverage_%": "{:.1f}",
    "N_eff": "{:.0f}",
    "Opp_used": "{:.0f}",
    "Opp_total": "{:.0f}",
}

def show_ranking(
    ranking: pd.DataFrame,
    top_n: int | None = 15,
    cols: Sequence[str] | None = None,
    fmt: Mapping[str, str] | None = None,
    title: str | None = None,
    *,
    show: bool = True,
    return_df: bool = False,
    return_styler: bool = False,
):
    """
    Mostra (con Styler) la Top-N del ranking con formattazione base.

    Parametri
    ---------
    ranking : DataFrame ordinato con colonna 'Deck'
    top_n   : int | None — N righe da mostrare (clamp a [1, len])
    cols    : colonne da mostrare (default: DEFAULT_COLS ∩ columns)
    fmt     : mapping colonna -> formato
    title   : caption
    show    : se True, visualizza (display)
    return_df / return_styler : ritorna l'oggetto corrispondente (mutuamente esclusivi)
    """
    if return_df and return_styler:
        raise ValueError("Scegliere uno tra return_df=True e return_styler=True, non entrambi.")

    if cols is None:
        cols = DEFAULT_COLS
    cols = [c for c in cols if c in ranking.columns]

    k = int(top_n) if (top_n is not None and top_n > 0) else len(ranking)
    k = min(k, len(ranking))

    out = ranking.iloc[:k, :].loc[:, cols].copy()

    fmts = dict(DEFAULT_FMT)
    if fmt:
        fmts.update(fmt)
    fmts.setdefault("N_eff", "{:,.0f}")
    fmts.setdefault("Opp_used", "{:,.0f}")
    fmts.setdefault("Opp_total", "{:,.0f}")

    caption = title or (f"Top {k} / {len(ranking)}" if k < len(ranking) else f"Ranking completo ({len(ranking)})")
    styler = out.style.format({c: fmts[c] for c in cols if c in fmts}).set_caption(caption)

    if show:
        display(styler)
        return None

    if return_styler:
        return styler
    if return_df:
        return out
    return None


def _font_sizes(k: int) -> tuple[float, float, float]:
    """
    Calibrazione dimensioni font in funzione del numero di deck mostrati.
    Ritorna (tick, annot, title).
    """
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
    # salvataggio
    save: bool = False,
    save_dir: Path | None = None,
    save_fmt: str = "png",
    save_dpi: int = 300,
) -> Tuple[plt.Figure, plt.Axes, pd.DataFrame]:
    """
    Disegna una heatmap delle WR direzionali (%) ordinate per Top-N del ranking,
    con diagonale sempre bianca, opzione per mascherare il triangolo superiore,
    annotazioni opzionali e salvataggio duale (latest + versionato) se richiesto.

    Se save=True:
      - salva in <save_dir>/wr_heatmap_latest.<fmt>
      - salva anche versione timestampata <save_dir>/wr_heatmap_T{T}_<YYYYmmdd_HHMMSS>.<fmt>
        (dove T = numero di deck nell'asse 'ranking')

    Parametri chiave
    ----------------
    ranking: DataFrame ordinato (colonna 'Deck' obbligatoria)
    wr:      DataFrame T×T con diagonale NaN
    top_n:   clamp robusto tra 2 e len(ranking)
    annot:   stampa i valori nelle celle (senza %; usa 'fmt')
    save_dir: se None e save=True, usa outputs/Matrices/heatmap (via ROUTES)
    """
    if "Deck" not in ranking.columns:
        raise ValueError("ranking deve contenere la colonna 'Deck'.")

    total = len(ranking)
    if total < 2:
        raise ValueError("Servono almeno 2 deck per disegnare la heatmap.")

    k = int(top_n)
    if k < 2:
        k = 2
    if k > total:
        k = total
    decks = ranking["Deck"].astype(str).head(k).tolist()

    wr_aligned = wr.copy()
    wr_aligned.index = wr_aligned.index.astype(str)
    wr_aligned.columns = wr_aligned.columns.astype(str)
    wr_sub = wr_aligned.reindex(index=decks, columns=decks)
    np.fill_diagonal(wr_sub.values, np.nan)

    # maschera: sempre diagonale; opzionalmente triangolo superiore
    mask = wr_sub.isna().to_numpy()
    if mask_mirror:
        mask |= np.triu(np.ones_like(mask, dtype=bool), k=1)

    # stile
    sns.set_theme(style="white")
    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)

    # colormap con NaN bianco
    cmap_obj = sns.color_palette(cmap, as_cmap=True)
    try:
        cmap_obj.set_bad(na_color)
    except Exception:
        pass

    # font dinamici
    tick_fs, annot_fs, title_fs = _font_sizes(k)

    # heatmap
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

    # etichette & estetica
    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right", fontsize=tick_fs)
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0, fontsize=tick_fs)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(False)

    if title is None:
        title = (
            f"WR heatmap — Top-{k} (ordinata per ranking)"
            if k < total else
            f"WR heatmap — Ranking completo ({total})"
        )
    ax.set_title(title, pad=12, fontsize=title_fs)

    # salvataggio opzionale
    if save:
        # route default se non specificato
        if save_dir is None:
            paths = init_paths(Path.cwd())
            save_dir = _dest(paths, "heatmap_topN")
        # tag richiesto: T{T} (T = numero deck complessivi del ranking)
        tag = f"T{k}"
        save_plot_dual(fig, base_dir=save_dir, prefix="wr_heatmap", tag=tag, fmt=save_fmt, dpi=save_dpi)

    return fig, ax, wr_sub
