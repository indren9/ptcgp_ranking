from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _offdiag_mask(size: int) -> np.ndarray:
    mask = np.ones((size, size), dtype=bool)
    np.fill_diagonal(mask, False)
    return mask


def _top_meta_lookup(top_meta: pd.DataFrame | None) -> dict[str, float]:
    if top_meta is None or top_meta.empty or "Deck" not in top_meta.columns:
        return {}

    df = top_meta.copy()
    if "Share_%" in df.columns:
        share = pd.to_numeric(df["Share_%"], errors="coerce").fillna(0.0)
    elif "Share_frac" in df.columns:
        share = pd.to_numeric(df["Share_frac"], errors="coerce").fillna(0.0) * 100.0
    elif "share" in df.columns:
        share = pd.to_numeric(df["share"], errors="coerce").fillna(0.0)
    elif "Share" in df.columns:
        share = pd.to_numeric(
            df["Share"].astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce",
        ).fillna(0.0)
    else:
        share = pd.Series([0.0] * len(df), index=df.index)

    return dict(zip(df["Deck"].astype(str).str.strip(), share.astype(float)))


def build_nan_diagnostics(
    wr: pd.DataFrame,
    n_dir: pd.DataFrame | None = None,
    top_meta: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Build pre-filter diagnostics for the current matchup matrix axis.

    The table is intentionally descriptive, not prescriptive: it helps inspect
    daily Limitless data before choosing or tuning a dynamic NaN threshold.
    """
    if wr is None or wr.empty:
        empty = pd.DataFrame(
            columns=[
                "Deck",
                "Share_%",
                "observed_opponents",
                "total_opponents",
                "coverage_%",
                "nan_count",
                "nan_ratio",
                "total_matches",
            ]
        )
        return empty, {"axis_count": 0}

    axis = [str(x).strip() for x in wr.index.tolist()]
    wr_norm = wr.copy()
    wr_norm.index = axis
    wr_norm.columns = [str(x).strip() for x in wr_norm.columns.tolist()]
    wr_norm = wr_norm.reindex(index=axis, columns=axis)

    total_opponents = max(0, len(axis) - 1)
    mask = _offdiag_mask(len(axis))
    is_observed = wr_norm.notna().to_numpy(bool) & mask
    observed = is_observed.sum(axis=1)
    nan_count = total_opponents - observed
    nan_ratio = np.divide(nan_count, total_opponents, out=np.zeros_like(nan_count, dtype=float), where=total_opponents > 0)
    coverage = 1.0 - nan_ratio

    share_lookup = _top_meta_lookup(top_meta)

    if n_dir is not None and not n_dir.empty:
        n_norm = n_dir.copy()
        n_norm.index = [str(x).strip() for x in n_norm.index.tolist()]
        n_norm.columns = [str(x).strip() for x in n_norm.columns.tolist()]
        n_norm = n_norm.reindex(index=axis, columns=axis)
        volumes = n_norm.fillna(0).to_numpy(float)
        np.fill_diagonal(volumes, 0.0)
        total_matches = volumes.sum(axis=1)
    else:
        total_matches = np.zeros(len(axis), dtype=float)

    df = pd.DataFrame(
        {
            "Deck": axis,
            "Share_%": [round(float(share_lookup.get(deck, 0.0)), 4) for deck in axis],
            "observed_opponents": observed.astype(int),
            "total_opponents": int(total_opponents),
            "coverage_%": np.round(coverage * 100.0, 2),
            "nan_count": nan_count.astype(int),
            "nan_ratio": np.round(nan_ratio, 4),
            "total_matches": np.round(total_matches, 0).astype(int),
        }
    )
    df = df.sort_values(
        ["nan_ratio", "Share_%", "total_matches", "Deck"],
        ascending=[False, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    def _quantiles(series: pd.Series) -> dict[str, float]:
        if series.empty:
            return {}
        q = series.astype(float).quantile([0.10, 0.25, 0.50, 0.75, 0.90])
        return {
            "p10": round(float(q.loc[0.10]), 4),
            "p25": round(float(q.loc[0.25]), 4),
            "p50": round(float(q.loc[0.50]), 4),
            "p75": round(float(q.loc[0.75]), 4),
            "p90": round(float(q.loc[0.90]), 4),
        }

    summary = {
        "axis_count": len(axis),
        "total_opponents": total_opponents,
        "share_total_%": round(float(df["Share_%"].sum()), 4),
        "coverage_%": _quantiles(df["coverage_%"]),
        "nan_ratio": _quantiles(df["nan_ratio"]),
        "total_matches": _quantiles(df["total_matches"]),
        "low_coverage_decks": df.head(10).to_dict(orient="records"),
    }
    return df, summary


__all__ = ["build_nan_diagnostics"]
