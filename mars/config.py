from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass(frozen=True)
class MARSConfig:
    # --- Posterior / LB / Composite
    MU: float = 0.5
    Z_PENALTY: float = 1.2
    ALPHA_COMPOSITE: float = 0.72

    # --- META blend
    AUTO_GAMMA: bool = False
    GAMMA_META_BLEND: float = 0.30
    GAMMA_MIN: float = 0.10
    GAMMA_MAX: float = 0.60
    GAMMA_BASE: float = 0.10
    GAMMA_SLOPE: float = 1.5
    META_GAP_POLICY: str = "encounter"  # {'proportional','uniform','encounter'}

    # --- Ties (opzione, default OFF)
    HALF_TIES: bool = False
    HALF_TIES_WEIGHT: float = 0.5

    # --- AUTO-K / K (legacy knobs inclusi ma con nomi univoci)
    AUTO_K: bool = True
    K_MIN: float = 0.10
    K_CONST_BOUNDS: Tuple[float, float] = (0.05, 50.0)
    INSTANT_APPLY_K: bool = True
    K_BASE: float = 4.0
    K_SCALE: float = 2.0
    K_BASE_BOUNDS: Tuple[float, float] = (1.0, 12.0)
    K_SCALE_BOUNDS: Tuple[float, float] = (0.5, 3.0)

    # --- AUTO-K CV (tuning/robustezza) — Nomi CANONICI
    RHO_TEST: float = 1.0 / 3.0          # quota test per split proporzionale
    BOOT_N: int = 50                     # numero bootstrap di celle
    SEED: int = 42                       # seed deterministico
    REL_TOL_LL: float = 1e-3             # tie-break su LL (relative tol)
    EXPAND_LO_STEPS: int = 2             # espansioni di griglia verso il basso
    K_GRID_MULTS: Tuple[float, float, float, float, float] = (0.25, 0.5, 1.0, 2.0, 4.0)
    BOOT_LOCAL_MULTS: Tuple[float, float, float] = (0.7071067812, 1.0, 1.4142135624)
    MIN_TEST_IF_N_GE_4: int = 2          # min test quando N>=4
    MIN_TRAIN_IF_N_GT_1: int = 1         # lascia >=1 in train se N>1

    # --- BT
    N_MIN_BT_TARGET: int = 5
    BT_SOFT_POWER: Optional[float] = None   # None => auto-continuo
    BT_NEAR_BAND: float = 0.10
    BT_USE_HARMONIC_N: bool = True
    LAMBDA_RIDGE: float = 1.5
    MAX_BT_ITER: int = 500
    BT_TOL: float = 1e-6

    # --- Misc
    EPS: float = 1e-12
