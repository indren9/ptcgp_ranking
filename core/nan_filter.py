
# ──────────────────────────────────────────────────────────────────────────────
# core/nan_filter.py - stable iterative NaN filter
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from typing import List, Tuple
import math
import logging
import numpy as np
import pandas as pd

log = logging.getLogger("ptcgp")


def filter_wr_nan_iterative(wr: pd.DataFrame, *, max_nan_ratio: float, min_nan_allowed: int = 1, use_ceil: bool = False) -> Tuple[pd.DataFrame, List[str]]:
    """Iteratively drop decks with too many off-diagonal NaNs.

    The fixed threshold is computed from `max_nan_ratio`.
    Returns (filtered_wr, dropped_order).
    """
    if wr is None or wr.empty:
        return wr, []

    axis = wr.index.tolist()
    opponents = max(0, len(axis) - 1)
    base = opponents * float(max_nan_ratio)
    allowed = math.ceil(base) if use_ceil else math.floor(base)
    allowed = max(int(min_nan_allowed), int(allowed))

    def _offdiag_nan_counts(df: pd.DataFrame) -> pd.Series:
        is_nan = df.isna()
        return is_nan.sum(axis=1).sub(np.diag(is_nan.values)).astype("int64")

    dropped: List[str] = []
    cur = wr.copy()
    it = 0
    while True:
        if cur.shape[0] <= 2:
            break
        counts = _offdiag_nan_counts(cur)
        over = counts - allowed
        over = over[over > 0]
        if over.empty:
            break
        exceed = int(over.max())
        to_drop = over[over == exceed].index.tolist()
        log.debug("[NaN-filter] iter %d: drop %d decks (excess=%d > allowed=%d). Example: %s", it+1, len(to_drop), exceed, allowed, to_drop[0])
        cur = cur.drop(index=to_drop, columns=to_drop)
        dropped.extend(to_drop)
        it += 1

    return cur, dropped


def _candidate_ratios(min_ratio: float, max_ratio: float, step: float) -> list[float]:
    min_ratio = max(0.0, float(min_ratio))
    max_ratio = min(1.0, max(min_ratio, float(max_ratio)))
    step = max(0.001, float(step))

    out: list[float] = []
    cur = min_ratio
    while cur <= max_ratio + 1e-12:
        out.append(round(cur, 4))
        cur += step
    if out[-1] < max_ratio:
        out.append(round(max_ratio, 4))
    return out


def _share_lookup(top_meta: pd.DataFrame | None) -> pd.Series:
    if top_meta is None or top_meta.empty or "Deck" not in top_meta.columns:
        return pd.Series(dtype=float)
    if "Share_%" in top_meta.columns:
        share = pd.to_numeric(top_meta["Share_%"], errors="coerce").fillna(0.0)
    elif "Share_frac" in top_meta.columns:
        share = pd.to_numeric(top_meta["Share_frac"], errors="coerce").fillna(0.0) * 100.0
    else:
        share = pd.Series([0.0] * len(top_meta), index=top_meta.index)
    return pd.Series(share.astype(float).to_numpy(), index=top_meta["Deck"].astype(str).str.strip())


def choose_dynamic_nan_filter(
    wr: pd.DataFrame,
    top_meta: pd.DataFrame | None,
    *,
    min_nan_ratio: float,
    max_nan_ratio: float,
    step: float,
    target_share_pct: float,
    min_axis_count: int | None,
    min_nan_allowed: int = 1,
    use_ceil: bool = False,
) -> tuple[float, pd.DataFrame, dict]:
    """
    Pick the lowest NaN ratio whose iterative filtering result satisfies targets.

    This does not filter deck rows in one pass. Each candidate ratio runs the
    same iterative filter used by the fixed mode, because removing a deck changes
    the off-diagonal NaN counts of the remaining matrix.
    """
    if wr is None or wr.empty:
        empty = pd.DataFrame(columns=["max_nan_ratio", "axis_count", "dropped_count", "share_kept_%"])
        return float(min_nan_ratio), empty, {"reason": "empty_matrix"}

    axis_target = None if min_axis_count is None else int(min_axis_count)
    shares = _share_lookup(top_meta)
    rows: list[dict] = []
    for ratio in _candidate_ratios(min_nan_ratio, max_nan_ratio, step):
        kept_wr, dropped = filter_wr_nan_iterative(
            wr,
            max_nan_ratio=ratio,
            min_nan_allowed=min_nan_allowed,
            use_ceil=use_ceil,
        )
        kept_axis = [str(x).strip() for x in kept_wr.index.tolist()]
        share_kept = float(shares.reindex(kept_axis).fillna(0.0).sum()) if not shares.empty else 0.0
        row = {
            "max_nan_ratio": ratio,
            "axis_count": len(kept_axis),
            "dropped_count": len(dropped),
            "share_kept_%": round(share_kept, 4),
            "target_axis_met": True if axis_target is None else len(kept_axis) >= axis_target,
            "target_share_met": share_kept >= float(target_share_pct),
        }
        rows.append(row)

    sim_df = pd.DataFrame(rows)
    ok = sim_df[sim_df["target_axis_met"] & sim_df["target_share_met"]]
    if not ok.empty:
        selected = float(ok.iloc[0]["max_nan_ratio"])
        reason = "target_met"
    else:
        # Fallback: keep the best available candidate inside configured bounds.
        ranked = sim_df.sort_values(["share_kept_%", "axis_count", "max_nan_ratio"], ascending=[False, False, True])
        selected = float(ranked.iloc[0]["max_nan_ratio"])
        reason = "best_available_within_bounds"

    selected_row = sim_df[sim_df["max_nan_ratio"] == selected].iloc[0].to_dict()
    diagnostics = {
        "mode": "dynamic",
        "selected_max_nan_ratio": selected,
        "reason": reason,
        "target_share_pct": float(target_share_pct),
        "min_axis_count": axis_target,
        "min_nan_ratio": float(min_nan_ratio),
        "max_nan_ratio": float(max_nan_ratio),
        "step": float(step),
        "selected_axis_count": int(selected_row["axis_count"]),
        "selected_share_kept_%": float(selected_row["share_kept_%"]),
    }
    return selected, sim_df, diagnostics


__all__ = ["choose_dynamic_nan_filter", "filter_wr_nan_iterative"]

