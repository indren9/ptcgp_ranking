from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import logging
import random
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("ptcgp")
netlog = logging.getLogger("ptcgp.net")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def make_session(
    user_agent: str = DEFAULT_UA,
    *,
    max_retries: int = 3,
    backoff: float = 0.7,
    timeout: int = 20,
) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=max_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,it-IT,it;q=0.8",
            "Connection": "keep-alive",
        }
    )
    session.request_timeout = timeout
    return session


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


def fetch_html(
    url: str,
    *,
    session: requests.Session,
    cache_dir: Path,
    ttl_minutes: int,
    force_refresh: bool,
    rate_limit_seconds: float,
    rate_limit_jitter_frac: float = 0.0,
    metrics: list[dict] | None = None,
) -> tuple[str, bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_file(cache_dir, url)
    started = time.perf_counter()
    if not force_refresh and cache_is_fresh(path, ttl_minutes=ttl_minutes):
        netlog.debug("[cache hit] %s", url)
        html = path.read_text(encoding="utf-8", errors="ignore")
        if metrics is not None:
            metrics.append(
                {
                    "url": url,
                    "cache_hit": True,
                    "delay_seconds": 0.0,
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
        return html, True

    netlog.debug("[fetch] %s", url)
    resp = session.get(url, timeout=getattr(session, "request_timeout", 20))
    delay = float(rate_limit_seconds)
    jitter = max(0.0, float(rate_limit_jitter_frac or 0.0))
    if delay > 0 and jitter > 0:
        delay *= random.uniform(max(0.0, 1.0 - jitter), 1.0 + jitter)
    time.sleep(delay)
    resp.raise_for_status()
    html = resp.text
    try:
        path.write_text(html, encoding="utf-8")
    except Exception:
        pass
    if metrics is not None:
        metrics.append(
            {
                "url": url,
                "cache_hit": False,
                "delay_seconds": delay,
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
    return html, False

__all__ = ["make_session", "cache_is_fresh", "fetch_html"]
