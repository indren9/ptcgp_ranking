from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse
import json
import logging
import random
import re

from domain.expansions import Expansion, SET_CODE_RE
from sources.limitless.browser import chrome
from sources.limitless.client import make_session
from sources.limitless.constants import DEFAULT_GAME_CODE, LIMITLESS_DECKS_URL
from storage.routing import resolve_auto_from_outputs

log = logging.getLogger("ptcgp.sets")
DEFAULT_DECKS_URL = LIMITLESS_DECKS_URL
DEFAULT_FORMAT_CODE = "standard"


def _clean_game_code(value) -> Optional[str]:
    if value is None:
        return None
    code = str(value).strip()
    if not code:
        return None
    code = re.sub(r"\s+", "_", code)
    code = re.sub(r"[^A-Za-z0-9_.-]+", "", code)
    return code.upper() or None


def source_game_code(cfg: dict | None, decks_url: str | None = None, *, default: str = DEFAULT_GAME_CODE) -> str:
    source = ((cfg or {}).get("source", {}) or {})
    manual = _clean_game_code(source.get("game")) if isinstance(source, dict) else None
    if manual:
        return manual
    if decks_url:
        query_game = parse_qs(urlparse(str(decks_url)).query).get("game", [None])[0]
        parsed = _clean_game_code(query_game)
        if parsed:
            return parsed
    return default


def _clean_format_code(value) -> Optional[str]:
    if value is None:
        return None
    code = str(value).strip()
    if not code:
        return None
    code = re.sub(r"\s+", "_", code)
    code = re.sub(r"[^A-Za-z0-9_.-]+", "", code)
    return code.lower() or None


def format_config_from_source(cfg: dict | None) -> tuple[str, Optional[str]]:
    """
    Return (mode, code) for source.format.

    Supported shapes:
      source.format.mode: auto | code
      source.format.code: standard

    A plain string is accepted as a legacy/convenience manual code.
    """
    source = ((cfg or {}).get("source", {}) or {})
    fmt_cfg = source.get("format", {}) if isinstance(source, dict) else {}
    if isinstance(fmt_cfg, str):
        return "code", _clean_format_code(fmt_cfg)
    if not isinstance(fmt_cfg, dict):
        return "auto", None

    mode = str(fmt_cfg.get("mode") or "auto").strip().lower()
    code = _clean_format_code(fmt_cfg.get("code"))
    if mode not in {"auto", "code"}:
        raise ValueError("source.format.mode deve essere 'auto' oppure 'code'.")
    if mode == "code" and not code:
        raise ValueError("source.format.code e' obbligatorio quando source.format.mode='code'.")
    return mode, code


def resolve_format_code(cfg: dict | None, decks_url: str | None, *, default: str = DEFAULT_FORMAT_CODE) -> str:
    """
    Resolve the Limitless format code used in URL and output scope.

    mode=code forces source.format.code.
    mode=auto keeps the URL format when present, otherwise falls back to the
    Limitless default used by this project.
    """
    mode, manual_code = format_config_from_source(cfg)
    if mode == "code":
        return manual_code or default

    query_code = None
    if decks_url:
        query_code = parse_qs(urlparse(str(decks_url)).query).get("format", [None])[0]
    return _clean_format_code(query_code) or default


def build_decks_url_for_expansion(exp, decks_url: str = DEFAULT_DECKS_URL, cfg: dict | None = None) -> str:
    """
    Ensure game, resolved format, and set=<CODE> when available.
    Idempotent: existing query params are updated rather than duplicated.
    """
    base = decks_url or DEFAULT_DECKS_URL
    parsed = urlparse(base)
    query = parse_qs(parsed.query)

    query["game"] = [source_game_code(cfg, base)]
    query["format"] = [resolve_format_code(cfg, base)]
    if getattr(exp, "code", None):
        query["set"] = [quote(exp.code, safe="")]
    else:
        query.pop("set", None)

    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _expansion_from_option(opt) -> Optional[Expansion]:
    try:
        code = (opt.get("data-set") or opt.get("value") or "").strip() or None
        if not code or not SET_CODE_RE.fullmatch(code):
            return None
        name = opt.get_text(" ", strip=True) or None
        is_current = bool(opt.has_attr("selected"))
        return Expansion(code=code, name=name, is_current=is_current)
    except Exception:
        return None


