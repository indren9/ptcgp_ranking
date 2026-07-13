from __future__ import annotations
import numpy as np
import pandas as pd

def coverage_tables(n_dir: pd.DataFrame, axis: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build:
      - coverage_df: observed coverage by deck (Opp_used, Missing, Coverage_%, N_eff)
      - missing_pairs_long: long list of all missing A->B pairs

    Parameters
    ----------
    n_dir : pd.DataFrame
        Directional W+L matrix on the final axis; off-diagonal cells are observed if >0.
    axis : list[str]
        Deck order for rows/columns.

    Returns
    -------
    coverage_df : pd.DataFrame
        Columns: Deck, Opp_used, Opp_total, Missing, Coverage_%, N_eff, Missing_sample (max 5)
    missing_pairs_long : pd.DataFrame
        Columns: Deck, Missing_opponent, sorted by deck/opponent.
    """
    N = n_dir.reindex(index=axis, columns=axis)
    OBS = (N.fillna(0.0) > 0.0)
    np.fill_diagonal(OBS.values, False)

    Opp_used = OBS.sum(axis=1)
    Opp_total = len(axis) - 1
    Coverage = (Opp_used / max(Opp_total, 1)) * 100.0
    N_eff = N.sum(axis=1, skipna=True)

    # Missing sample (max 5) per deck, preserving axis order.
    miss_samples = []
    for a in axis:
        row = N.loc[a]
        miss = [b for b in axis if (a != b and (row.get(b, 0.0) <= 0.0 or pd.isna(row.get(b, np.nan))))]
        miss_samples.append(", ".join(miss[:5]))

    coverage_df = pd.DataFrame({
        "Deck": axis,
        "Opp_used": Opp_used.values,
        "Opp_total": int(Opp_total),
        "Missing": (int(Opp_total) - Opp_used.values),
        "Coverage_%": Coverage.values,
        "N_eff": N_eff.reindex(axis).values,
        "Missing_sample (max 5)": miss_samples,
    }).sort_values(["Missing", "Opp_used", "Deck"], ascending=[False, True, True]).reset_index(drop=True)

    # Long list of missing pairs (A->B with W+L <= 0).
    pairs = []
    for a in axis:
        row = N.loc[a]
        for b in axis:
            if a != b and (row.get(b, 0.0) <= 0.0 or pd.isna(row.get(b, np.nan))):
                pairs.append((a, b))
    missing_pairs_long = pd.DataFrame(pairs, columns=["Deck", "Missing_opponent"])\
                          .sort_values(["Deck", "Missing_opponent"]).reset_index(drop=True)

    return coverage_df, missing_pairs_long
