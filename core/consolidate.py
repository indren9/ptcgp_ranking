# ──────────────────────────────────────────────────────────────────────────────
# core/consolidate.py — max-N per (A,B), tie→WARNING+somma; alias + flat A–B
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
    # N verità
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")
    return d


def maxN_flat(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Tieni per ogni (A,B) la riga con N massimo.
    Se più righe hanno lo **stesso N massimo**, WARNING e **somma** W/L/T; ricalcola N e Winrate.
    Winrate in questa fase è W/N * 100 (informativa), ma verrà sovrascritta nella flat contrattuale con WR direzionale.
    """
    if not REQUIRED_RAW.issubset(df_raw.columns):
        missing = REQUIRED_RAW - set(df_raw.columns)
        raise KeyError(f"Mancano colonne in df_matchup_raw: {missing}")

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
            log.warning("[Tie N] %s vs %s — %d righe con N massimo=%d — aggrego somme.", a, b, len(top), nmax)
            W = int(top["W"].sum())
            L = int(top["L"].sum())
            T = int(top["T"].sum())
            N = int(W + L + T)
            wr = round(100.0 * W / N, 2) if N > 0 else 0.0
            out_rows.append({"Deck A": a, "Deck B": b, "W": W, "L": L, "T": T, "N": N, "Winrate": wr})

    df_flat = pd.DataFrame(out_rows)
    if df_flat.empty:
        return df_flat
    # ordina per A e N desc
    df_flat = df_flat.sort_values(["Deck A", "N"], ascending=[True, False], kind="mergesort").reset_index(drop=True)
    # tipi
    for c in ("W", "L", "T", "N"):
        df_flat[c] = df_flat[c].astype("Int64")
    df_flat["Winrate"] = df_flat["Winrate"].astype(float)
    return df_flat

def _norm_key(s: str) -> str:
    """Normalizza per lookup alias_index."""
    return unicodedata.normalize("NFKC", str(s)).strip().casefold()


def _apply_alias_series(s: pd.Series, alias_index: Dict[str, str]) -> pd.Series:
    if not alias_index:
        return s.astype(str).str.strip()
    return s.astype(str).map(lambda x: alias_index.get(_norm_key(x), str(x).strip()))


def _enforce_directional_symmetry(df: pd.DataFrame) -> pd.DataFrame:
    """
    Impone simmetria direzionale per ogni coppia non ordinata {A,B}.
    Regola: scegli la direzione con N_dir = W+L maggiore (tie-break su N=W+L+T, poi lessicografico),
    poi crea la riga speculare scambiando W<->L (T e N uguali).
    """
    if df.empty:
        return df.copy()

    d = df.copy()
    for c in ("W", "L", "T"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).astype("Int64")
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")
    d["N_dir"] = (d["W"] + d["L"]).astype("Int64")

    # chiave non-ordinata per {A,B}
    pair_key = np.where(
        d["Deck A"] <= d["Deck B"],
        d["Deck A"] + "||" + d["Deck B"],
        d["Deck B"] + "||" + d["Deck A"],
    )
    d["_pair"] = pair_key

    rows = []
    for key, g in d.groupby("_pair", sort=False):
        # due possibili direzioni (A,B) e (B,A), ma talvolta ne arriva solo una
        # scegliamo la riga con N_dir max, poi N, poi ordine lessicografico di (A,B)
        g = g.copy()
        g["_tie_sort"] = list(
            zip(
                -g["N_dir"].astype(int),          # max first
                -g["N"].astype(int),              # poi N totale
                g["Deck A"].astype(str),          # infine deterministico
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

        # riga A->B (scelta) + riga speculare B->A
        rows.append({"Deck A": A, "Deck B": B, "W": W, "L": L, "T": T, "N": N})
        rows.append({"Deck A": B, "Deck B": A, "W": L, "L": W, "T": T, "N": N})

    out = pd.DataFrame.from_records(rows)

    # Tipi finali coerenti + Winrate direzionale (T esclusi)
    for c in ("W", "L", "T", "N"):
        out[c] = out[c].astype("Int64")

    denom = (out["W"] + out["L"]).astype("Int64")
    wr = np.where(denom > 0, 100.0 * out["W"].astype(float) / denom.astype(float), np.nan)
    out["Winrate"] = pd.Series(wr, index=out.index).round(2)

    # Ordine colonne come da contratto e sorting per leggibilità
    out = out[["Deck A", "Deck B", "W", "L", "T", "N", "Winrate"]]
    out = out.sort_values(["Deck A", "Deck B"], kind="mergesort").reset_index(drop=True)
    return out


def apply_alias_and_aggregate(df_flat: pd.DataFrame, alias_index: Dict[str, str]) -> pd.DataFrame:
    """
    1) Applica alias a 'Deck A' e 'Deck B'
    2) Rimuove mirror (A==B)
    3) Aggrega (somma) su (Deck A, Deck B) per unire eventuali duplicati direzionali
    4) Impone simmetria direzionale per ogni {A,B} con la regola del max N_dir (W+L)
    5) Calcola Winrate = 100 * W / (W+L) (T esclusi)
    6) Ritorna DF flat conforme al contratto (entrambe le direzioni presenti, niente diagonale)
    """
    if df_flat is None or df_flat.empty:
        return pd.DataFrame(columns=["Deck A", "Deck B", "W", "L", "T", "N", "Winrate"])

    d = df_flat.copy()

    # Normalizza nomi e applica alias
    d["Deck A"] = _apply_alias_series(d["Deck A"], alias_index)
    d["Deck B"] = _apply_alias_series(d["Deck B"], alias_index)

    # Rimuovi mirror
    d = d[d["Deck A"] != d["Deck B"]].copy()
    if d.empty:
        return pd.DataFrame(columns=["Deck A", "Deck B", "W", "L", "T", "N", "Winrate"])

    # Tipi & N
    for c in ("W", "L", "T"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).astype("Int64")
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")

    # Aggrega su (A,B) per consolidare eventuali duplicati direzionali
    d = (
        d.groupby(["Deck A", "Deck B"], as_index=False, sort=False)[["W", "L", "T", "N"]]
         .sum()
    )

    # Impone simmetria direzionale (crea entrambe le direzioni coerenti)
    out = _enforce_directional_symmetry(d)

    # Audit leggero
    # (A,B)+(B,A) devono essere presenti entrambi e coerenti
    # eventuali discrepanze si intercetteranno nel validatore
    return out

def build_score_table_filtered(
    df_flat_alias: pd.DataFrame,
    kept_axis: list[str],
    *,
    round_wr: int = 2,
    legacy_winrate_alias: bool = True
) -> pd.DataFrame:
    """
    Costruisce la score table post-alias e post-filtro NaN.

    Input:
      - df_flat_alias: output di apply_alias_and_aggregate (già alias, no mirror,
        aggregazione direzionale fatta e simmetria imposta)
      - kept_axis: lista dei deck mantenuti dal filtro NaN (filtered_wr.index)

    Output (contratto):
      - DataFrame con solo deck ∈ kept_axis, senza diagonale, entrambe le direzioni presenti,
        colonne: Deck A, Deck B, W, L, T, N, WR_dir (+ opzionale Winrate = WR_dir).
    """
    # Caso banale / asse vuoto
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
        raise KeyError(f"build_score_table_filtered: mancano colonne {missing}")

    kept_set = set(map(str, kept_axis))

    # 1) Filtro rigido su asse kept
    d = df_flat_alias.copy()
    d = d[d["Deck A"].isin(kept_set) & d["Deck B"].isin(kept_set)].copy()
    if d.empty:
        return pd.DataFrame(columns=cols_with_legacy)

    # 2) Rimuovi qualsiasi diagonale residua (per robustezza)
    d = d[d["Deck A"] != d["Deck B"]].copy()
    if d.empty:
        return pd.DataFrame(columns=cols_with_legacy)

    # 3) Tipi + N verità
    for c in ("W", "L", "T"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).clip(lower=0).astype("Int64")
    d["N"] = (d["W"] + d["L"] + d["T"]).astype("Int64")

    # 4) Aggrega su (A,B) per idempotenza
    d = (
        d.groupby(["Deck A", "Deck B"], as_index=False, sort=False)[["W", "L", "T", "N"]]
         .sum()
    )

    # 5) (Ri)impone simmetria direzionale coerente
    d = _enforce_directional_symmetry(d)

    # 6) WR_dir = 100*W/(W+L); droppa denom==0 (non dovrebbero esserci su asse kept)
    denom = (d["W"] + d["L"]).astype("Int64")
    wr = np.where(denom > 0, 100.0 * d["W"].astype(float) / denom.astype(float), np.nan)
    d["WR_dir"] = pd.Series(wr, index=d.index).round(int(round_wr))
    d = d[denom > 0].copy()

    # 7) Ordinamento deterministico + colonne contratto
    out = d.sort_values(["Deck A", "Deck B"], kind="mergesort").reset_index(drop=True)
    out = out[["Deck A", "Deck B", "W", "L", "T", "N", "WR_dir"]]
    if legacy_winrate_alias:
        out["Winrate"] = out["WR_dir"]  # alias legacy per compatibilità

    return out

