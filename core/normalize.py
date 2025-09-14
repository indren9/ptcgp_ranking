# =============================================
# PTCGP – Refactor D2/D3: core (consolidamento, alias) + matrici/filtri
# File da creare/riempire:
#   core/normalize.py
#   core/consolidate.py
#   core/matrices.py
#   core/nan_filter.py
#   _smoke_d2_d3.py  (driver di prova)
# Dipendenze: utils/io.py, scraper/* già installati in D1
# =============================================

# ──────────────────────────────────────────────────────────────────────────────
# core/normalize.py — normalizzazione etichette + alias_map.json
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from pathlib import Path
from typing import Dict, Iterable, Tuple
import json
import logging
import unicodedata
import pandas as pd

log = logging.getLogger("ptcgp")

# Normalizza per confronti robusti (NFKC + trim + collapse spazi + casefold)
def normalize_label(s: str) -> str:
    if s is None:
        return ""
    x = unicodedata.normalize("NFKC", str(s)).strip()
    x = " ".join(x.split())
    return x.casefold()


def load_alias_map(path: Path) -> Dict[str, list[str]]:
    if not path.exists():
        log.warning("alias_map.json non trovato in %s — uso mappa vuota.", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("alias_map.json non è un oggetto {canonico: [varianti]}.")
        # garantisci liste
        out = {}
        for k, v in data.items():
            out[str(k)] = list(v or [])
        return out
    except Exception as e:
        log.error("Impossibile caricare alias_map.json: %s", e)
        return {}


def build_alias_index(alias_map: Dict[str, list[str]]) -> Dict[str, str]:
    """Crea indice variant_norm -> canonico.
    In caso di collisione (stessa variante punta a canonici diversi) tiene il primo e logga WARNING.
    """
    idx: Dict[str, str] = {}
    for canon, variants in alias_map.items():
        pool = {canon, *(variants or [])}
        for name in pool:
            key = normalize_label(name)
            if key in idx and idx[key] != canon:
                log.warning("Alias collisione su '%s': '%s' vs '%s' → mantengo '%s'", key, idx[key], canon, idx[key])
                continue
            idx.setdefault(key, canon)
    return idx


def apply_alias_series(series: pd.Series, alias_index: Dict[str, str]) -> pd.Series:
    if not alias_index:
        return series.astype(str).str.strip()
    return series.astype(str).map(lambda x: alias_index.get(normalize_label(x), x)).astype(str)


def alias_coverage(series: pd.Series, alias_index: Dict[str, str]) -> float:
    if not alias_index:
        return 0.0
    sr = series.dropna().astype(str)
    hits = sr.map(lambda x: normalize_label(x) in alias_index).sum()
    tot = int(sr.size)
    cov = 100.0 * hits / tot if tot else 0.0
    log.info("Aliases: copertura %.1f%% (%d/%d)", cov, hits, tot)
    return cov


