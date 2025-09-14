
# ──────────────────────────────────────────────────────────────────────────────
# scraper/decklist.py — Selenium scrape + mini cache of HTML + top-meta filter
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from bs4 import BeautifulSoup
import re
from pathlib import Path
import hashlib
import pandas as pd
from scraper.session import cache_is_fresh
from scraper.browser import chrome, safe_get, polite_sleep

import logging
log = logging.getLogger("ptcgp")
netlog = logging.getLogger("ptcgp.net")  # 👈

LIMITLESS_BASE_URL = "https://play.limitlesstcg.com"
LIMITLESS_DECKS_URL = f"{LIMITLESS_BASE_URL}/decks?game=POCKET"


def _decklist_cache_file(cache_dir: Path, url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"decks_{h}.html"


def scrape_decklist_html(url: str, *, cache_dir: Path, ttl_minutes: int, force_refresh: bool, headless: bool, wait_css_selector: str = "table") -> tuple[str, bool]:
    p = _decklist_cache_file(cache_dir, url)
    if not force_refresh and cache_is_fresh(p, ttl_minutes=ttl_minutes):
        netlog.debug("[cache hit] %s", url)  # 👈 invece di log.info(...)
        return p.read_text(encoding="utf-8", errors="ignore"), True
    # Selenium fetch
    with chrome(headless=headless) as driver:
        safe_get(driver, url, wait_css_selector=wait_css_selector, timeout=20)
        polite_sleep(5.0)
        html = driver.page_source
    p.write_text(html, encoding="utf-8")
    return html, False


def parse_decklist_table(html: str) -> pd.DataFrame:
    from bs4 import BeautifulSoup
    import pandas as pd

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("Nessuna tabella trovata nella pagina Decks")

    # scegli la tabella che ha un header con 'Deck'
    def _heads(t):
        return [th.get_text(" ", strip=True) for th in t.find_all("th")]

    table = None
    for t in tables:
        hs = [h.lower().strip() for h in _heads(t)]
        if any("deck" in h for h in hs):
            table = t
            break
    if table is None:
        table = tables[0]

    headers = _heads(table)
    tbody = table.find("tbody")
    rows = (tbody.find_all("tr") if tbody else table.find_all("tr")[1:]) or []

    data = []
    deck_idx = next((i for i, h in enumerate(headers) if "deck" in (h or "").lower()), None)

    from urllib.parse import urljoin
    for tr in rows:
        tds = tr.find_all("td")
        if not tds:
            continue
        vals = [td.get_text(" ", strip=True) for td in tds]

        # URL della cella 'Deck' (se presente)
        url_cell = None
        if deck_idx is not None and deck_idx < len(tds):
            a = tds[deck_idx].find("a", href=True)
            if a:
                href = a["href"]
                url_cell = href if href.startswith("http") else urljoin(LIMITLESS_BASE_URL.rstrip("/") + "/", href.lstrip("/"))

        vals.append(url_cell)
        data.append(vals)

    if not data:
        raise RuntimeError("Tabella vuota o non parsabile (decklist)")

    # colonne previste (aggiungi URL se non esiste già)
    cols = headers + (["URL"] if "URL" not in headers else [])

    # se ci sono più valori della lista colonne, rinomina le extra
    if len(cols) < len(data[0]):
        cols = cols + [f"extra_{i}" for i in range(len(data[0]) - len(cols))]

    df = pd.DataFrame(data, columns=cols)

    # ── PATCH: normalizza e rimuovi duplicati di colonna ──────────────────────
    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated(keep="first")]

    # helper rinomina “simile a”
    def _rename_like(df_in, candidates, new_name):
        for c in candidates:
            if c in df_in.columns:
                if new_name in df_in.columns and c != new_name:
                    # se la target esiste già, prima droppa la vecchia per evitare duplicati
                    df_in = df_in.drop(columns=[new_name])
                return df_in.rename(columns={c: new_name})
        return df_in

    # Rank
    if "Rank" not in df.columns:
        rank_candidates = [c for c in df.columns if c.lower() in ("rank", "#", "pos", "position", "placement")]
        if rank_candidates:
            df = _rename_like(df, [rank_candidates[0]], "Rank")
        else:
            # fallback: crea Rank sequenziale
            df.insert(0, "Rank", pd.RangeIndex(start=1, stop=len(df) + 1, step=1))

    # Deck
    if "Deck" not in df.columns:
        deck_alt = next((c for c in df.columns if "deck" in c.lower()), None)
        if deck_alt:
            df = df.rename(columns={deck_alt: "Deck"})
        else:
            raise KeyError("Colonna 'Deck' non trovata")

    # Share
    if "Share" not in df.columns:
        share_alt = next((c for c in df.columns if ("share" in c.lower()) or (c.strip() in {"%", "Share %", "Share%"})), None)
        df = df.rename(columns={share_alt: "Share"}) if share_alt else df.assign(Share=None)

    # Count
    if "Count" not in df.columns:
        count_alt = next((c for c in df.columns if ("count" in c.lower()) or ("players" in c.lower())), None)
        df = df.rename(columns={count_alt: "Count"}) if count_alt else df.assign(Count=None)

    # URL (se per qualche motivo ancora non c'è)
    if "URL" not in df.columns:
        maybe = next((c for c in df.columns if c.lower() == "url" or (isinstance(c, str) and c.startswith("extra_"))), None)
        df = df.rename(columns={maybe: "URL"}) if maybe and maybe != "URL" else df.assign(URL=None)

    # ── conversioni tipo robuste ──────────────────────────────────────────────
    # Rank
    df["Rank"] = pd.to_numeric(df["Rank"], errors="coerce")
    df["Rank"] = df["Rank"].round().astype("Int64")

    # Count
    if "Count" in df.columns:
        df["Count"] = pd.to_numeric(df["Count"], errors="coerce").astype("Int64")
    else:
        df["Count"] = pd.Series([pd.NA] * len(df), dtype="Int64")

    # output canonico
    out = df[["Rank", "Deck", "Share", "Count", "URL"]].set_index("Rank").sort_index()
    return out


def filter_top_meta(df_decklist: pd.DataFrame, *, threshold_pct: float) -> pd.DataFrame:
    if df_decklist is None or df_decklist.empty:
        raise ValueError("Decklist vuota")
    def parse_percent_series(s: pd.Series) -> pd.Series:
        return pd.to_numeric(
            s.astype(str)
             .str.replace("\xa0", "", regex=False)
             .str.replace("%", "", regex=False)
             .str.replace(",", ".", regex=False)
             .str.strip(),
            errors="coerce"
        )
    df = df_decklist.copy().reset_index()
    df["share"] = parse_percent_series(df["Share"]).fillna(0.0)
    df = df.sort_values("share", ascending=False, kind="mergesort").reset_index(drop=True)
    df["share_cum"] = df["share"].cumsum()
    pos = (df["share_cum"] >= float(threshold_pct)).idxmax() if (df["share_cum"] >= float(threshold_pct)).any() else len(df)-1
    top = df.iloc[:pos+1].copy()
    return top

