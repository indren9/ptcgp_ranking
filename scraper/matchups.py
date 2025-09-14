
# ──────────────────────────────────────────────────────────────────────────────
# scraper/matchups.py — /matchups URLs + HTML→DF + scrape loop
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from bs4 import BeautifulSoup
import re
from pathlib import Path
import pandas as pd
import requests  # solo per l'annotation requests.Session
import logging
log = logging.getLogger("ptcgp")

from scraper.session import fetch_html
from scraper.decklist import LIMITLESS_BASE_URL


def to_matchup_url(u: str | None) -> str | None:
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u:
        return None
    from urllib.parse import urlsplit, urlunsplit, urljoin
    s = urlsplit(u)
    if not s.scheme or not s.netloc:
        base = LIMITLESS_BASE_URL.rstrip("/") + "/"
        u_abs = urljoin(base, u.lstrip("/"))
        s = urlsplit(u_abs)
    path = s.path.rstrip("/")
    if not path.endswith("/matchups"):
        path = f"{path}/matchups"
    return urlunsplit((s.scheme, s.netloc, path, s.query, s.fragment))


def extract_matchups_from_html(html: str, deck_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    # select proper table
    def _select_table(soup):
        for t in soup.find_all("table"):
            heads_raw = [th.get_text(" ", strip=True) for th in t.find_all("th")]
            heads = [h.strip().lower() for h in heads_raw]
            has_deck = any("deck" in h for h in heads)
            has_matches = any("matches" in h for h in heads)
            has_score = any(("score" in h) or ("record" in h) for h in heads)
            has_wr = any(("win" in h and "%" in hr) or ("winrate" in h) for h, hr in zip(heads, heads_raw))
            if has_deck and has_matches and has_score and has_wr:
                return t
        return None
    table = _select_table(soup) or soup.find("table")
    if table is None:
        return []
    thead = table.find("thead")
    headers_raw = [th.get_text(" ", strip=True) for th in (thead.find_all("th") if thead else table.find_all("th"))]
    headers = [h.strip().lower() for h in headers_raw]
    def _idx(check):
        for i, (h, hr) in enumerate(zip(headers, headers_raw)):
            if check(h, hr):
                return i
        return None
    i_opp = _idx(lambda h, hr: "deck" in h)
    i_n = _idx(lambda h, hr: "matches" in h)
    i_rec = _idx(lambda h, hr: ("score" in h) or ("record" in h))
    i_wr = _idx(lambda h, hr: ("win" in h and "%" in hr) or ("winrate" in h))
    if None in (i_opp, i_n, i_rec, i_wr):
        return []
    tbody = table.find("tbody")
    rows = (tbody.find_all("tr") if tbody else table.find_all("tr")[1:]) or []
    out: list[dict] = []
    dash = r"[\-–—−]"
    for row in rows:
        cols = row.find_all(["td", "th"])
        if not cols:
            continue
        a = cols[i_opp].find("a")
        opp = (a.get_text(" ", strip=True) if a else cols[i_opp].get_text(" ", strip=True)) or "Unknown"
        # N
        digits = re.findall(r"\d+", cols[i_n].get_text(" ", strip=True) or "")
        n = int("".join(digits)) if digits else 0
        # record W-L(-T)
        m = re.search(rf"(\d+)\s*{dash}\s*(\d+)(?:\s*{dash}\s*(\d+))?", cols[i_rec].get_text(" ", strip=True) or "")
        if not m:
            w=l=t=0
        else:
            w, l, t = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        # WR (optional)
        wr_txt = cols[i_wr].get_text(" ", strip=True) or ""
        m2 = re.search(r"(\d+(?:\.\d+)?)", wr_txt.replace(",", "."))
        wr = float(m2.group(1)) if m2 else None
        # N consistency
        n_calc = w + l + t
        if n < n_calc:
            n = n_calc
        if wr is None:
            wr = (100.0 * w / n) if n > 0 else 0.0
        out.append({
            "Deck A": deck_name,
            "Deck B": opp,
            "W": w, "L": l, "T": t,
            "N": n,
            "Winrate": round(wr, 2)
        })
    return out


def scrape_matchups(
    urls: list[tuple[str, str]],
    *,
    session: requests.Session,
    cache_dir: Path,
    ttl_minutes: int = 720,
    force_refresh: bool = False,
    rate_limit_seconds: float = 5.0,
    progress: bool = False,                 # barra di progressione
    pbar_desc: str = "Scraping matchups",   # descrizione barra
) -> tuple[pd.DataFrame, int, int]:
    """
    urls: list of (deck_name, matchup_url)
    Returns: (df_raw, total_pages, cache_hits)
    """

    # --- deduplica URL mantenendo il primo deck_name visto ---
    seen: set[str] = set()
    dedup: list[tuple[str, str]] = []
    for deck_name, u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        dedup.append((deck_name, u))

    total = len(dedup)
    rows: list[dict] = []
    cache_hits = 0

    # --- iteratore con tqdm se richiesto ---
    use_pbar = False
    iterator = dedup
    if progress:
        try:
            from tqdm.auto import tqdm  # lazy import per non aggiungere dipendenze a runtime se non serve
            iterator = tqdm(dedup, total=total, desc=pbar_desc, leave=False, dynamic_ncols=True)
            use_pbar = True
        except Exception:
            use_pbar = False
            iterator = dedup

    # --- loop fetch + parse ---
    for deck_name, u in iterator:
        html, from_cache = fetch_html(
            u,
            session=session,
            cache_dir=cache_dir,
            ttl_minutes=ttl_minutes,
            force_refresh=force_refresh,
            rate_limit_seconds=rate_limit_seconds,
        )
        cache_hits += int(from_cache)
        rows.extend(extract_matchups_from_html(html, deck_name))

        # aggiornamento veloce della progress bar
        if use_pbar:
            try:
                iterator.set_postfix({"cache": cache_hits, "rows": len(rows)}, refresh=False)
            except Exception:
                pass

    # --- build DataFrame + tipi coerenti ---
    df = pd.DataFrame(rows)
    for c in ("W", "L", "T", "N"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("Int64")
    if "Winrate" in df.columns:
        df["Winrate"] = pd.to_numeric(df["Winrate"], errors="coerce").fillna(0.0)

    return df, total, cache_hits
