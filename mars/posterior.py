from __future__ import annotations
import numpy as np
import pandas as pd
from .config import MARSConfig

def posterior_dir(
    S_dir: pd.DataFrame,
    F_dir: pd.DataFrame,
    K_used: float,
    cfg: MARSConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Posteriori Beta–Binomiali e varianza direzionale con prior Beta(mu*K, (1-mu)*K).

    Parameters
    ----------
    S_dir, F_dir : pd.DataFrame
        Matrici (asse identico) con conteggi W e L per direzione A→B (off-diag).
    K_used : float
        K unico selezionato da AUTO-K-CV.
    cfg : MARSConfig
        Configurazione (usa MU ed EPS).

    Returns
    -------
    p_hat : pd.DataFrame
        Stima p̂(A→B) = (W + mu*K) / (W + L + K) con diag=NaN.
    var_hat : pd.DataFrame
        Varianza della Beta posterior: (αβ)/((α+β)^2 * (α+β+1)) con diag=NaN.
    """
    axis = list(S_dir.index)

    # Matrice K costante, diag NaN
    K = np.full((len(axis), len(axis)), float(K_used), dtype=float)
    np.fill_diagonal(K, np.nan)

    # Prior
    A0 = cfg.MU * K
    B0 = (1.0 - cfg.MU) * K

    # Win/Loss come float, NaN→0
    S_np = S_dir.reindex(index=axis, columns=axis).to_numpy(dtype=float)
    F_np = F_dir.reindex(index=axis, columns=axis).to_numpy(dtype=float)
    S_np = np.nan_to_num(S_np, nan=0.0)
    F_np = np.nan_to_num(F_np, nan=0.0)

    # Posterior
    A_post = S_np + np.nan_to_num(A0, nan=0.0)
    B_post = F_np + np.nan_to_num(B0, nan=0.0)
    den = A_post + B_post

    with np.errstate(divide="ignore", invalid="ignore"):
        p_hat_np = A_post / den
        var_hat_np = (A_post * B_post) / (den ** 2 * (den + 1.0))

    p_hat = pd.DataFrame(p_hat_np, index=axis, columns=axis)
    var_hat = pd.DataFrame(var_hat_np, index=axis, columns=axis)
    for df_ in (p_hat, var_hat):
        np.fill_diagonal(df_.values, np.nan)

    return p_hat, var_hat
