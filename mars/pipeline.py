from __future__ import annotations
import pandas as pd
from .config import MARSConfig
from .validate_io import validate_contract
from .meta import blend_meta
from .auto_k_cv import auto_k_cv
from .posterior import posterior_dir
from .mas_lb import mas_se_lb
from .bt import bt_soft
from .composite import compose
from .coverage import coverage_tables

def run_mars(filtered_wr: pd.DataFrame, n_dir: pd.DataFrame,
             score_flat: pd.DataFrame | None,
             top_meta_df: pd.DataFrame | None,
             cfg: MARSConfig) -> tuple[pd.DataFrame, dict, pd.DataFrame, pd.DataFrame]:
    """
    Orchestrazione MARS: validazione → pesi meta → AUTO-K → posteriori → MAS/LB → BT → composito.
    Ritorna: (mars_ranking, diag, coverage_df, missing_pairs_long).
    """
    # Validator minimo
    v = validate_contract(filtered_wr, n_dir)
    if not v.get("ok", False):
        raise ValueError(f"Contract validation failed: {v['issues']}")

    axis = list(filtered_wr.index)

    # Pesi meta
    p_weights, meta_info = blend_meta(axis, n_dir, top_meta_df, cfg)

    # Conteggi direzionali: per ora deriviamo da score_flat se fornito, altrimenti fallback (NO I/O qui)
    if score_flat is None or score_flat.empty:
        raise ValueError("score_flat (post-filtro) mancante: MARS richiede W/L reali per AUTO-K.")
    # Expect: Deck A/B, W, L
    S = score_flat.pivot_table(index="Deck A", columns="Deck B", values="W", aggfunc="sum").reindex(index=axis, columns=axis)
    F = score_flat.pivot_table(index="Deck A", columns="Deck B", values="L", aggfunc="sum").reindex(index=axis, columns=axis)
    N = (S.fillna(0.0) + F.fillna(0.0)).reindex(index=axis, columns=axis)

    # AUTO-K
    auto_k = auto_k_cv(S, F, N, cfg); K_used = float(auto_k["K_used"])

    # Posteriori + MAS/LB
    p_hat, var_hat = posterior_dir(S, F, K_used, cfg)
    mas_df = mas_se_lb(p_hat, var_hat, p_weights, N, cfg)

    # BT
    bt = bt_soft(axis, N, p_hat, K_used, cfg)
    bt_pct = bt["bt_pct"]

    # Composito
    score_pct = compose(mas_df["LB_%"], bt_pct, cfg.ALPHA_COMPOSITE)

    # Coverage/missing
    coverage_df, missing_pairs_long = coverage_tables(N, axis)

    # Assemble
    Opp_used  = (N.fillna(0.0)>0.0).sum(axis=1)
    Opp_total = len(axis) - 1
    Coverage  = (Opp_used / max(Opp_total,1)) * 100.0
    N_eff     = N.sum(axis=1, skipna=True)

    mars_ranking = pd.DataFrame({
        "Deck": axis,
        "Score_%": score_pct.values,
        "MAS_%": (mas_df["MAS_%"]).values,
        "LB_%": (mas_df["LB_%"]).values,
        "BT_%": bt_pct.values,
        "SE_%": (mas_df["SE_%"]).values,
        "N_eff": N_eff.values,
        "Opp_used": Opp_used.values,
        "Opp_total": int(Opp_total),
        "Coverage_%": Coverage.values,
    }).sort_values("Score_%", ascending=False).reset_index(drop=True)
    mars_ranking.index = mars_ranking.index + 1
    mars_ranking.index.name = "Rank"

    diag = {"AUTO_K": auto_k, "META": meta_info, "BT": bt.get("diag", {})}
    return mars_ranking, diag, coverage_df, missing_pairs_long
