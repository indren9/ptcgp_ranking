from __future__ import annotations
import numpy as np
import pandas as pd
from math import erf, sqrt

def _z(s: pd.Series) -> pd.Series:
    v = s.values.astype(float)
    m = float(np.nanmean(v))
    sd = float(np.nanstd(v))
    return pd.Series(np.zeros_like(v), index=s.index) if sd <= 1e-12 else (s - m) / sd

def compose(lb_pct: pd.Series, bt_pct: pd.Series, alpha: float) -> pd.Series:
    """
    Combine z(LB) and z(BT) with alpha weight into z_comp, then map to Score_% in [0,100].
    """
    z = alpha * _z(lb_pct) + (1.0 - alpha) * _z(bt_pct)
    return 100.0 * 0.5 * (1.0 + pd.Series([erf(val / sqrt(2.0)) for val in z], index=z.index))