def _expansion_from_anchor(anchor) -> Optional[Expansion]:
    try:
        href = anchor.get("href") or ""
        if "set=" not in href:
            return None
        parsed = urlparse(href)
        path = (parsed.path or "").rstrip("/")
        if path and path != "/decks":
            return None
        match = re.search(r"[?&]set=([^&]+)", href)
        code = (match.group(1) if match else "").strip()
        if not code or not SET_CODE_RE.fullmatch(code):
            return None
        name = anchor.get_text(" ", strip=True) or None
        classes = anchor.get("class") or []
        is_current = any("active" in c or "selected" in c for c in classes)
        return Expansion(code=code, name=name, is_current=is_current)
    except Exception:
        return None


def _select_looks_like_set(select) -> bool:
    attrs = [
        select.get("id"),
        select.get("name"),
        select.get("aria-label"),
        select.get("title"),
    ]
    text = " ".join(str(attr or "").lower() for attr in attrs)
    if any(token in text for token in ("set", "expansion")):
        return True
    if any(token in text for token in ("game", "format", "rotation")):
        return False
    return False


def parse_expansions_from_html(html: str) -> List[Expansion]:
    """Best-effort parser: prefer the set selector, fallback to links with set=."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    all_selects = soup.find_all("select")
    set_selects = [select for select in all_selects if _select_looks_like_set(select)]
    # Historical fixtures used a bare single select. Keep supporting that shape
    # without treating the real Game/Format selectors as expansion catalogs.
    candidate_selects = set_selects or (all_selects if len(all_selects) == 1 else [])

    for select in candidate_selects:
        bucket: List[Expansion] = []
        for option in select.find_all("option"):
            exp = _expansion_from_option(option)
            if exp:
                bucket.append(exp)
        if bucket:
            dedup = {}
            for exp in bucket:
                if exp.code and exp.code not in dedup:
                    dedup[exp.code] = exp
            return list(dedup.values())

    bucket: List[Expansion] = []
    for anchor in soup.find_all("a", href=True):
        exp = _expansion_from_anchor(anchor)
        if exp:
            bucket.append(exp)

    dedup = {}
    for exp in bucket:
        if exp.code and exp.code not in dedup:
            dedup[exp.code] = exp
    return list(dedup.values())


def default_cache_path(base_dir: Path | None = None) -> Path:
    base = Path(base_dir) if base_dir else Path("cache") / "requests"
    return Path(base) / "expansions_pocket.json"


def load_cached_expansions(cache_path: Path) -> tuple[List[Expansion], Optional[datetime], Optional[datetime]]:
    if not cache_path.exists():
        return [], None, None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data.get("fetched_at"))
        burst_raw = data.get("burst_until")
        burst_until = datetime.fromisoformat(burst_raw) if burst_raw else None
        items = [Expansion(**e) for e in data.get("expansions", [])]

        current_count = sum(1 for e in items if getattr(e, "is_current", False))
        if current_count != 1 and items:
            items = normalize_is_current(items)
            log.debug("[catalog] riparato is_current: imposto '%s' come corrente.", items[0].code)

        return items, ts, burst_until
    except Exception:
        return [], None, None


def save_cached_expansions(cache_path: Path, exps: List[Expansion], *, burst_until: Optional[datetime] = None) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    exps = normalize_is_current(list(exps))
    payload = {
        "fetched_at": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
        "burst_until": burst_until.isoformat(timespec="seconds") if burst_until else None,
        "expansions": [e.__dict__ for e in exps],
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_is_current(exps: list[Expansion]) -> list[Expansion]:
    """
    Enforce the invariant used by the pipeline: exactly the first expansion is current.
    """
    if not exps:
        return exps
    return [Expansion(code=e.code, name=e.name, is_current=(i == 0)) for i, e in enumerate(exps)]


def _days_left_in_month(dt: datetime) -> int:
    if dt.month == 12:
        next_month = datetime(dt.year + 1, 1, 1)
    else:
        next_month = datetime(dt.year, dt.month + 1, 1)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return (next_month - start).days


def _ttl_policy_hours(now: datetime, *, last10_days_hours: int = 24, last3_days_hours: int = 6) -> int:
    days_left = _days_left_in_month(now)
    if days_left <= 3:
        return last3_days_hours
    if days_left <= 10:
        return last10_days_hours
    return 7 * 24


def _apply_jitter(hours: float, frac: float = 0.10) -> float:
    jitter = 1.0 + random.uniform(-frac, frac)
    return max(1.0, hours * jitter)


def catalog_changed(prev: List[Expansion], new: List[Expansion]) -> bool:
    prev_codes = {e.code for e in prev if e.code}
    new_codes = {e.code for e in new if e.code}
    prev_names = {e.name for e in prev if e.name}
    new_names = {e.name for e in new if e.name}
    return (prev_codes != new_codes) or (prev_names != new_names)


def compute_ttl(
    now: datetime,
    *,
    ttl_days_fixed: int | None,
    burst_enabled: bool,
    burst_until: Optional[datetime],
    burst_cap_hours: int,
    last10_days_hours: int,
    last3_days_hours: int,
    jitter_frac: float,
) -> timedelta:
    if ttl_days_fixed is not None:
        return timedelta(days=max(0, ttl_days_fixed))
    hours = _ttl_policy_hours(now, last10_days_hours=last10_days_hours, last3_days_hours=last3_days_hours)
    if burst_enabled and burst_until and now < burst_until:
        hours = min(hours, burst_cap_hours)
    return timedelta(hours=_apply_jitter(hours, frac=jitter_frac))


def fetch_expansions_http(session, *, decks_url: str) -> List[Expansion]:
    resp = session.get(decks_url, timeout=getattr(session, "request_timeout", 20))
    resp.raise_for_status()
    return parse_expansions_from_html(resp.text)


def get_expansions_catalog(
    session=None,
    *,
    decks_url: str,
    cache_path: Path | None = None,
    ttl_days_fixed: int | None = None,
    burst_enabled: bool = True,
    burst_days: int = 2,
    burst_cap_hours: int = 12,
    last10_days_hours: int = 24,
    last3_days_hours: int = 6,
    jitter_frac: float = 0.10,
    browser=None,
    cfg: dict | None = None,
) -> List[Expansion]:
    cache_path = cache_path or default_cache_path()
    prev, ts, burst_until = load_cached_expansions(cache_path)
    now = datetime.now(UTC).replace(tzinfo=None)

    ttl = compute_ttl(
        now,
        ttl_days_fixed=ttl_days_fixed,
        burst_enabled=burst_enabled,
        burst_until=burst_until,
        burst_cap_hours=burst_cap_hours,
        last10_days_hours=last10_days_hours,
        last3_days_hours=last3_days_hours,
        jitter_frac=jitter_frac,
    )

    fresh = ts is not None and (now - ts) < ttl
    if prev and fresh:
        return prev

    if session is None:
        if not prev:
            log.debug("Catalog missing and no HTTP session available; returning [].")
        return prev

    try:
        new = fetch_expansions_http(session, decks_url=decks_url)
        if not new and browser is not None:
            new = fetch_expansions_selenium(browser, decks_url=decks_url, cfg=cfg)
        if new:
            new_burst_until = burst_until
            if catalog_changed(prev, new) and burst_enabled:
                new_burst_until = now + timedelta(days=max(1, burst_days))
            save_cached_expansions(cache_path, new, burst_until=new_burst_until)
            return new
    except Exception:
        log.debug("Catalog refresh failed (HTTP/Selenium); using previous cache.", exc_info=True)

    return prev


def expansions_cache_params_from_config(cfg: dict) -> Dict[str, Any]:
    exp_cfg = (cfg.get("scraping", {}) or {}).get("expansions_cache", {}) or {}
    return {
        "ttl_days_fixed": exp_cfg.get("ttl_days_fixed", None),
        "burst_enabled": bool(exp_cfg.get("burst_enabled", True)),
        "burst_days": int(exp_cfg.get("burst_days", 2)),
        "burst_cap_hours": int(exp_cfg.get("burst_cap_hours", 12)),
        "last10_days_hours": int(exp_cfg.get("last10_days_hours", 24)),
        "last3_days_hours": int(exp_cfg.get("last3_days_hours", 6)),
        "jitter_frac": float(exp_cfg.get("jitter_frac", 0.10)),
    }


def expansions_cache_path(paths, cfg: dict | None = None, decks_url: str | None = None) -> Path:
    game = source_game_code(cfg, decks_url).lower()
    path = Path(paths.cache) / f"expansions_{game}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fetch_catalog_with_policy(
    cfg: dict,
    paths,
    *,
    session,
    browser=None,
    ttl_override: int | None = None,
    decks_url: str = DEFAULT_DECKS_URL,
):
    """Apply project config/cache policy to the Limitless expansion catalog."""
    params = expansions_cache_params_from_config(cfg)
    cache_path = expansions_cache_path(paths, cfg, decks_url)
    kwargs = dict(
        session=session,
        decks_url=decks_url,
        cache_path=cache_path,
        browser=browser,
        cfg=cfg,
        **params,
    )
    if ttl_override is not None:
        kwargs["ttl_days_fixed"] = ttl_override
    return get_expansions_catalog(**kwargs)


def resolve_expansion_and_url_from_config(
    cfg: dict,
    paths,
    *,
    require_in_catalog: bool = True,
    decks_url: str = DEFAULT_DECKS_URL,
) -> Tuple[Expansion, str, List[Expansion]]:
    """
    Resolve the run expansion from config and return:
      (expansion, decks_url_for_run, catalog_list)

    mode='auto' prefers the live selected set, then catalog, then latest outputs.
    mode='code' validates/augments the configured code against the catalog.
    """
    scraping = (cfg.get("scraping", {}) or {})
    set_cfg = (scraping.get("set", {}) or {})
    mode = (set_cfg.get("mode") or "auto").strip().lower()
    code_raw = (set_cfg.get("code") or "") or None
    code = code_raw.strip() if isinstance(code_raw, str) else None
    decks_url_for_catalog = _ensure_game_format(decks_url, cfg=cfg)

    timeout = int(scraping.get("timeout_sec", 20) or scraping.get("request_timeout_sec", 20))
    session = make_session(timeout=timeout)
    catalog = fetch_catalog_with_policy(cfg, paths, session=session, browser=None, decks_url=decks_url_for_catalog)

    if mode == "auto":
        sel_cfg = (scraping.get("selenium", {}) or {})
        headless = bool(sel_cfg.get("headless", True))

        with chrome(headless=headless) as browser:
            catalog = fetch_catalog_with_policy(
                cfg,
                paths,
                session=session,
                browser=browser,
                ttl_override=0,
                decks_url=decks_url_for_catalog,
            )
            live_exp = read_current_expansion_from_selenium(
                browser,
                decks_url=decks_url_for_catalog,
                wait_seconds=int((scraping.get("selenium", {}) or {}).get("wait_sec", 20) or 20),
                cfg=cfg,
            )

        source = "live-site"
        if live_exp and live_exp.code:
            exp = Expansion(code=live_exp.code, name=live_exp.name, is_current=True)
        elif live_exp and not live_exp.code:
            log.warning(
                "Live site ha selezionato un set senza codice valido (%s); ignoro e uso catalog/fallback.",
                getattr(live_exp, "name", None),
            )
            if catalog:
                cur = next((e for e in catalog if getattr(e, "is_current", False)), None) or catalog[0]
                exp = Expansion(code=cur.code, name=cur.name, is_current=True)
                source = "catalog"
            else:
                fallback = resolve_auto_from_outputs(getattr(paths, "outputs", None) or getattr(paths, "output_dir", None))
                exp = Expansion(code=fallback.code, name=fallback.name, is_current=True)
                source = "outputs-fallback"
                if not exp.code:
                    log.warning("No expansion catalog and no output folder found: using an empty set.")
        elif catalog:
            cur = next((e for e in catalog if getattr(e, "is_current", False)), None) or catalog[0]
            exp = Expansion(code=cur.code, name=cur.name, is_current=True)
            source = "catalog"
        else:
            fallback = resolve_auto_from_outputs(getattr(paths, "outputs", None) or getattr(paths, "output_dir", None))
            exp = Expansion(code=fallback.code, name=fallback.name, is_current=True)
            source = "outputs-fallback"
            if not exp.code:
                log.warning("No expansion catalog and no output folder found: using an empty set.")

        log.debug("[SET AUTO] source=%s | code=%s | name=%s", source, getattr(exp, "code", None), getattr(exp, "name", None))
        url = build_decks_url_for_expansion(exp, decks_url, cfg=cfg)
        session.close()
        return exp, url, catalog

    if mode == "code" and code:
        exp = Expansion(code=code, name=None, is_current=False)
        need_check = require_in_catalog
    else:
        exp = Expansion(code=None, name=None, is_current=True)
        need_check = False

    if need_check:
        hit = next((e for e in catalog if (e.code or "").lower() == code.lower()), None)
        if hit and hit.name:
            exp = Expansion(code=code, name=hit.name, is_current=False)
        else:
            sel_cfg = (scraping.get("selenium", {}) or {})
            headless = bool(sel_cfg.get("headless", True))
            with chrome(headless=headless) as browser:
                catalog = fetch_catalog_with_policy(
                    cfg,
                    paths,
                    session=session,
                    browser=browser,
                    ttl_override=0,
                    decks_url=decks_url_for_catalog,
                )
            hit = next((e for e in catalog if (e.code or "").lower() == code.lower()), None)
            if hit and hit.name:
                exp = Expansion(code=code, name=hit.name, is_current=False)
            else:
                session.close()
                raise RuntimeError(f"Set code '{code}' is not present in the Limitless catalog even after refresh.")

    url = build_decks_url_for_expansion(exp, decks_url, cfg=cfg)
    session.close()
    return exp, url, catalog


def _url_without_set(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query.pop("set", None)
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _ensure_game_format(url: str, cfg: dict | None = None) -> str:
    parsed = urlparse(_url_without_set(url))
    query = parse_qs(parsed.query)
    query["game"] = [source_game_code(cfg, url)]
    query["format"] = [resolve_format_code(cfg, url)]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _read_set_param(url: str) -> Optional[str]:
    try:
        return parse_qs(urlparse(url).query).get("set", [None])[0]
    except Exception:
        return None


def _extract_set_code(value: str, current_url: str, display_text: str) -> Optional[str]:
    value = (value or "").strip()
    if value and SET_CODE_RE.fullmatch(value):
        return value

    code = _read_set_param(current_url)
    if code and SET_CODE_RE.fullmatch(code):
        return code

    text = (display_text or "").strip()
    if text:
        match = re.search(r"\b([A-Z]\d{1,3}[a-z]?)\b", text)
        if match:
            code = match.group(1)
            if SET_CODE_RE.fullmatch(code):
                return code
    return None


def _selenium_deps():
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import Select, WebDriverWait

    return By, EC, Select, WebDriverWait


def read_current_expansion_from_selenium(
    browser,
    *,
    decks_url: str,
    wait_seconds: int = 20,
    cfg: dict | None = None,
) -> Optional[Expansion]:
    """Read the currently selected expansion from the Limitless decklist page."""
    By, EC, Select, WebDriverWait = _selenium_deps()

    try:
        browser.get(_ensure_game_format(decks_url, cfg=cfg))
        wait = WebDriverWait(browser, wait_seconds)
        set_select = wait.until(EC.presence_of_element_located((By.ID, "set")))
        select_api = Select(set_select)
        option = select_api.first_selected_option
        if option is None:
            return None

        text = (option.text or "").strip()
        value = (option.get_attribute("data-set") or option.get_attribute("value") or "").strip()
        code = _extract_set_code(value, browser.current_url, text)

        if code and text:
            return Expansion(code=code, name=text, is_current=True)
        if code:
            return Expansion(code=code, name=None, is_current=True)
    except Exception:
        return None

    return None


def fetch_expansions_selenium(
    browser,
    *,
    decks_url: str,
    wait_seconds: int = 20,
    cfg: dict | None = None,
) -> List[Expansion]:
    """
    Select resolved game+format, iterate the set selector and read ?set=<CODE>.
    Returns UI order, generally newest expansion first.
    """
    import time

    By, EC, Select, WebDriverWait = _selenium_deps()

    browser.get(_ensure_game_format(decks_url, cfg=cfg))
    wait = WebDriverWait(browser, wait_seconds)

    game_select = wait.until(EC.presence_of_element_located((By.ID, "game")))
    format_select = wait.until(EC.presence_of_element_located((By.ID, "format")))
    set_select = wait.until(EC.presence_of_element_located((By.ID, "set")))

    game_code = source_game_code(cfg, decks_url)
    try:
        Select(game_select).select_by_value(game_code)
    except Exception:
        try:
            visible = "Pokémon TCG Pocket" if game_code == DEFAULT_GAME_CODE else game_code
            Select(game_select).select_by_visible_text(visible)
        except Exception:
            pass

    fmt_code = resolve_format_code(cfg, browser.current_url)
    try:
        Select(format_select).select_by_value(fmt_code.upper())
    except Exception:
        try:
            Select(format_select).select_by_value(fmt_code)
        except Exception:
            try:
                Select(format_select).select_by_visible_text(fmt_code.replace("_", " ").title())
            except Exception:
                pass

    set_select = wait.until(EC.presence_of_element_located((By.ID, "set")))
    select_api = Select(set_select)
    option_names = [(o.text or "").strip() for o in select_api.options if (o.text or "").strip()]

    browser.get(_url_without_set(browser.current_url))
    time.sleep(0.2)

    out: List[Expansion] = []
    seen: set[str] = set()
    current_name = (select_api.first_selected_option.text or "").strip() if select_api.first_selected_option else ""

    for name in option_names:
        set_select = wait.until(EC.presence_of_element_located((By.ID, "set")))
        select_api = Select(set_select)
        prev_code = _read_set_param(browser.current_url)

        try:
            select_api.select_by_visible_text(name)
        except Exception:
            options = [o for o in select_api.options if (o.text or "").strip().lower() == name.lower()]
            if not options:
                continue
            select_api.select_by_visible_text(options[0].text)

        try:
            browser.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", set_select)
        except Exception:
            pass

        code = None
        deadline = time.time() + wait_seconds
        last_url = None
        while time.time() < deadline:
            time.sleep(0.15)
            current_url = browser.current_url
            if current_url != last_url:
                last_url = current_url
                candidate = _read_set_param(current_url)
                if candidate and candidate != prev_code:
                    code = candidate
                    break

        if code is None:
            browser.get(_url_without_set(browser.current_url))
            time.sleep(0.1)
            set_select = wait.until(EC.presence_of_element_located((By.ID, "set")))
            select_api = Select(set_select)
            try:
                select_api.select_by_visible_text(name)
                browser.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", set_select)
            except Exception:
                pass
            deadline = time.time() + wait_seconds / 2
            while time.time() < deadline:
                time.sleep(0.15)
                candidate = _read_set_param(browser.current_url)
                if candidate:
                    code = candidate
                    break

        if not code or code in seen:
            continue
        seen.add(code)
        out.append(Expansion(code=code, name=name, is_current=(name.lower() == current_name.lower())))

    return out

__all__ = [
    "Expansion",
    "SET_CODE_RE",
    "DEFAULT_DECKS_URL",
    "DEFAULT_FORMAT_CODE",
    "build_decks_url_for_expansion",
    "format_config_from_source",
    "source_game_code",
    "resolve_format_code",
    "default_cache_path",
    "expansions_cache_params_from_config",
    "expansions_cache_path",
    "fetch_catalog_with_policy",
    "resolve_expansion_and_url_from_config",
    "catalog_changed",
    "compute_ttl",
    "load_cached_expansions",
    "normalize_is_current",
    "save_cached_expansions",
    "fetch_expansions_http",
    "get_expansions_catalog",
    "parse_expansions_from_html",
    "fetch_expansions_selenium",
    "read_current_expansion_from_selenium",
]
