from __future__ import annotations
import numpy as np
import pandas as pd

def validate_contract(filtered_wr: pd.DataFrame, n_dir: pd.DataFrame) -> dict:
    """
    Controlli contratto: shape/assi identici, diag=NaN, WR(A,B)+WR(B,A)≈100, n_dir simmetrica.
    """
    issues: list[str] = []
    ok = True
    if filtered_wr.shape != n_dir.shape:
        ok = False; issues.append("shape mismatch")
    if not filtered_wr.index.equals(n_dir.index) or not filtered_wr.columns.equals(n_dir.columns):
        ok = False; issues.append("axis mismatch")
    if not np.all(np.isnan(np.diag(filtered_wr.values))):
        ok = False; issues.append("filtered_wr diag not NaN")
    if not np.all(np.isnan(np.diag(n_dir.values))):
        ok = False; issues.append("n_dir diag not NaN")
    wr_sum = (filtered_wr + filtered_wr.T).to_numpy()
    m = ~np.isnan(wr_sum)
    if m.any() and (np.abs(wr_sum[m] - 100.0) > 1.0).sum() > 0:
        issues.append("WR symmetry off >1.0pp")
    if (n_dir.fillna(0.0) - n_dir.T.fillna(0.0)).to_numpy().any():
        issues.append("n_dir not symmetric")
    return {"ok": ok and len([e for e in issues if "mismatch" in e]) == 0, "issues": issues}
