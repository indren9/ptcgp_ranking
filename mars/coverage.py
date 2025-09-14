from __future__ import annotations
import numpy as np
import pandas as pd

def coverage_tables(n_dir: pd.DataFrame, axis: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Costruisce:
      - coverage_df: per ogni deck, copertura osservata (Opp_used, Missing, Coverage_%, N_eff)
      - missing_pairs_long: lista lunga (Deck, Missing_opponent) per tutte le coppie mancanti A→B

    Parametri
    ---------
    n_dir : pd.DataFrame
        Matrice W+L direzionale sull'asse finale (off-diag osservate se >0).
    axis : list[str]
        Ordine dei deck (righe/colonne).

    Ritorna
    -------
    coverage_df : pd.DataFrame
        Colonne: Deck, Opp_used, Opp_total, Missing, Coverage_%, N_eff, Missing_sample (max 5)
    missing_pairs_long : pd.DataFrame
        Colonne: Deck, Missing_opponent (ordinate alfabeticamente per deck/opponent)
    """
    N = n_dir.reindex(index=axis, columns=axis)
    OBS = (N.fillna(0.0) > 0.0)
    np.fill_diagonal(OBS.values, False)

    Opp_used = OBS.sum(axis=1)
    Opp_total = len(axis) - 1
    Coverage = (Opp_used / max(Opp_total, 1)) * 100.0
    N_eff = N.sum(axis=1, skipna=True)

    # missing sample (max 5) per deck, preservando l’ordine di axis
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

    # long list delle coppie mancanti (A→B con W+L<=0)
    pairs = []
    for a in axis:
        row = N.loc[a]
        for b in axis:
            if a != b and (row.get(b, 0.0) <= 0.0 or pd.isna(row.get(b, np.nan))):
                pairs.append((a, b))
    missing_pairs_long = pd.DataFrame(pairs, columns=["Deck", "Missing_opponent"])\
                          .sort_values(["Deck", "Missing_opponent"]).reset_index(drop=True)

    return coverage_df, missing_pairs_long
