from __future__ import annotations
import numpy as np
import pandas as pd
from .config import MARSConfig

def _normalize_weights(vals: pd.Series | np.ndarray, index: pd.Index, floor: float | None = None) -> pd.Series:
    """Robust weight normalization with optional floor, safe to zeros."""
    s = pd.Series(vals, index=index, dtype=float).fillna(0.0)
    if floor is not None:
        s = s.clip(lower=floor)
    tot = float(s.sum())
    if not np.isfinite(tot) or tot <= 0:
        return pd.Series(np.ones(len(index)) / max(len(index), 1), index=index, dtype=float)
    return s / tot

def encounter_share(n_dir: pd.DataFrame, axis: list[str]) -> pd.Series:
    """p_enc from column sums of n_dir, renormalized on axis."""
    col_sum = n_dir.sum(axis=0, skipna=True).reindex(axis).fillna(0.0)
    return _normalize_weights(col_sum, pd.Index(axis), floor=None)

def _pick_share_col(df: pd.DataFrame) -> str | None:
    for c in ["Share_frac","Share_%","Usage_%","share_%","usage_%","Share","Usage","Meta_%"]:
        if c in df.columns: return c
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]): return c
    return None

# NEW: correlazione “safe” (niente warning se std=0 oppure overlap < 2)
def _corr_safe(a: pd.Series, b: pd.Series, *, eps: float = 1e-12) -> float:
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    mask = a.notna() & b.notna()
    if mask.sum() < 2:
        return float("nan")
    a = a[mask].to_numpy(dtype=float)
    b = b[mask].to_numpy(dtype=float)
    a = a - a.mean()
    b = b - b.mean()
    sa = a.std(ddof=0)
    sb = b.std(ddof=0)
    if not np.isfinite(sa) or not np.isfinite(sb) or sa <= eps or sb <= eps:
        return float("nan")
    # Pearson (ddof=0, stesso convention di np.corrcoef)
    r = float((a @ b) / (sa * sb * len(a)))
    return r if np.isfinite(r) else float("nan")

def meta_share_on_axis(
    axis: list[str],
    top_meta_df: pd.DataFrame | None,
    p_enc_for_gap: pd.Series | None,
    policy: str
) -> pd.Series:
    """
    Map top-meta shares to axis; if some meta mass falls outside the axis, fill the gap
    with 'policy' ∈ {'proportional','uniform','encounter'}.
    Assumiamo upstream già post-alias: mapping 1:1 per nome deck.
    """
    idx = pd.Index(axis)
    if top_meta_df is None or top_meta_df.empty:
        return pd.Series(1.0 / len(idx), index=idx, dtype=float)

    deck_col = next((c for c in ["Deck","deck","Deck Name","DeckName","Name","Archetype","Alias"]
                     if c in top_meta_df.columns), None)
    share_col = _pick_share_col(top_meta_df)
    if deck_col is None or share_col is None:
        return pd.Series(1.0 / len(idx), index=idx, dtype=float)

    names = top_meta_df[deck_col].astype(str).str.strip()
    shares = pd.to_numeric(top_meta_df[share_col], errors="coerce").fillna(0.0).astype(float).values
    if np.nanmax(shares) > 1.0 + 1e-9:
        shares = shares / 100.0

    s = pd.Series(shares, index=names)
    p = s.groupby(level=0).sum().reindex(idx).fillna(0.0)

    s_on_axis = float(p.sum())
    if s_on_axis <= 0:
        return pd.Series(1.0 / len(idx), index=idx, dtype=float)

    gap = max(0.0, 1.0 - s_on_axis)
    if gap > 0:
        if policy == "uniform":
            w = pd.Series(1.0 / len(idx), index=idx, dtype=float)
        elif policy == "encounter" and p_enc_for_gap is not None:
            w = p_enc_for_gap.reindex(idx).fillna(0.0)
            w = w / (w.sum() if w.sum() > 0 else 1.0)
        else:  # proportional
            w = p / s_on_axis
        p = p + gap * w

    return p / float(p.sum())

def blend_meta(
    axis: list[str],
    n_dir: pd.DataFrame,
    top_meta_df: pd.DataFrame | None,
    cfg: MARSConfig
) -> tuple[pd.Series, dict]:
    """
    Compute meta weights:
      - p_enc from n_dir,
      - p_meta from top-meta table (with gap filling),
      - blend p = (1-γ)*p_meta + γ*p_enc  (γ fixed or AUTO in [GAMMA_MIN, GAMMA_MAX]).
    Returns (weights Series on axis, info dict for logs).
    """
    p_enc = encounter_share(n_dir, axis)
    p_meta = meta_share_on_axis(axis, top_meta_df, p_enc, cfg.META_GAP_POLICY)
    tv = 0.5 * float((p_meta - p_enc).abs().sum())

    if cfg.AUTO_GAMMA:
        gamma_raw = cfg.GAMMA_BASE + cfg.GAMMA_SLOPE * tv
        gamma = float(np.clip(gamma_raw, cfg.GAMMA_MIN, cfg.GAMMA_MAX))
        floor = cfg.EPS
    else:
        gamma = float(cfg.GAMMA_META_BLEND)
        floor = None

    p = (1.0 - gamma) * p_meta + gamma * p_enc
    p = _normalize_weights(p, pd.Index(axis), floor=floor)

    info = {
        "policy": cfg.META_GAP_POLICY,
        "AUTO_GAMMA": cfg.AUTO_GAMMA,
        "gamma": gamma,
        "tv": tv,
        # QUI: nuovo calcolo robusto (niente warning se std=0)
        "corr": _corr_safe(p_meta, p_enc, eps=cfg.EPS) if len(axis) > 1 else float("nan"),
    }
    return p, info
