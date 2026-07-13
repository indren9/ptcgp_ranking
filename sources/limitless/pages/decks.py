from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import pandas as pd
import requests
from bs4 import BeautifulSoup

from sources.limitless.browser import chrome, polite_sleep, safe_get
from sources.limitless.client import cache_is_fresh, fetch_html
from sources.limitless.constants import LIMITLESS_BASE_URL, LIMITLESS_DECKS_URL

netlog = logging.getLogger("ptcgp.net")


def _decklist_cache_file(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"decks_{digest}.html"


def scrape_decklist_html(
    url: str,
    *,
    cache_dir: Path,
    ttl_minutes: int,
    force_refresh: bool,
    headless: bool,
    wait_css_selector: str = "table",
) -> tuple[str, bool]:
    cache_path = _decklist_cache_file(cache_dir, url)
    if not force_refresh and cache_is_fresh(cache_path, ttl_minutes=ttl_minutes):
        netlog.debug("[cache hit] %s", url)
        return cache_path.read_text(encoding="utf-8", errors="ignore"), True

    with chrome(headless=headless) as driver:
        safe_get(driver, url, wait_css_selector=wait_css_selector, timeout=20)
        polite_sleep(5.0)
        html = driver.page_source

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(html, encoding="utf-8")
    return html, False


def to_matchup_url(u: str | None) -> str | None:
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u:
        return None
    split = urlsplit(u)
    if not split.scheme or not split.netloc:
        base = LIMITLESS_BASE_URL.rstrip("/") + "/"
        u = urljoin(base, u.lstrip("/"))
        split = urlsplit(u)
    path = split.path.rstrip("/")
    if not path.endswith("/matchups"):
        path = f"{path}/matchups"
    return urlunsplit((split.scheme, split.netloc, path, split.query, split.fragment))


def parse_decklist_table(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("No table found on the Decks page")

    def _heads(table):
        return [th.get_text(" ", strip=True) for th in table.find_all("th")]

    table = None
    for candidate in tables:
        heads = [h.lower().strip() for h in _heads(candidate)]
        if any("deck" in h for h in heads):
            table = candidate
            break
    if table is None:
        table = tables[0]

    headers = _heads(table)
    tbody = table.find("tbody")
    rows = (tbody.find_all("tr") if tbody else table.find_all("tr")[1:]) or []
    deck_idx = next((i for i, h in enumerate(headers) if "deck" in (h or "").lower()), None)

    data = []
    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue
        vals = [td.get_text(" ", strip=True) for td in cells]

        url_cell = None
        if deck_idx is not None and deck_idx < len(cells):
            anchor = cells[deck_idx].find("a", href=True)
            if anchor:
                href = anchor["href"]
                url_cell = href if href.startswith("http") else urljoin(LIMITLESS_BASE_URL.rstrip("/") + "/", href.lstrip("/"))

        vals.append(url_cell)
        data.append(vals)

    if not data:
        raise RuntimeError("Tabella vuota o non parsabile (decklist)")

    cols = headers + (["URL"] if "URL" not in headers else [])
    if len(cols) < len(data[0]):
        cols = cols + [f"extra_{i}" for i in range(len(data[0]) - len(cols))]

    df = pd.DataFrame(data, columns=cols)
    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated(keep="first")]

    def _rename_like(df_in, candidates, new_name):
        for column in candidates:
            if column in df_in.columns:
                if new_name in df_in.columns and column != new_name:
                    df_in = df_in.drop(columns=[new_name])
                return df_in.rename(columns={column: new_name})
        return df_in

    if "Rank" not in df.columns:
        rank_candidates = [c for c in df.columns if c.lower() in ("rank", "#", "pos", "position", "placement")]
        if rank_candidates:
            df = _rename_like(df, [rank_candidates[0]], "Rank")
        else:
            df.insert(0, "Rank", pd.RangeIndex(start=1, stop=len(df) + 1, step=1))

    if "Deck" not in df.columns:
        deck_alt = next((c for c in df.columns if "deck" in c.lower()), None)
        if deck_alt:
            df = df.rename(columns={deck_alt: "Deck"})
        else:
            raise KeyError("Colonna 'Deck' non trovata")

    if "Share" not in df.columns:
        share_alt = next((c for c in df.columns if ("share" in c.lower()) or (c.strip() in {"%", "Share %", "Share%"})), None)
        df = df.rename(columns={share_alt: "Share"}) if share_alt else df.assign(Share=None)

    if "Count" not in df.columns:
        count_alt = next((c for c in df.columns if ("count" in c.lower()) or ("players" in c.lower())), None)
        df = df.rename(columns={count_alt: "Count"}) if count_alt else df.assign(Count=None)

    if "URL" not in df.columns:
        maybe = next((c for c in df.columns if c.lower() == "url" or (isinstance(c, str) and c.startswith("extra_"))), None)
        df = df.rename(columns={maybe: "URL"}) if maybe and maybe != "URL" else df.assign(URL=None)

    df["Rank"] = pd.to_numeric(df["Rank"], errors="coerce")
    df["Rank"] = df["Rank"].round().astype("Int64")

    if "Count" in df.columns:
        df["Count"] = pd.to_numeric(df["Count"], errors="coerce").astype("Int64")
    else:
        df["Count"] = pd.Series([pd.NA] * len(df), dtype="Int64")

    return df[["Rank", "Deck", "Share", "Count", "URL"]].set_index("Rank").sort_index()


parse_decklist_table_to_df = parse_decklist_table


def filter_top_meta(df_decklist: pd.DataFrame, *, threshold_pct: float | None) -> pd.DataFrame:
    if df_decklist is None or df_decklist.empty:
        raise ValueError("Decklist vuota")

    def parse_percent_series(series: pd.Series) -> pd.Series:
        return pd.to_numeric(
            series.astype(str)
            .str.replace("\xa0", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.strip(),
            errors="coerce",
        )

    df = df_decklist.copy().reset_index()
    df["share"] = parse_percent_series(df["Share"]).fillna(0.0)
    df = df.sort_values("share", ascending=False, kind="mergesort").reset_index(drop=True)
    df["share_cum"] = df["share"].cumsum()
    if threshold_pct is None or float(threshold_pct) >= 100.0:
        return df.copy()
    if (df["share_cum"] >= float(threshold_pct)).any():
        pos = (df["share_cum"] >= float(threshold_pct)).idxmax()
    else:
        pos = len(df) - 1
    return df.iloc[: pos + 1].copy()


def extract_matchups_from_html(html: str, deck_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    def _select_table(soup):
        for table in soup.find_all("table"):
            heads_raw = [th.get_text(" ", strip=True) for th in table.find_all("th")]
            heads = [h.strip().lower() for h in heads_raw]
            has_deck = any("deck" in h for h in heads)
            has_matches = any("matches" in h for h in heads)
            has_score = any(("score" in h) or ("record" in h) for h in heads)
            has_wr = any(("win" in h and "%" in hr) or ("winrate" in h) for h, hr in zip(heads, heads_raw))
            if has_deck and has_matches and has_score and has_wr:
                return table
        return None

    table = _select_table(soup) or soup.find("table")
    if table is None:
        return []

    thead = table.find("thead")
    headers_raw = [th.get_text(" ", strip=True) for th in (thead.find_all("th") if thead else table.find_all("th"))]
    headers = [h.strip().lower() for h in headers_raw]

    def _idx(check):
        for i, (header, raw_header) in enumerate(zip(headers, headers_raw)):
            if check(header, raw_header):
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

        anchor = cols[i_opp].find("a")
        opp = (anchor.get_text(" ", strip=True) if anchor else cols[i_opp].get_text(" ", strip=True)) or "Unknown"

        digits = re.findall(r"\d+", cols[i_n].get_text(" ", strip=True) or "")
        n = int("".join(digits)) if digits else 0

        match = re.search(rf"(\d+)\s*{dash}\s*(\d+)(?:\s*{dash}\s*(\d+))?", cols[i_rec].get_text(" ", strip=True) or "")
        if not match:
            w = l = t = 0
        else:
            w, l, t = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)

        wr_txt = cols[i_wr].get_text(" ", strip=True) or ""
        wr_match = re.search(r"(\d+(?:\.\d+)?)", wr_txt.replace(",", "."))
        wr = float(wr_match.group(1)) if wr_match else None

        n_calc = w + l + t
        if n < n_calc:
            n = n_calc
        if wr is None:
            wr = (100.0 * w / n) if n > 0 else 0.0

        out.append(
            {
                "Deck A": deck_name,
                "Deck B": opp,
                "W": w,
                "L": l,
                "T": t,
                "N": n,
                "Winrate": round(wr, 2),
            }
        )

    return out


def scrape_matchups(
    urls: list[tuple[str, str]],
    *,
    session: requests.Session,
    cache_dir: Path,
    ttl_minutes: int = 720,
    force_refresh: bool = False,
    rate_limit_seconds: float = 5.0,
    rate_limit_jitter_frac: float = 0.0,
    progress: bool = False,
    pbar_desc: str = "Scraping matchups",
    collect_diagnostics: bool = False,
) -> tuple[pd.DataFrame, int, int] | tuple[pd.DataFrame, int, int, dict]:
    """
    Fetch and parse matchup pages.

    urls: list of (deck_name, matchup_url)
    Returns: (df_raw, total_pages, cache_hits), or with collect_diagnostics:
    (df_raw, total_pages, cache_hits, diagnostics)
    """
    started = time.perf_counter()
    seen: set[str] = set()
    dedup: list[tuple[str, str]] = []
    for deck_name, url in urls:
        url = (url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        dedup.append((deck_name, url))

    total = len(dedup)
    rows: list[dict] = []
    cache_hits = 0
    fetch_metrics: list[dict] = []

    use_pbar = False
    iterator = dedup
    if progress:
        try:
            from tqdm.auto import tqdm

            iterator = tqdm(dedup, total=total, desc=pbar_desc, leave=False, dynamic_ncols=True)
            use_pbar = True
        except Exception:
            use_pbar = False
            iterator = dedup

    for deck_name, url in iterator:
        html, from_cache = fetch_html(
            url,
            session=session,
            cache_dir=cache_dir,
            ttl_minutes=ttl_minutes,
            force_refresh=force_refresh,
            rate_limit_seconds=rate_limit_seconds,
            rate_limit_jitter_frac=rate_limit_jitter_frac,
            metrics=fetch_metrics,
        )
        cache_hits += int(from_cache)
        rows.extend(extract_matchups_from_html(html, deck_name))

        if use_pbar:
            try:
                iterator.set_postfix({"cache": cache_hits, "rows": len(rows)}, refresh=False)
            except Exception:
                pass

    df = pd.DataFrame(rows)
    for column in ("W", "L", "T", "N"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype("Int64")
    if "Winrate" in df.columns:
        df["Winrate"] = pd.to_numeric(df["Winrate"], errors="coerce").fillna(0.0)

    if not collect_diagnostics:
        return df, total, cache_hits

    elapsed = time.perf_counter() - started
    delays = [float(m.get("delay_seconds", 0.0) or 0.0) for m in fetch_metrics]
    fetch_elapsed = [float(m.get("elapsed_seconds", 0.0) or 0.0) for m in fetch_metrics]
    cache_misses = total - cache_hits
    diagnostics = {
        "requested_pages": len(urls),
        "unique_pages": total,
        "duplicate_pages": max(0, len(urls) - total),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "rows": len(df),
        "elapsed_seconds": elapsed,
        "avg_seconds_per_page": elapsed / total if total else 0.0,
        "delay_seconds_total": sum(delays),
        "delay_seconds_min": min(delays) if delays else 0.0,
        "delay_seconds_max": max(delays) if delays else 0.0,
        "delay_seconds_mean": (sum(delays) / len(delays)) if delays else 0.0,
        "fetch_elapsed_seconds_total": sum(fetch_elapsed),
        "fetch_elapsed_seconds_mean": (sum(fetch_elapsed) / len(fetch_elapsed)) if fetch_elapsed else 0.0,
        "rate_limit_seconds": float(rate_limit_seconds),
        "rate_limit_jitter_frac": float(rate_limit_jitter_frac or 0.0),
    }
    return df, total, cache_hits, diagnostics


def scrape_decklist_for_expansion(
    browser,
    cache_dir: Path,
    exp,
    *,
    ttl_minutes: int = 60,
    force_refresh: bool = False,
    wait_css_selector: str = "table",
    headless: bool = True,
):
    """
    Build the expansion-aware Decks URL, fetch HTML with cache and parse it.

    The browser argument is kept for compatibility with the legacy call shape;
    fetching is delegated to scrape_decklist_html, which owns the Selenium session.
    """
    from domain.expansions import Expansion
    from sources.limitless.pages.sets import build_decks_url_for_expansion

    url = build_decks_url_for_expansion(exp or Expansion(code=None, name=None, is_current=True))
    html, cache_hit = scrape_decklist_html(
        url,
        cache_dir=cache_dir,
        ttl_minutes=ttl_minutes,
        force_refresh=force_refresh,
        headless=headless,
        wait_css_selector=wait_css_selector,
    )
    df = parse_decklist_table_to_df(html)
    return df, html, cache_hit


__all__ = [
    "LIMITLESS_BASE_URL",
    "LIMITLESS_DECKS_URL",
    "scrape_decklist_html",
    "parse_decklist_table",
    "parse_decklist_table_to_df",
    "filter_top_meta",
    "scrape_decklist_for_expansion",
    "to_matchup_url",
    "extract_matchups_from_html",
    "scrape_matchups",
]
