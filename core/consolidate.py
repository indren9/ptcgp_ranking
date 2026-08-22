# ──────────────────────────────────────────────────────────────────────────────
# core/consolidate.py - max-N per (A,B), tie warning + sum; aliases + flat A-B
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from typing import Dict, Tuple
import logging
import numpy as np
import pandas as pd
import unicodedata
from core.normalize import apply_alias_series

log = logging.getLogger("ptcgp")

REQUIRED_RAW = {"Deck A", "Deck B", "W", "L", "T"}


def _coerce_counts(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for c in ("W", "L", "T"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).clip(lower=0).astype("Int64")
    d["Deck A"] = d["Deck A"].astype(str).str.strip()
    d["Deck B"] = d["Deck B"].astype(str).str.strip()
    # Ground-truth N.
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")
    return d


def maxN_flat(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Keep the row with maximum N for each (A,B).

    If several rows share the same maximum N, log a warning and sum W/L/T; then
    recompute N and Winrate. Winrate here is informational W/N * 100, and it is
    later overwritten by the contractual directional WR.
    """
    if not REQUIRED_RAW.issubset(df_raw.columns):
        missing = REQUIRED_RAW - set(df_raw.columns)
        raise KeyError(f"Missing columns in df_matchup_raw: {missing}")

    d = _coerce_counts(df_raw)

    out_rows = []
    for (a, b), grp in d.groupby(["Deck A", "Deck B"], sort=False):
        if grp.empty:
            continue
        nmax = int(grp["N"].max())
        top = grp[grp["N"] == nmax]
        if len(top) == 1:
            r = top.iloc[0]
            out_rows.append({
                "Deck A": a, "Deck B": b,
                "W": int(r["W"]), "L": int(r["L"]), "T": int(r["T"]),
                "N": int(r["N"]),
                "Winrate": round((100.0 * float(r["W"]) / float(r["N"])) if int(r["N"])>0 else 0.0, 2)
            })
        else:
            log.debug("[Tie N] %s vs %s - %d rows with maximum N=%d - summing counts.", a, b, len(top), nmax)
            W = int(top["W"].sum())
            L = int(top["L"].sum())
            T = int(top["T"].sum())
            N = int(W + L + T)
            wr = round(100.0 * W / N, 2) if N > 0 else 0.0
            out_rows.append({"Deck A": a, "Deck B": b, "W": W, "L": L, "T": T, "N": N, "Winrate": wr})

    df_flat = pd.DataFrame(out_rows)
    if df_flat.empty:
        return df_flat
    # Sort by A and descending N.
    df_flat = df_flat.sort_values(["Deck A", "N"], ascending=[True, False], kind="mergesort").reset_index(drop=True)
    # Types.
    for c in ("W", "L", "T", "N"):
        df_flat[c] = df_flat[c].astype("Int64")
    df_flat["Winrate"] = df_flat["Winrate"].astype(float)
    return df_flat

def _norm_key(s: str) -> str:
    """Normalize for alias_index lookup."""
    return unicodedata.normalize("NFKC", str(s)).strip().casefold()


def _apply_alias_series(s: pd.Series, alias_index: Dict[str, str]) -> pd.Series:
    if not alias_index:
        return s.astype(str).str.strip()
    return s.astype(str).map(lambda x: alias_index.get(_norm_key(x), str(x).strip()))


def _enforce_directional_symmetry(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enforce directional symmetry for each unordered pair {A,B}.

    Pick the direction with the largest N_dir = W+L, tie-break on N=W+L+T and
    then lexicographic order, then create the mirrored row by swapping W/L.
    """
    if df.empty:
        return df.copy()

    d = df.copy()
    for c in ("W", "L", "T"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).astype("Int64")
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")
    d["N_dir"] = (d["W"] + d["L"]).astype("Int64")

    # Unordered key for {A,B}.
    pair_key = np.where(
        d["Deck A"] <= d["Deck B"],
        d["Deck A"] + "||" + d["Deck B"],
        d["Deck B"] + "||" + d["Deck A"],
    )
    d["_pair"] = pair_key

    rows = []
    for key, g in d.groupby("_pair", sort=False):
        # There can be two directions, but sometimes only one arrives.
        # Pick max N_dir, then total N, then lexicographic (A,B) order.
        g = g.copy()
        g["_tie_sort"] = list(
            zip(
                -g["N_dir"].astype(int),          # max first
                -g["N"].astype(int),              # then total N
                g["Deck A"].astype(str),          # finally deterministic
                g["Deck B"].astype(str),
            )
        )
        chosen = g.sort_values("_tie_sort").iloc[0]

        A = str(chosen["Deck A"])
        B = str(chosen["Deck B"])
        W = int(chosen["W"])
        L = int(chosen["L"])
        T = int(chosen["T"])
        N = int(chosen["N"])

        # Chosen A->B row plus mirrored B->A row.
        rows.append({"Deck A": A, "Deck B": B, "W": W, "L": L, "T": T, "N": N})
        rows.append({"Deck A": B, "Deck B": A, "W": L, "L": W, "T": T, "N": N})

    out = pd.DataFrame.from_records(rows)

    # Final coherent types plus directional Winrate (ties excluded).
    for c in ("W", "L", "T", "N"):
        out[c] = out[c].astype("Int64")

    denom = (out["W"] + out["L"]).astype("Int64")
    wr = np.where(denom > 0, 100.0 * out["W"].astype(float) / denom.astype(float), np.nan)
    out["Winrate"] = pd.Series(wr, index=out.index).round(2)

    # Contract column order and readable sorting.
    out = out[["Deck A", "Deck B", "W", "L", "T", "N", "Winrate"]]
    out = out.sort_values(["Deck A", "Deck B"], kind="mergesort").reset_index(drop=True)
    return out


def apply_alias_and_aggregate(df_flat: pd.DataFrame, alias_index: Dict[str, str]) -> pd.DataFrame:
    """
    1) Apply aliases to 'Deck A' and 'Deck B'.
    2) Remove mirrors (A==B).
    3) Aggregate on (Deck A, Deck B) to merge directional duplicates.
    4) Enforce directional symmetry for each {A,B} using max N_dir (W+L).
    5) Compute Winrate = 100 * W / (W+L), ties excluded.
    6) Return the contractual flat DataFrame with both directions and no diagonal.
    """
    if df_flat is None or df_flat.empty:
        return pd.DataFrame(columns=["Deck A", "Deck B", "W", "L", "T", "N", "Winrate"])

    d = df_flat.copy()

    # Normalize names and apply aliases.
    d["Deck A"] = _apply_alias_series(d["Deck A"], alias_index)
    d["Deck B"] = _apply_alias_series(d["Deck B"], alias_index)

    # Remove mirrors.
    d = d[d["Deck A"] != d["Deck B"]].copy()
    if d.empty:
        return pd.DataFrame(columns=["Deck A", "Deck B", "W", "L", "T", "N", "Winrate"])

    # Types and N.
    for c in ("W", "L", "T"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).astype("Int64")
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")

    # Aggregate on (A,B) to consolidate any directional duplicates.
    d = (
        d.groupby(["Deck A", "Deck B"], as_index=False, sort=False)[["W", "L", "T", "N"]]
         .sum()
    )

    # Enforce directional symmetry, creating both coherent directions.
    out = _enforce_directional_symmetry(d)

    # Lightweight audit: (A,B) and (B,A) should both exist and be coherent.
    # Any discrepancy is caught by the validator.
    return out

def build_score_table_filtered(
    df_flat_alias: pd.DataFrame,
    kept_axis: list[str],
    *,
    round_wr: int = 2,
    legacy_winrate_alias: bool = True,
    preserve_zero_evidence: bool = False,
) -> pd.DataFrame:
    """
    Build the post-alias, post-NaN-filter score table.

    Input:
      - df_flat_alias: output from apply_alias_and_aggregate (already aliased,
        no mirrors, directional aggregation done, symmetry enforced)
      - kept_axis: decks kept by the NaN filter (filtered_wr.index)

    Output (contratto):
      - DataFrame with only decks in kept_axis, no diagonal, both directions
        present, and columns: Deck A, Deck B, W, L, T, N, WR_dir
        (+ optional Winrate = WR_dir).
    """
    # Trivial case / empty axis.
    cols = ["Deck A", "Deck B", "W", "L", "T", "N", "WR_dir"]
    if legacy_winrate_alias:
        cols_with_legacy = cols + ["Winrate"]
    else:
        cols_with_legacy = cols

    if df_flat_alias is None or df_flat_alias.empty or not kept_axis or len(kept_axis) < 2:
        return pd.DataFrame(columns=cols_with_legacy)

    required = {"Deck A", "Deck B", "W", "L", "T"}
    missing = required - set(df_flat_alias.columns)
    if missing:
        raise KeyError(f"build_score_table_filtered: missing columns {missing}")

    kept_set = set(map(str, kept_axis))

    # 1) Strict filter on kept axis.
    d = df_flat_alias.copy()
    d = d[d["Deck A"].isin(kept_set) & d["Deck B"].isin(kept_set)].copy()
    if d.empty:
        return pd.DataFrame(columns=cols_with_legacy)

    # 2) Remove any residual diagonal for robustness.
    d = d[d["Deck A"] != d["Deck B"]].copy()
    if d.empty:
        return pd.DataFrame(columns=cols_with_legacy)

    # 3) Types plus ground-truth N.
    for c in ("W", "L", "T"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).clip(lower=0).astype("Int64")
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")

    # 4) Aggregate on (A,B) for idempotence.
    d = (
        d.groupby(["Deck A", "Deck B"], as_index=False, sort=False)[["W", "L", "T", "N"]]
         .sum()
    )

    # 5) Re-enforce coherent directional symmetry.
    d = _enforce_directional_symmetry(d)

    # 6) WR_dir = 100*W/(W+L). Legacy mode drops denom==0 rows.
    # Dense acquisition mode preserves them with WR_dir=NaN so zero-evidence
    # and tie-only pairs remain explicit on the kept axis.
    denom = (d["W"] + d["L"]).astype("Int64")
    wr = np.where(denom > 0, 100.0 * d["W"].astype(float) / denom.astype(float), np.nan)
    d["WR_dir"] = pd.Series(wr, index=d.index).round(int(round_wr))
    if not preserve_zero_evidence:
        d = d[denom > 0].copy()

    # 7) Deterministic sorting plus contract columns.
    out = d.sort_values(["Deck A", "Deck B"], kind="mergesort").reset_index(drop=True)
    out = out[["Deck A", "Deck B", "W", "L", "T", "N", "WR_dir"]]
    if legacy_winrate_alias:
        out["Winrate"] = out["WR_dir"]  # legacy compatibility alias

    return out

