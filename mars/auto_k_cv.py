from __future__ import annotations
import numpy as np
import pandas as pd
from math import lgamma
from .config import MARSConfig

def _split_counts(W: int, L: int, rho: float) -> tuple[int, int, int, int]:
    """
    Deterministic split: test ≈ rho*N with min 2 in test when N>=4, and keep >=1 in train if N>1.
    Returns (Wtr, Ltr, Wte, Lte).
    """
    W = int(round(float(W))); L = int(round(float(L)))
    N = W + L
    if N <= 0:
        return 0, 0, 0, 0
    test_target = int(round(rho * N))
    if N >= 4:
        test_target = max(test_target, 2)
    test = min(max(1, test_target), max(0, N - 1))
    Wte = int(round(test * (W / N))) if N > 0 else 0
    Wte = max(0, min(Wte, W))
    Lte = test - Wte
    Lte = max(0, min(Lte, L))
    Wtr, Ltr = W - Wte, L - Lte
    if (Wtr + Ltr) <= 0 and N > 1:
        if Wte >= Lte and Wte > 0:
            Wte -= 1; Wtr += 1
        elif Lte > 0:
            Lte -= 1; Ltr += 1
    return Wtr, Ltr, Wte, Lte

def _logB(x: float, y: float) -> float:
    """log Beta via lgamma (stable)."""
    x = float(x); y = float(y)
    return lgamma(x) + lgamma(y) - lgamma(x + y)

