from __future__ import annotations
import math
import numpy as np
import pandas as pd
from .config import MARSConfig

def _s_of(N: float, K: float) -> float:
    """Confidenza direzionale s = N/(N+K)."""
    return (float(N) / (float(N) + float(K))) if N > 0 else 0.0

def _hhi(vals: np.ndarray) -> float:
    tot = float(vals.sum())
    if not np.isfinite(tot) or tot <= 0:
        return float("nan")
    p = vals / tot
    return float(np.sum(p * p))

def bt_soft(
    axis: list[str],
    n_dir: pd.DataFrame,
    p_hat: pd.DataFrame,
    K_used: float,
    cfg: MARSConfig,
) -> dict:
    """
    Bradley–Terry robusto:
      - filtro adattivo su confidenza media s̄ ≥ s_min (s_min da N_min_target, K_used)
      - n_base armonica se entrambe le direzioni >0, altrimenti media osservata
      - soft-weight n_eff = n_base * s̄^γ con γ auto-continuo se cfg.BT_SOFT_POWER is None
      - stima MM con ridge (λ), normalizzazione media geometrica, mappa in [0,1] con sigmoide.

    Returns
    -------
    out : dict
      {
        "bt_pct": Series (0..100),
        "diag": {...},   # diagnostiche chiave
        "pairs": list[(A,B,n_eff,w_ij,w_ji)]
      }
    """
    # 1) Pre-selezione archi
    N_MIN = int(cfg.N_MIN_BT_TARGET)
    s_min = float(N_MIN) / (float(N_MIN) + float(K_used))

    info_kept: list[tuple[str,str,float,float,float,float,float]] = []
    edges_kept = edges_drop = 0
    sbar_list: list[float] = []

    for i, ai in enumerate(axis):
        for j in range(i + 1, len(axis)):
            aj = axis[j]
            Nij = float(n_dir.loc[ai, aj])
            Nji = float(n_dir.loc[aj, ai])
            if (Nij <= 0.0) and (Nji <= 0.0):
                continue

            s_ij = _s_of(Nij, K_used)
            s_ji = _s_of(Nji, K_used)
            s_vals = [v for v in (s_ij, s_ji) if v > 0]
            s_bar = (sum(s_vals) / len(s_vals)) if s_vals else 0.0
            if s_bar < s_min:
                edges_drop += 1
                continue

            p1 = p_hat.loc[ai, aj]
            p2 = p_hat.loc[aj, ai]
            if pd.notna(p1) and pd.notna(p2):
                p_bar = 0.5 * (float(p1) + (1.0 - float(p2)))
            elif pd.notna(p1):
                p_bar = float(p1)
            elif pd.notna(p2):
                p_bar = 1.0 - float(p2)
            else:
                edges_drop += 1
                continue

            if cfg.BT_USE_HARMONIC_N and (Nij > 0.0) and (Nji > 0.0):
                n_base = (2.0 * Nij * Nji) / (Nij + Nji)
            else:
                obs = [x for x in (Nij, Nji) if x > 0.0]
                n_base = float(np.mean(obs)) if obs else 0.0

            sbar_list.append(s_bar)
            info_kept.append((ai, aj, Nij, Nji, n_base, s_bar, p_bar))
            edges_kept += 1

    if edges_kept == 0:
        # fallback
        bt_prob = pd.Series(0.5, index=axis, dtype=float)
        return {"bt_pct": bt_prob * 100.0,
                "diag": {"kept": 0, "dropped": edges_drop, "s_min": s_min},
                "pairs": []}

    # 2) Diagnostica & auto soft-power
    sbar_kept = np.asarray(sbar_list, dtype=float)
    near_mask = (sbar_kept >= s_min) & (sbar_kept < s_min + float(cfg.BT_NEAR_BAND))
    near_share = float(near_mask.mean())
    sbar_med = float(np.nanmedian(sbar_kept)) if sbar_kept.size else float("nan")

    lev_base = []
    for (ai, aj, Nij, Nji, n_base, s_bar, p_bar) in info_kept:
        lev_base.append(float(max(n_base, 1e-12) * abs(float(p_bar) - 0.5)))
    lev_base = np.asarray(lev_base, dtype=float)
    hhi_lev = _hhi(lev_base)

    deck_counts = {d: 0 for d in axis}
    for (ai, aj, *_rest) in info_kept:
        deck_counts[ai] += 1
        deck_counts[aj] += 1
    opp_counts = pd.Series(deck_counts)
    min_opp = int(opp_counts.min()) if len(opp_counts) else 0
    med_opp = float(opp_counts.median()) if len(opp_counts) else float("nan")

    # Soft-power γ
    if cfg.BT_SOFT_POWER is None:
        def _clip01(x: float) -> float:
            return float(np.clip(x, 0.0, 1.0))
        x1 = _clip01((near_share - 0.15) / 0.15)                                   # ↑ se molti edge al pelo
        x2 = _clip01((0.60 - (sbar_med if np.isfinite(sbar_med) else 0.60)) / 0.10) # ↑ se s̄_med bassa
        x3 = _clip01(((hhi_lev if np.isfinite(hhi_lev) else 0.10) - 0.10) / 0.05)   # ↑ se leva concentrata
        x4 = _clip01((8.0 - float(min_opp)) / 5.0)                                  # ↑ se pochi opp per qualche deck
        soft_power = float(np.clip(1.5 + 0.4*x1 + 0.2*x2 + 0.2*x3 + 0.1*x4, 1.5, 2.1))
        pow_mode = "auto-cont"
    else:
        soft_power = float(cfg.BT_SOFT_POWER)
        pow_mode = "set"

    # 3) Costruzione coppie e pesi soft
    pairs_bt: list[tuple[str, str, float, float, float]] = []
    for (ai, aj, Nij, Nji, n_base, s_bar, p_bar) in info_kept:
        p_bar = float(np.clip(float(p_bar), 1e-9, 1.0 - 1e-9))
        n_eff = float(max(float(n_base) * float(s_bar ** float(soft_power)), 1e-9))
        w_ij = float(p_bar * n_eff)
        w_ji = float((1.0 - p_bar) * n_eff)
        # sanity
        if abs((w_ij + w_ji) - n_eff) > 1e-6:
            raise AssertionError(f"w sum != n_eff for {ai} vs {aj}")
        if not (0.0 < w_ij < n_eff and 0.0 < w_ji < n_eff):
            raise AssertionError(f"w out of range for {ai} vs {aj}")
        pairs_bt.append((ai, aj, n_eff, w_ij, w_ji))

    # 4) Stima BT (MM con ridge)
    idx_map = {d: i for i, d in enumerate(axis)}
    wins_out = [0.0] * len(axis)
    opp_list: list[list[tuple[int, float]]] = [[] for _ in axis]
    for ai, aj, n_eff, w_ij, w_ji in pairs_bt:
        i = idx_map[ai]; j = idx_map[aj]
        wins_out[i] += w_ij
        wins_out[j] += w_ji
        opp_list[i].append((j, n_eff))
        opp_list[j].append((i, n_eff))

    pi = np.ones(len(axis), dtype=float)
    for _ in range(int(cfg.MAX_BT_ITER)):
        max_rel = 0.0
        new_pi = pi.copy()
        for i in range(len(axis)):
            s_i = wins_out[i]
            denom_i = 0.0
            pi_i = pi[i]
            for (j, n_eff) in opp_list[i]:
                denom_i += n_eff / (pi_i + pi[j] + 1e-12)
            upd = (s_i + float(cfg.LAMBDA_RIDGE)) / (denom_i + float(cfg.LAMBDA_RIDGE) + 1e-12)
            max_rel = max(max_rel, abs(upd - pi_i) / (pi_i + 1e-9))
            new_pi[i] = max(upd, 1e-8)
        pi = new_pi
        if max_rel < float(cfg.BT_TOL):
            break

    # Scala & map a [0,1]
    gmean = math.exp(np.log(pi).mean())
    pi = pi / (gmean + 1e-12)
    theta = np.log(pi)
    t_std = np.std(theta) if np.std(theta) > 0 else 1.0
    bt_prob = 1.0 / (1.0 + np.exp(-theta / t_std))
    bt_score = pd.Series(bt_prob, index=axis)

    diag = {
        "kept": edges_kept,
        "dropped": edges_drop,
        "near_thresh_pct": near_share * 100.0,
        "s_bar_median": sbar_med,
        "HHI_lev_base": hhi_lev,
        "min_opp": min_opp,
        "med_opp": med_opp,
        "BT_SOFT_POWER": soft_power,
        "BT_SOFT_POWER_mode": pow_mode,
        "s_min": s_min,
    }
    return {"bt_pct": bt_score * 100.0, "diag": diag, "pairs": pairs_bt}
