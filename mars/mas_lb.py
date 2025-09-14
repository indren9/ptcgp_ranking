from __future__ import annotations
import numpy as np
import pandas as pd
from .config import MARSConfig

def mas_se_lb(
    p_hat: pd.DataFrame,
    var_hat: pd.DataFrame,
    p_weights: pd.Series,
    n_dir: pd.DataFrame,
    cfg: MARSConfig,
) -> pd.DataFrame:
    """
    MAS/SE/LB con rinormalizzazione per riga sui soli avversari osservati (n_dir>0).

    Parameters
    ----------
    p_hat, var_hat : pd.DataFrame
        Stime direzionali e varianze (diag=NaN, stesso asse).
    p_weights : pd.Series
        Pesi meta-blend p(B) su asse colonne.
    n_dir : pd.DataFrame
        Volumi direzionali W+L; OBS = (n_dir>0) off-diag.
    cfg : MARSConfig
        Usa Z_PENALTY.

    Returns
    -------
    out : pd.DataFrame
        Colonne: MAS_%, SE_%, LB_% (percentuali 0–100), indicizzate per Deck.
    """
    axis = list(p_hat.index)
    # Maschera di osservazione (off-diag)
    OBS = (n_dir.fillna(0.0) > 0.0)
    np.fill_diagonal(OBS.values, False)

    # Base weights replicati per riga
    W_base = pd.DataFrame(
        np.broadcast_to(p_weights.reindex(axis).values, (len(axis), len(axis))),
        index=axis, columns=axis
    )
    # Zeri dove non osservato
    W_masked = W_base.where(OBS, other=0.0)

    # Rinormalizzazione riga sulle sole colonne osservate
    row_sum = W_masked.sum(axis=1)
    W_norm = W_masked.div(row_sum.replace(0.0, np.nan), axis=0)

    rows_with_obs = OBS.sum(axis=1) > 0
    rows_need_uniform = rows_with_obs & (~np.isfinite(row_sum) | (row_sum <= 0.0))
    if rows_need_uniform.any():
        unif = OBS.loc[rows_need_uniform].astype(float)
        W_norm.loc[rows_need_uniform] = unif.div(unif.sum(axis=1), axis=0)

    # Nessuna osservazione: tutta la riga = NaN
    no_obs_rows = (~rows_with_obs)
    if no_obs_rows.any():
        W_norm.loc[no_obs_rows] = np.nan

    # MAS e var(MAS) vettoriali
    MAS = (W_norm * p_hat).sum(axis=1)
    VAR_MAS = (W_norm.pow(2) * var_hat).sum(axis=1)

    SE = VAR_MAS.clip(lower=0.0).pow(0.5)
    LB = MAS - cfg.Z_PENALTY * SE

    out = pd.DataFrame({
        "MAS_%": MAS * 100.0,
        "SE_%": SE * 100.0,
        "LB_%": LB * 100.0,
    }, index=axis)
    return out