def auto_k_cv(
    S_dir: pd.DataFrame,
    F_dir: pd.DataFrame,
    N_dir: pd.DataFrame,
    cfg: MARSConfig,
) -> dict:
    """
    AUTO-K-CV: choose a single K for all pairs by maximizing OOF predictive log-likelihood
    of a Beta-Binomial with prior Beta(mu*K, (1-mu)*K).
    Strategy:
      - Build a log-spaced grid around beta_auto = sqrt(median(N) * p75(N)) on observed cells.
      - Deterministic train/test split per cell (rho=cfg.RHO_TEST, with min 2 in test if N>=4).
      - Evaluate LL on held-out counts; tie-break to smallest K within REL_TOL.
      - Light bootstrap (cfg.BOOT_N) on cells to stabilize; boundary override if strongly supported.
      - Clip to prudent range relative to beta_auto (×[1/4, 4]).
    Returns a dict with grid, K_star, K_used, reason, diagnostics.
    """
    axis = list(S_dir.index)
    S_np = S_dir.reindex(index=axis, columns=axis).to_numpy(dtype=float)
    F_np = F_dir.reindex(index=axis, columns=axis).to_numpy(dtype=float)
    N_np = N_dir.reindex(index=axis, columns=axis).fillna(0.0).to_numpy(dtype=float)

    # observed off-diagonal cells
    mask_obs = (N_np > 0.0)
    np.fill_diagonal(mask_obs, False)
    W_vec = S_np[mask_obs].ravel()
    L_vec = F_np[mask_obs].ravel()
    N_vec = N_np[mask_obs].ravel()
    if W_vec.size == 0:
        raise RuntimeError("AUTO_K-CV: no observed cells (N_dir>0).")

    # scale
    N_med = float(np.nanmedian(N_vec))
    N_75  = float(np.nanpercentile(N_vec, 75))
    beta_auto = float(np.sqrt(max(N_med, 1.0) * max(N_75, 1.0)))

    # grid around beta_auto
    k_lo_user, k_hi_user = cfg.K_CONST_BOUNDS
    grid_raw = np.array([0.25, 0.5, 1.0, 2.0, 4.0], dtype=float) * beta_auto
    K_grid = np.clip(grid_raw, max(k_lo_user, cfg.K_MIN), k_hi_user)
    K_grid = np.unique(K_grid)

    # precompute splits
    splits = [_split_counts(int(w), int(l), cfg.RHO_TEST) for (w, l) in zip(W_vec, L_vec)]
    idx_used = [i for i, (Wtr, Ltr, Wte, Lte) in enumerate(splits) if (Wte + Lte) > 0]
    if not idx_used:
        raise RuntimeError("AUTO_K-CV: after split, no trials remain in test.")

    MU = float(cfg.MU)

    def ll_pred_total(K: float, idx_subset: list[int] | None = None) -> float:
        if not (K > 0.0 and 0.0 < MU < 1.0):
            return -np.inf
        a0 = MU * K
        b0 = (1.0 - MU) * K
        if a0 <= 0.0 or b0 <= 0.0:
            return -np.inf
        total = 0.0
        it = idx_used if idx_subset is None else idx_subset
        for i in it:
            Wtr, Ltr, Wte, Lte = splits[i]
            if Wte + Lte <= 0:
                continue
            alpha = a0 + Wtr
            beta  = b0 + Ltr
            total += _logB(Wte + alpha, Lte + beta) - _logB(alpha, beta)
        return float(total)

    # evaluate grid
    LL = np.array([ll_pred_total(K) for K in K_grid], dtype=float)
    best_idx = int(np.nanargmax(LL))
    LL_max = float(LL[best_idx])

    # tie-break to smallest K within tolerance
    rel_tol = float(cfg.REL_TOL_LL)
    cands = [i for i, v in enumerate(LL) if (LL_max - float(v)) <= rel_tol * max(1.0, abs(LL_max))]
    best_idx = int(min(cands))
    K_star = float(K_grid[best_idx])

    # auto-expand downward if latched at the minimum (up to 2 steps)
    floorK = max(k_lo_user, cfg.K_MIN)
    expansions = 0
    while best_idx == 0 and (K_grid.min() > floorK + 1e-12) and expansions < 2:
        K_ext = float(max(K_grid.min() / 2.0, floorK))
        K_grid = np.unique(np.concatenate([[K_ext], K_grid]))
        LL = np.array([ll_pred_total(K) for K in K_grid], dtype=float)
        best_idx = int(np.nanargmax(LL))
        LL_max = float(LL[best_idx])
        cands = [i for i, v in enumerate(LL) if (LL_max - float(v)) <= rel_tol * max(1.0, abs(LL_max))]
        best_idx = int(min(cands))
        K_star = float(K_grid[best_idx])
        expansions += 1

    # ΔLL/100 vs baseline (K = beta_auto clipped to grid)
    K_base = float(np.clip(beta_auto, K_grid.min(), K_grid.max()))
    LL_star = float(LL[best_idx])
    LL_base = float(ll_pred_total(K_base))
    N_test_tot = float(sum((splits[i][2] + splits[i][3]) for i in idx_used))
    dLL_per100 = (100.0 * (LL_star - LL_base) / N_test_tot) if N_test_tot > 0 else 0.0

    # light bootstrap over cells, deterministic seed
    rng = np.random.default_rng(int(cfg.SEED))
    local_grid = np.unique(np.clip(
        np.array([K_star / np.sqrt(2.0), K_star, K_star * np.sqrt(2.0)], dtype=float),
        K_grid.min(), K_grid.max()
    ))

    def argmax_smallest(vals: dict[float, float]) -> float:
        items = sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))
        return float(items[0][0])

    K_boot = []
    for _ in range(int(cfg.BOOT_N)):
        idx_sample = rng.integers(low=0, high=len(idx_used), size=len(idx_used))
        subset = [idx_used[j] for j in idx_sample]
        vals = {float(K): ll_pred_total(float(K), idx_subset=subset) for K in local_grid}
        K_boot.append(argmax_smallest(vals))
    K_boot = np.array(K_boot, dtype=float)

    # boundary / stability rules
    at_boundary = (abs(K_star - K_grid.min()) <= 1e-12) or (abs(K_star - K_grid.max()) <= 1e-12)
    boot_med = float(np.median(K_boot))
    boot_iqr = float(np.percentile(K_boot, 75) - np.percentile(K_boot, 25))
    mode_freq = float(np.mean(np.isclose(K_boot, K_star, rtol=0.0, atol=1e-12)))
    strong_boundary = at_boundary and (mode_freq >= 0.80) and (boot_med > 0) and ((boot_iqr / boot_med) <= 0.05)

    beta_lo, beta_hi = (beta_auto / 4.0), (beta_auto * 4.0)
    clip_lo = max(K_grid.min(), cfg.K_MIN, k_lo_user)
    clip_hi = min(K_grid.max(), k_hi_user, beta_hi)

    if strong_boundary:
        K_used = float(np.clip(K_star, clip_lo, clip_hi))
        reason = "best-boundary-override"
    elif at_boundary or (boot_med > 0 and (boot_iqr / boot_med) > 0.35) or (mode_freq < 0.50):
        clip_lo_safe = max(beta_lo, clip_lo)
        K_used = float(np.clip(boot_med, clip_lo_safe, clip_hi))
        reason = "boot-clipped"
    else:
        K_used = float(np.clip(K_star, clip_lo, clip_hi))
        reason = "best"

    # shrink diagnostics
    with np.errstate(divide='ignore', invalid='ignore'):
        r_vals = K_used / (N_vec + K_used)
    r_p10 = float(np.nanpercentile(r_vals, 10))
    r_p50 = float(np.nanpercentile(r_vals, 50))
    r_p90 = float(np.nanpercentile(r_vals, 90))
    Q25_N = float(np.nanpercentile(N_vec, 25))
    r_small_med = float(np.nanmedian(r_vals[N_vec <= Q25_N])) if np.isfinite(Q25_N) else float("nan")

    return {
        "K_grid": np.round(K_grid, 6).tolist(),
        "K_star": K_star,
        "K_used": K_used,
        "reason": reason,
        "beta_auto": beta_auto,
        "N_med": float(np.median(N_vec)),
        "N_75": float(np.percentile(N_vec, 75)),
        "delta_ll_100": dLL_per100,
        "r_p10": r_p10, "r_p50": r_p50, "r_p90": r_p90, "r_small_med": r_small_med,
        "expansions": int(expansions),
    }
