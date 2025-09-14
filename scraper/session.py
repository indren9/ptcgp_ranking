# ──────────────────────────────────────────────────────────────────────────────
# scraper/session.py — requests.Session + cache TTL=12h + force refresh
# ──────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta
from pathlib import Path
import time
import hashlib
import logging

log    = logging.getLogger("ptcgp")
netlog = logging.getLogger("ptcgp.net")  # 👈 logger dedicato solo al traffico rete/cache

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def make_session(user_agent: str = DEFAULT_UA, *, max_retries: int = 3, backoff: float = 0.7, timeout: int = 20) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=max_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it-IT,it;q=0.8",
        "Connection": "keep-alive",
    })
    s.request_timeout = timeout  # attach for external usage
    return s

def _cache_file(cache_dir: Path, url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{h}.html"

def cache_is_fresh(path: Path, *, ttl_minutes: int) -> bool:
    try:
        if ttl_minutes <= 0 or not path.exists():
            return False
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return (datetime.now() - mtime) < timedelta(minutes=ttl_minutes)
    except Exception:
        return False

def fetch_html(url: str, *, session: requests.Session, cache_dir: Path, ttl_minutes: int,
               force_refresh: bool, rate_limit_seconds: float) -> tuple[str, bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = _cache_file(cache_dir, url)
    if not force_refresh and cache_is_fresh(p, ttl_minutes=ttl_minutes):
        netlog.debug("[cache hit] %s", url)   # 👈 niente INFO
        return p.read_text(encoding="utf-8", errors="ignore"), True

    netlog.debug("[fetch] %s", url)          # 👈 niente INFO
    resp = session.get(url, timeout=getattr(session, "request_timeout", 20))
    time.sleep(rate_limit_seconds)
    resp.raise_for_status()
    html = resp.text
    try:
        p.write_text(html, encoding="utf-8")
    except Exception:
        pass
    return html, False
