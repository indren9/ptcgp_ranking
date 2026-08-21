# MARS Explained

MARS stands for **Meta-Adjusted, Regularized Score**. It ranks decks by combining
performance against the observed meta with a regularized estimate of intrinsic
matchup strength.

## Inputs

The MARS pipeline expects aligned matchup data:

- `winrate_matrix` / `filtered_wr`: directional win rate matrix `A -> B`, in
  percent, with mirror cells on the diagonal set to `NaN`.
- `match_count_matrix` / `n_dir`: directional match volume `W + L`, aligned to
  the same axis.
- `matchup_scores_latest.csv`: flat matchup table with `Deck A`, `Deck B`, `W`,
  `L`, `T`, `N`, and `WR_dir`.
- Optional top-meta data used to build meta weights.

Ties are excluded from directional win-rate estimates by default. Mirror rows
are not used in MAS.

## Step 1: Bayesian Pair Smoothing

For each observed directed pair, MARS applies a Beta-Binomial posterior with
prior mean `mu = 0.5` and global strength `K`:

```text
a = W + mu * K
b = L + (1 - mu) * K
p_hat = (W + mu * K) / (N + K)
Var[p_hat] = (a * b) / ((a + b)^2 * (a + b + 1))
```

Equivalently:

```text
p_hat = (K / (N + K)) * 0.5 + (N / (N + K)) * (W / N)
```

Low-volume matchups shrink toward 50%; high-volume matchups stay close to the
observed rate.

## Step 2: AUTO_K-CV

When enabled, `AUTO_K` selects one global `K` from the data using out-of-fold
predictive log likelihood. The selected value controls posterior shrinkage for
MAS, SE, LB, and Bradley-Terry preprocessing.

The diagnostics report the searched grid, selected `K`, bootstrap summary, and
shrinkage quantiles.

## Step 3: Meta Weights

MARS blends two opponent distributions:

```text
p(B) = (1 - gamma) * p_meta(B) + gamma * p_enc(B)
```

- `p_meta`: share from top-meta data.
- `p_enc`: encounter share inferred from matchup volumes.

For each deck row, weights are renormalized over observed opponents only.

## Step 4: MAS, SE, and LB

For each deck `A`:

```text
MAS(A) = sum_B p(B) * p_hat(A -> B)
SE(A)  = sqrt(sum_B p(B)^2 * Var[p_hat(A -> B)])
LB(A)  = MAS(A) - z * SE(A)
```

`MAS` estimates expected performance against the weighted meta. `SE` captures
uncertainty. `LB` is the conservative score used by the final composite.

## Step 5: Bradley-Terry

MARS also fits a regularized Bradley-Terry model over the matchup graph. It uses
adaptive filtering, soft edge weights, harmonic volume handling when both
directions exist, and ridge regularization.

The output is scaled to `BT_%`, a second signal that captures transitive
strength across the matchup network.

## Step 6: Final Composite

MARS standardizes `LB` and `BT_%`, blends them, and maps the result to a
percentage-like score:

```text
z_comp = alpha * z(LB) + (1 - alpha) * z(BT)
Score_% = 100 * Phi(z_comp)
```

This is the mapping implemented in `mars/composite.py`. Earlier drafts used
`Phi(z_comp / sqrt(2))`; that is not the behavior of the current code or of the
published example. The mapped value is a percentile-like composite score, not
a matchup win probability.

Default tie-break order:

1. `LB_%`
2. `BT_%`
3. effective volume / coverage

## Output Columns

Typical ranking columns:

- `Deck`
- `Score_%`
- `MAS_%`
- `LB_%`
- `BT_%`
- `SE_%`
- `N_eff`
- `Opp_used`
- `Opp_total`
- `Coverage_%`

## How To Read The Ranking

- High `BT_%` and low `MAS_%`: strong deck with unfavorable current meta.
- High `MAS_%` and low `BT_%`: deck performing well into the current observed
  field, but with weaker transitive strength.
- `LB_%` much lower than `MAS_%`: high uncertainty.
- Low coverage: interpret the rank more cautiously.

## Shipped Profile Defaults

```yaml
MU: 0.5
Z_PENALTY: 1.96
ALPHA_COMPOSITE: 0.72
AUTO_K: true
GAMMA_META_BLEND: 0.30
META_GAP_POLICY: encounter
N_MIN_BT_TARGET: 5
BT_SOFT_POWER: null
BT_USE_HARMONIC_N: true
LAMBDA_RIDGE: 1.5
```

These defaults favor a stable but still discriminating ranking for incomplete
Limitless matchup data.

All four shipped YAML profiles use `Z_PENALTY: 1.96`. The `MARSConfig`
dataclass retains `1.2` as its fallback when code instantiates it without a
profile; public run manifests record the effective profile value so the two
contexts are not conflated.
