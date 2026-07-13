
# ──────────────────────────────────────────────────────────────────────────────
# core/matrices.py - matrix building on a fixed axis
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from typing import List, Tuple
import numpy as np
import pandas as pd
import logging

log = logging.getLogger("ptcgp")

from core.normalize import apply_alias_series

def topmeta_post_alias(df_top_meta: pd.DataFrame, alias_index: dict) -> pd.DataFrame:
    """
    Build a post-alias top-meta table from Deck plus a share-like column:
      - Deck (canonical, post-alias)
      - Share_frac in [0,1]
      - Share_% (percentage)
    Tolerates column names such as 'share', 'Share', 'Share_%', 'Usage_%', and 'Share_frac'.
    """
    if df_top_meta is None or df_top_meta.empty:
        return pd.DataFrame(columns=["Deck", "Share_frac", "Share_%"])

    df = df_top_meta.copy()

    # 1) Deck column.
    deck_col = None
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("deck", "archetype", "name"):
            deck_col = c
            break
    if deck_col is None:
        # Fallback: first plausible object column.
        cand = [c for c in df.columns if df[c].dtype == object]
        if not cand:
            raise KeyError("Top-meta has no recognizable 'Deck' column.")
        deck_col = cand[0]

    # 2) Share column, accepting several variants.
    lowmap = {str(c).strip().lower(): c for c in df.columns}
    share_col = None
    share_key = None
    for k in ("share_frac", "share frac", "share_%", "usage_%", "share", "usage", "meta_%"):
        if k in lowmap:
            share_col = lowmap[k]
            share_key = k
            break
    if share_col is None:
        # Fallback: first numeric column.
        num_cand = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        if not num_cand:
            raise KeyError(f"Top-meta has no recognizable share column. Columns: {list(df.columns)}")
        share_col = num_cand[0]

    # 3) Select and rename.
    df = df[[deck_col, share_col]].rename(columns={deck_col: "Deck_raw", share_col: "share_raw"})
    df["Deck_raw"] = df["Deck_raw"].astype(str).str.strip()
    raw = df["share_raw"]
    if raw.dtype == object:
        raw = raw.astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False).str.strip()
    sr = pd.to_numeric(raw, errors="coerce").fillna(0.0).astype(float)

    # 4) Normalize to fraction [0,1].
    # Only Share_frac variants are already fractions. Other Share/share columns
    # are percentage points, even when the value is below 1 (e.g. 0.96 = 0.96%).
    if share_key in ("share_frac", "share frac"):
        df["Share_frac"] = sr.where(sr <= 1.0, sr / 100.0).clip(lower=0.0, upper=1.0)
    else:
        df["Share_frac"] = (sr / 100.0).clip(lower=0.0, upper=1.0)

    # 5) Alias to canonical Deck.
    df["Deck"] = apply_alias_series(df["Deck_raw"], alias_index)

    # 6) Aggregate by canonical name, sort, compute Share_%.
    out = (
        df.groupby("Deck", as_index=False)["Share_frac"]
          .sum()
          .sort_values("Share_frac", ascending=False)
          .reset_index(drop=True)
    )
    out["Share_%"] = (out["Share_frac"] * 100.0).round(2)

    return out[["Deck", "Share_frac", "Share_%"]]



def build_matrices(df_flat_alias: pd.DataFrame, axis: List[str], *, mode: str = "exclude", mirror: float | None = None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (W_mat, L_mat, T_mat, WR_mat) on the fixed `axis`.
    - mode="exclude": WR = 100*W/(W+L)
    - mode="half": WR = 100*(W+0.5T)/(W+L+T)
    Diagonal = NaN if mirror is None, otherwise the requested value.
    """
    if not axis:
        raise RuntimeError("Empty top-meta axis")
    df = df_flat_alias.copy()
    df = df[df["Deck A"].isin(axis) & df["Deck B"].isin(axis)]

    # Pivot W/L/T.
    W = (df.pivot_table(index="Deck A", columns="Deck B", values="W", aggfunc="sum", fill_value=0)
           .reindex(index=axis, columns=axis, fill_value=0))
    L = (df.pivot_table(index="Deck A", columns="Deck B", values="L", aggfunc="sum", fill_value=0)
           .reindex(index=axis, columns=axis, fill_value=0))
    T = (df.pivot_table(index="Deck A", columns="Deck B", values="T", aggfunc="sum", fill_value=0)
           .reindex(index=axis, columns=axis, fill_value=0))

    # WR.
    Wv, Lv, Tv = W.to_numpy(float), L.to_numpy(float), T.to_numpy(float)
    if mode == "half":
        denom = Wv + Lv + Tv
        num = Wv + 0.5 * Tv
    else:
        denom = Wv + Lv
        num = Wv
    wr = np.full_like(denom, np.nan, dtype=float)
    np.divide(100.0 * num, denom, out=wr, where=(denom > 0))
    if mirror is None:
        np.fill_diagonal(wr, np.nan)
    else:
        np.fill_diagonal(wr, float(mirror))
    WR = pd.DataFrame(wr, index=axis, columns=axis).round(2)

    return W.astype("Int64"), L.astype("Int64"), T.astype("Int64"), WR


def n_dir_from_WL(W: pd.DataFrame, L: pd.DataFrame) -> pd.DataFrame:
    n_dir = (W.astype(int) + L.astype(int)).astype("Int64")
    arr = n_dir.to_numpy(copy=True)
    # NaN diagonal by contract, matching the WR matrix.
    # Keep zero counts internally, but expose NaN on the contractual diagonal.
    import numpy as np
    arr = arr.astype(float)
    np.fill_diagonal(arr, np.nan)
    return pd.DataFrame(arr, index=n_dir.index, columns=n_dir.columns)

