
# ──────────────────────────────────────────────────────────────────────────────
# core/nan_filter.py — filtro NaN iterativo stabile
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from typing import List, Tuple
import math
import logging
import numpy as np
import pandas as pd

log = logging.getLogger("ptcgp")


def filter_wr_nan_iterative(wr: pd.DataFrame, *, max_nan_ratio: float, min_nan_allowed: int = 1, use_ceil: bool = False) -> Tuple[pd.DataFrame, List[str]]:
    """Applica filtro eliminando iterativamente i mazzi con più NaN off-diagonale
    oltre la soglia fissa calcolata da `max_nan_ratio`.
    Ritorna (wr_filtrata, dropped_order).
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
        log.info("[NaN-filter] iter %d: drop %d mazzi (excess=%d > allowed=%d). Esempio: %s", it+1, len(to_drop), exceed, allowed, to_drop[0])
        cur = cur.drop(index=to_drop, columns=to_drop)
        dropped.extend(to_drop)
        it += 1

    return cur, dropped

