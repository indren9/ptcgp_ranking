# mars/report.py
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Iterable, Optional, Tuple
from pathlib import Path
import logging

import numpy as np
import pandas as pd

from utils.io import write_excel_versioned_styled  # lascia il writer
# NOTA: useremo un riordino robusto interno (non dipende più da utils.io.reorder_excel_sheets)

LOGGER = logging.getLogger("ptcgp")

_PCT = 100.0


def _sanitize_sheet_name(name: str) -> str:
    """Excel: max 31 char, no []:*?/\\ ."""
    bad = set('[]:*?/\\')
    safe = "".join(ch for ch in str(name) if ch not in bad).strip()
    if not safe:
        safe = "Sheet"
    return safe[:31]


def _ensure_axis(df: pd.DataFrame, axis: Iterable[str]) -> pd.DataFrame:
    axis = list(axis)
    return df.reindex(index=axis, columns=axis)


def _wr_real_from_score(score_flat: pd.DataFrame, axis: Iterable[str]) -> pd.DataFrame:
    """Matrice WR_real_% T×T (direzionale A→B) da score_latest (flat)."""
    need = {"Deck A", "Deck B", "WR_dir"}
    missing = need.difference(score_flat.columns)
    if missing:
        raise ValueError(f"score_flat mancano colonne: {sorted(missing)}")

    wr_pivot = (
        score_flat
        .astype({"Deck A": "string", "Deck B": "string"})
        .pivot(index="Deck A", columns="Deck B", values="WR_dir")
    )
    wr_pivot = _ensure_axis(wr_pivot, axis)
    np.fill_diagonal(wr_pivot.values, np.nan)
    return wr_pivot


def _counts_from_score(score_flat: pd.DataFrame, axis: Iterable[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Matrici W e L T×T da score_latest (flat)."""
    need = {"Deck A", "Deck B", "W", "L"}
    missing = need.difference(score_flat.columns)
    if missing:
        raise ValueError(f"score_flat mancano colonne: {sorted(missing)}")

    W = (
        score_flat.pivot(index="Deck A", columns="Deck B", values="W")
        .reindex(index=axis, columns=axis)
        .fillna(0.0)
        .astype(float)
    )
    L = (
        score_flat.pivot(index="Deck A", columns="Deck B", values="L")
        .reindex(index=axis, columns=axis)
        .fillna(0.0)
        .astype(float)
    )
    # Diagonale coerente
    np.fill_diagonal(W.values, np.nan)
    np.fill_diagonal(L.values, np.nan)
    return W, L


def _posterior_from_wr_n(
    wr_dir_pct: pd.DataFrame,
    n_dir: pd.DataFrame,
    *,
    mu: float,
    K: float,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Posteriori Beta su ogni cella A→B usando p_obs e N_dir:
      a = mu*K + W,  b = (1-mu)*K + L,  con W = p_obs*N_dir, L = (1-p_obs)*N_dir
    Restituisce:
      p_hat_% (T×T) e SE_dir_% (T×T) = 100*sqrt(Var[Beta(a,b)]).
    """
    if set(wr_dir_pct.columns) != set(n_dir.columns) or set(wr_dir_pct.index) != set(n_dir.index):
        raise ValueError("wr_dir_pct e n_dir non condividono lo stesso asse.")

    p_obs = wr_dir_pct / _PCT
    N = n_dir.astype(float)

    a = mu * K + (p_obs * N)
    b = (1.0 - mu) * K + ((1.0 - p_obs) * N)
    denom = a + b

    p_hat = a / denom
    var = (a * b) / (denom.pow(2) * (denom + 1.0))
    se = np.sqrt(var)

    return (p_hat * _PCT), (se * _PCT)


def _se_binom_from_wr_n(
    wr_dir_pct: pd.DataFrame,
    n_dir: pd.DataFrame,
) -> pd.DataFrame:
    """SE binomiale della proporzione osservata: 100·sqrt(p(1-p)/N_dir). NaN se N_dir==0."""
    p = wr_dir_pct / _PCT
    N = n_dir.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        se = np.sqrt(np.clip(p * (1.0 - p) / N, 0.0, None))
    return se * _PCT


def _weights_row_for_A(p_blend: pd.Series, A: str, axis: Iterable[str]) -> pd.Series:
    """Rinormalizza i pesi meta-blend su B≠A; somma=1."""
    axis = list(axis)
    w = p_blend.reindex(axis).astype(float).copy()
    w.loc[A] = 0.0
    s = w.sum()
    if s <= 0:
        n_opp = max(len(axis) - 1, 1)
        w = pd.Series({b: (0.0 if b == A else 1.0 / n_opp) for b in axis}, dtype=float)
    else:
        w /= s
    return w


def make_pairs_by_deck_tables(
    filtered_wr: pd.DataFrame,         # T×T, % direzionali A→B, diag NaN
    n_dir: pd.DataFrame,               # T×T, W+L direzionali, diag NaN
    p_blend: pd.Series,                # pesi meta-blend su asse (stessi della MAS)
    K_used: float,
    *,
    score_flat: Optional[pd.DataFrame] = None,  # se presente, WR_real_% e conteggi da qui
    mu: float = 0.5,
    include_posterior_se: bool = True,
    include_binom_se: bool = True,
    gamma: Optional[float] = None,
    include_counts: bool = True,            # aggiunge colonne W, L, N quando disponibili
    sort_by: Optional[str] = None,          # ignorato se global_order è passato
    global_order: Optional[Iterable[str]] = None,  # ordine righe fisso (es. ranking)
    include_self_row: bool = True,          # inserisce riga del deck A come "Mirror"
    mirror_label: str = "Mirror",
) -> Tuple["OrderedDict[str, pd.DataFrame]", pd.DataFrame, Dict]:
    """
    Costruisce:
      - sheets_by_deck: {A -> DataFrame (T righe se include_self_row=True altrimenti T−1)
                         con ordine righe fisso = global_order (se passato) altrimenti axis}
      - legend_df: foglio 00_Legenda (con sezione "Legenda colori")
      - meta: info utili a naming/tag/controlli
    """
    axis: list[str] = list(filtered_wr.columns)
    filtered_wr = filtered_wr.reindex(index=axis, columns=axis)
    n_dir = n_dir.reindex(index=axis, columns=axis)
    p_blend = p_blend.reindex(axis).astype(float)

    # WR reale e (opz) conteggi W/L
    if score_flat is not None:
        wr_real_pct = _wr_real_from_score(score_flat, axis)
        W_mat, L_mat = _counts_from_score(score_flat, axis) if include_counts else (None, None)
    else:
        wr_real_pct = filtered_wr.copy()
        W_mat, L_mat = (None, None)

    # Posteriori + SE posterior (Beta)
    p = wr_real_pct / 100.0
    N = n_dir.astype(float)
    a = mu * K_used + (p * N)
    b = (1.0 - mu) * K_used + ((1.0 - p) * N)
    denom = a + b
    p_hat_pct = (a / denom) * 100.0
    se_post_pct = np.sqrt((a * b) / (denom.pow(2) * (denom + 1.0))) * 100.0

    # SE binomiale
    with np.errstate(divide="ignore", invalid="ignore"):
        se_binom_pct = np.sqrt(np.clip(p * (1.0 - p) / N, 0.0, None)) * 100.0

    # Ordine fisso righe (uguale per tutti i fogli)
    order = list(global_order) if global_order is not None else list(axis)

    sheets: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
    for A in axis:
        rows = []
        w_row = _weights_row_for_A(p_blend, A, axis)
        for B in order:
            # Riga Mirror
            if B == A and include_self_row:
                row = {"Opponent": mirror_label}
                if include_counts and (W_mat is not None) and (L_mat is not None):
                    row.update({"W": pd.NA, "L": pd.NA, "N": pd.NA})
                # tutti vuoti/NaN e 0 per i pesi/contributi
                row.update({
                    "WR_real_%": np.nan,
                    "p_hat_%": np.nan,
                })
                if include_posterior_se: row["SE_dir_%"] = np.nan
                if include_binom_se:     row["SE_binom_%"] = np.nan
                row.update({"gap_pp": np.nan, "w_A(B)_%": 0.0, "MAS_contrib_pp": 0.0})
                rows.append(row)
                continue
            if B == A and not include_self_row:
                continue

            # Riga normale A→B
            row = {"Opponent": B}
            if include_counts and (W_mat is not None) and (L_mat is not None):
                Wv = W_mat.loc[A, B]; Lv = L_mat.loc[A, B]
                Nv = (Wv + Lv) if pd.notna(Wv) and pd.notna(Lv) else np.nan
                row.update({"W": Wv, "L": Lv, "N": Nv})

            wr_ab = wr_real_pct.loc[A, B]
            ph_ab = p_hat_pct.loc[A, B]
            row.update({"WR_real_%": wr_ab, "p_hat_%": ph_ab})
            if include_posterior_se: row["SE_dir_%"] = se_post_pct.loc[A, B]
            if include_binom_se:     row["SE_binom_%"] = se_binom_pct.loc[A, B]

            wB = float(w_row.loc[B]) * 100.0
            row.update({"gap_pp": (ph_ab - wr_ab), "w_A(B)_%": wB, "MAS_contrib_pp": (wB * ph_ab / 100.0)})
            rows.append(row)

        df = pd.DataFrame(rows)

        # Se non usiamo global_order, permettiamo sort_by per-foglio
        if global_order is None and sort_by and sort_by in df.columns:
            df = df.sort_values(by=sort_by, ascending=False, kind="mergesort").reset_index(drop=True)

        # Tipi/rounding
        for col in df.columns:
            if col in ("W", "L", "N"):
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            elif col != "Opponent":
                df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

        sheets[A] = df

    # Legend + sezione "Legenda colori" (colonna 'Colore' riempita dallo styled writer)
    legend_rows = [
        ("Che cos'è", "Workbook con 1 foglio per deck A; righe = tutti i deck nell'ordine fisso (ranking)."),
        ("Unità", "Percentuali con 2 decimali; _pp = punti percentuali; W/L/N sono conteggi direzionali."),
        ("W,L,N", "W=wins, L=losses, N=W+L (direzionali A→B, da score_latest)."),
        ("WR_real_%", "Winrate osservata post-alias/post-filtro (score_latest), 100·W/(W+L)."),
        ("p_hat_%", "Posteriore Beta-Binomiale con μ=0.5 e K_used: 100·(W+μK)/(W+L+K)."),
        ("SE_dir_%", "Deviazione standard (in %) del posteriore Beta; definita anche con N piccolo."),
        ("SE_binom_%", "SE frequentista: 100·sqrt(p(1−p)/N_dir) con p=WR_real_/100; NaN se N_dir=0."),
        ("gap_pp", "p_hat_% − WR_real_% (quanto corregge lo smoothing)."),
        ("w_A(B)_%", "Peso di B nella MAS di A (pesi meta-blend rinormalizzati su B≠A, somma≈100)."),
        ("MAS_contrib_pp", "100·w_A(B)·p̂(A→B); la somma per foglio ricostruisce MAS_% di A."),
        ("Mirror", "La riga del deck A è marcata come 'Mirror' (campi vuoti)."),
    ]
    legend_df = pd.DataFrame(legend_rows, columns=["Campo", "Descrizione"])
    legend_df.loc[len(legend_df)] = ["Parametri run", f"T={len(axis)}; mu={mu}; K_used={K_used}" + (f"; gamma={gamma}" if gamma is not None else "")]
    legend_df.loc[len(legend_df)] = ["Convenzioni", "Ordine righe fisso per tutti i fogli."]

    # Legenda colori (colonna 'Colore' valorizzata a testo, il writer la colora)
    if "Colore" not in legend_df.columns:
        legend_df["Colore"] = pd.NA
    legend_colors = pd.DataFrame(
        [
            {"Campo": "|gap_pp| ≥ 8",          "Descrizione": "Criticità alta",        "Colore": "RED"},
            {"Campo": "4 ≤ |gap_pp| < 8",      "Descrizione": "Criticità media",       "Colore": "YELLOW"},
            {"Campo": "Top-K MAS_contrib_pp",  "Descrizione": "Top contributori (K=5)","Colore": "GREEN"},
            {"Campo": "Mirror",                "Descrizione": "Riga del deck stesso",  "Colore": "GRAY"},
        ],
        columns=["Campo", "Descrizione", "Colore"],
    )
    legend_df = pd.concat(
        [legend_df,
         pd.DataFrame([{"Campo": "", "Descrizione": "Legenda colori", "Colore": pd.NA}]),
         legend_colors],
        ignore_index=True
    )

    meta = {
        "T": len(axis),
        "K_used": float(K_used),
        "mu": float(mu),
        "gamma": (None if gamma is None else float(gamma)),
        "axis": axis,
        "global_order": order,
        "include_self_row": include_self_row,
    }
    return sheets, legend_df, meta


def build_summary_sheet(ranking_df: pd.DataFrame) -> pd.DataFrame:
    """
    Costruisce il foglio 01_Summary dal ranking MARS:
      colonne chiave: Deck, Score_%, MAS_%, LB_%, BT_%, SE_%, N_eff, Opp_used, Coverage_%
    Ordinate per Score_% decrescente.
    """
    keep_cols = [
        "Deck", "Score_%", "MAS_%", "LB_%", "BT_%", "SE_%",
        "N_eff", "Opp_used", "Opp_total", "Coverage_%"
    ]
    cols = [c for c in keep_cols if c in ranking_df.columns]
    df = (ranking_df.loc[:, cols]
          .copy()
          .sort_values("Score_%", ascending=False, kind="mergesort")
          .reset_index(drop=True))
    # Rounding leggero
    for c in ("Score_%", "MAS_%", "LB_%", "BT_%", "SE_%", "Coverage_%"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").round(2)
    return df


def prepare_workbook(
    sheets_by_deck: "OrderedDict[str, pd.DataFrame]",
    legend_df: pd.DataFrame,
    summary_df: Optional[pd.DataFrame] = None,
) -> "OrderedDict[str, pd.DataFrame]":
    """
    Inserisce '00_Legenda' come primo foglio e, se fornito, '01_Summary' come secondo.
    Poi un foglio per ogni deck A (nome sanificato).
    """
    workbook: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
    workbook["00_Legenda"] = legend_df.copy()
    if summary_df is not None:
        workbook["01_Summary"] = summary_df.copy()
    for deck, df in sheets_by_deck.items():
        workbook[_sanitize_sheet_name(deck)] = df
    return workbook


# ---------- NUOVO: riordino robusto direttamente sull'xlsx scritto ----------
def _reorder_excel_sheets_robust(excel_path: Path | str, desired_decks: list[str]) -> None:
    """
    Riordina i fogli dell'xlsx in modo robusto usando l'elenco 'desired_decks' (nomi deck originali).
    Gestisce differenze dovute a sanificazione, troncamento a 31 char e duplicazioni possibili.
    Mantiene '00_Legenda' primo e '01_Summary' secondo (se presenti).
    """
    from openpyxl import load_workbook

    p = Path(excel_path)
    wb = load_workbook(p)
    existing_titles = [ws.title for ws in wb.worksheets]
    title2ws = {ws.title: ws for ws in wb.worksheets}

    def sanitize(s: str) -> str:
        return _sanitize_sheet_name(s)

    # Obiettivo: mappare ogni desired → un titolo esistente, evitando riusi
    used = set()
    mapped_titles: list[str] = []

    # Prefissi fissi
    if "00_Legenda" in title2ws:
        mapped_titles.append("00_Legenda"); used.add("00_Legenda")
    if "01_Summary" in title2ws:
        mapped_titles.append("01_Summary"); used.add("01_Summary")

    # Candidati deck (sanificati)
    desired_san = [sanitize(d) for d in desired_decks]

    for d in desired_san:
        # 1) match esatto
        exact = [t for t in existing_titles if t == d and t not in used]
        if exact:
            mapped_titles.append(exact[0]); used.add(exact[0]); continue

        # 2) match con suffissi Excel (es. 'Name', 'Name1', 'Name (2)')
        #    prova startswith/strip numeri/parentesi
        starts = [t for t in existing_titles if t.startswith(d) and t not in used]
        if starts:
            mapped_titles.append(starts[0]); used.add(starts[0]); continue

        # 3) match "contenuto" case-insensitive (ultima spiaggia)
        lower = d.lower()
        contains = [t for t in existing_titles if (lower in t.lower()) and t not in used]
        if contains:
            mapped_titles.append(contains[0]); used.add(contains[0]); continue

        # Se non trovato, salta (comparirà in coda tra i rimanenti)
        LOGGER.warning("Reorder: nessun foglio trovato per '%s' (sanificato).", d)

    # Aggiungi fogli rimanenti non mappati per non perdere nulla
    for t in existing_titles:
        if t not in mapped_titles:
            mapped_titles.append(t)

    # Applica riordino
    ordered = [title2ws[t] for t in mapped_titles]
    wb._sheets = ordered  # uso intenzionale dell'attributo privato, standard in openpyxl per riordinare
    wb.save(p)


# === End-to-end: scrive l'Excel e riordina i fogli secondo il ranking ===
def write_pairs_by_deck_report(
    *,
    ranking_df: pd.DataFrame,          # deve avere almeno colonne: Deck, Score_%
    filtered_wr: pd.DataFrame,
    n_dir: pd.DataFrame,
    p_blend: pd.Series,
    K_used: float,
    score_flat: Optional[pd.DataFrame] = None,
    mu: float = 0.5,
    gamma: Optional[float] = None,
    include_posterior_se: bool = True,
    include_binom_se: bool = True,
    include_counts: bool = True,
    include_self_row: bool = True,
    out_dir: Path | str = "outputs/RankingData/MARS/Report",
    base_name: Optional[str] = None,   # default: pairs_by_deck_T{T}_MARS
) -> Tuple[Path, Path, Dict]:
    """
    Costruisce i fogli per-deck, aggiunge Legenda/Summary, scrive due file (versioned & latest)
    e poi RIORDINA i fogli Excel nel medesimo ordine del ranking (top→bottom), in modo robusto.
    Ritorna: (versioned_path, latest_path, meta)
    """
    # Ordine del ranking (top→bottom)
    if not {"Deck", "Score_%"} <= set(ranking_df.columns):
        raise ValueError("ranking_df deve contenere almeno le colonne: 'Deck' e 'Score_%'.")
    ranking_order: list[str] = (
        ranking_df.sort_values("Score_%", ascending=False, kind="mergesort")["Deck"].astype(str).tolist()
    )

    # Costruzione tabelle per-deck con ordine righe fissato = ranking
    sheets_by_deck, legend_df, meta = make_pairs_by_deck_tables(
        filtered_wr=filtered_wr,
        n_dir=n_dir,
        p_blend=p_blend,
        K_used=K_used,
        score_flat=score_flat,
        mu=mu,
        include_posterior_se=include_posterior_se,
        include_binom_se=include_binom_se,
        gamma=gamma,
        include_counts=include_counts,
        global_order=ranking_order,
        include_self_row=include_self_row,
    )

    # Summary dal ranking
    summary_df = build_summary_sheet(ranking_df)

    # Workbook con nomi fogli già sanificati
    workbook = prepare_workbook(sheets_by_deck, legend_df, summary_df)

    # Naming & scrittura
    T = meta.get("T", len(sheets_by_deck))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if base_name is None:
        base_name = f"pairs_by_deck_T{T}_MARS"

    versioned_path, latest_path = write_excel_versioned_styled(
        workbook,                 # dict: {sheet_name: DataFrame}
        base_dir=out_dir,
        base_name=base_name,
        style=True
    )

    # Riordino robusto (usa i nomi realmente presenti nell'xlsx)
    _reorder_excel_sheets_robust(versioned_path, ranking_order)
    _reorder_excel_sheets_robust(latest_path, ranking_order)

    LOGGER.info(
        "Report: riordinati i fogli per ranking (top→bottom) [robusto] | versioned=%s | latest=%s",
        versioned_path, latest_path
    )
    return Path(versioned_path), Path(latest_path), meta
