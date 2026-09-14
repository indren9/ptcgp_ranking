"""Automatic freshness for runtime Limitless expansion metadata.

Release-window snapshots and the independent public CSV export do not use this
cache. Live callers receive validated metadata without choosing a refresh flag;
replays only read their explicitly scoped cached inputs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from domain.expansions import Expansion, SET_CODE_RE
from sources.limitless.client import make_session
from sources.limitless.pages.sets import (
    DEFAULT_DECKS_URL,
    _ensure_game_format,
    fetch_expansions_http,
    format_uses_rotation,
    resolve_format_code,
    source_game_code,
)

log = logging.getLogger("ptcgp.catalog_refresh")
MAX_CATALOG_AGE = timedelta(minutes=60)
CACHE_SCHEMA_VERSION = 1
_POCKET_CODE = re.compile(r"[A-Z]\d{1,3}[a-z]?")
_PTCG_CODE = re.compile(r"[A-Z]{2,6}\d?")
_ROTATION = re.compile(r"20\d{2}")
_OFFLINE_MODES = frozenset({"offline", "replay", "frozen", "frozen_snapshot", "test", "fixture", "test_fixture"})


class CatalogValidationError(ValueError):
    """The fetched or cached metadata cannot safely identify this catalog."""


class CatalogRefreshError(RuntimeError):
    """Catalog resolution failed closed because no valid prior input exists."""


@dataclass(frozen=True)
class CatalogContext:
    game: str
    format: str
    rotation: str | None
    source_url: str


@dataclass(frozen=True)
class _CachedCatalog:
    expansions: list[Expansion]
    fetched_at: datetime


def catalog_context(cfg: dict | None, decks_url: str | None = None) -> CatalogContext:
    """Resolve the same game/format URL as the shared Limitless parser."""
    cfg = cfg or {}
    scraping = cfg.get("scraping", {}) or {}
    base_url = decks_url or scraping.get("decks_url") or DEFAULT_DECKS_URL
    game = source_game_code(cfg, base_url)
    if game not in {"POCKET", "PTCG"}:
        raise CatalogValidationError(f"Unsupported runtime catalog game: {game!r}")
    fmt = resolve_format_code(cfg, base_url)
    parsed = urlparse(_ensure_game_format(base_url, cfg=cfg))
    query = parse_qs(parsed.query)
    rotation = query.get("rotation", [None])[0]
    if rotation and (game != "PTCG" or not format_uses_rotation(fmt) or not _ROTATION.fullmatch(rotation)):
        raise CatalogValidationError("Invalid rotation for runtime catalog context")
    # Canonical ordering prevents two spellings of the same URL sharing no cache.
    query_string = urlencode(sorted((key, values[0]) for key, values in query.items()))
    source_url = urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.params, query_string, ""))
    return CatalogContext(game=game, format=fmt, rotation=rotation, source_url=source_url)


def catalog_cache_path(paths, context: CatalogContext) -> Path:
    """Scope by game, format, rotation selection and the actual source URL."""
    scope_digest = sha256(json.dumps(asdict(context), sort_keys=True).encode("utf-8")).hexdigest()[:16]
    rotation = context.rotation or "site-current"
    return Path(paths.cache) / f"runtime_catalog_{context.game.lower()}_{context.format}_{rotation}_{scope_digest}.json"


def validate_catalog(expansions, context: CatalogContext) -> list[Expansion]:
    """Validate existing parser output without inventing rows or current sets."""
    if not isinstance(expansions, (list, tuple)) or not expansions:
        raise CatalogValidationError("Expansion catalog is empty or not a list")
    result: list[Expansion] = []
    seen: set[str] = set()
    for expansion in expansions:
        if not isinstance(expansion, Expansion):
            raise CatalogValidationError("Catalog row is not an Expansion")
        code = expansion.code
        game_shape = _POCKET_CODE if context.game == "POCKET" else _PTCG_CODE
        if not isinstance(code, str) or not SET_CODE_RE.fullmatch(code) or not game_shape.fullmatch(code):
            raise CatalogValidationError(f"Invalid expansion code for {context.game}: {code!r}")
        canonical_code = code.casefold()
        if canonical_code in seen:
            raise CatalogValidationError(f"Duplicate canonical expansion code: {code}")
        seen.add(canonical_code)
        if not isinstance(expansion.name, str) or not expansion.name.strip() or any(ord(char) < 32 for char in expansion.name):
            raise CatalogValidationError(f"Expansion {code} has no usable name")
        if not isinstance(expansion.is_current, bool):
            raise CatalogValidationError(f"Expansion {code} has a non-boolean current marker")
        rotation = expansion.rotation
        if context.game == "PTCG" and format_uses_rotation(context.format):
            if not isinstance(rotation, str) or not _ROTATION.fullmatch(rotation):
                raise CatalogValidationError(f"Expansion {code} has no valid Standard rotation")
            if context.rotation and expansion.is_current and rotation != context.rotation:
                raise CatalogValidationError("Current expansion does not match the requested rotation")
        elif rotation is not None:
            raise CatalogValidationError(f"Expansion {code} has rotation outside a PTCG Standard context")
        result.append(expansion)
    if sum(expansion.is_current for expansion in result) != 1:
        raise CatalogValidationError("Catalog must identify exactly one current expansion")
    return result


def _timestamp(value, *, legacy: bool, now: datetime) -> datetime:
    if not isinstance(value, str):
        raise CatalogValidationError("Catalog has no successful refresh timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CatalogValidationError("Catalog refresh timestamp is invalid") from error
    if parsed.tzinfo is None:
        if not legacy:
            raise CatalogValidationError("Catalog refresh timestamp must have a timezone")
        # The old writer explicitly stored UTC after dropping tzinfo.
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed > now:
        raise CatalogValidationError("Catalog refresh timestamp is in the future")
    return parsed


def _read_cache(path: Path, context: CatalogContext, now: datetime, *, legacy: bool = False) -> _CachedCatalog:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CatalogValidationError("Catalog cache is not an object")
    if not legacy:
        if payload.get("schema_version") != CACHE_SCHEMA_VERSION or payload.get("context") != asdict(context):
            raise CatalogValidationError("Catalog cache context or schema does not match")
    elif "context" in payload and payload["context"] != asdict(context):
        raise CatalogValidationError("Legacy catalog declares a different context")
    fetched_at = _timestamp(payload.get("fetched_at"), legacy=legacy, now=now)
    rows = payload.get("expansions")
    if not isinstance(rows, list):
        raise CatalogValidationError("Catalog cache has no expansion list")
    expansions = validate_catalog([Expansion(**row) for row in rows], context)
    return _CachedCatalog(expansions, fetched_at)


def _legacy_cache_path(paths, context: CatalogContext) -> Path | None:
    # Old filenames identify game and format, but cannot identify an explicitly
    # requested rotation or a custom endpoint. Never consult expansions_pocket.json.
    parsed = urlparse(context.source_url)
    query = parse_qs(parsed.query)
    if (
        context.rotation is not None
        or parsed.scheme != "https"
        or parsed.netloc != "play.limitlesstcg.com"
        or parsed.path.rstrip("/") != "/decks"
        or set(query) != {"game", "format"}
    ):
        return None
    return Path(paths.cache) / f"expansions_{context.game.lower()}_{context.format}.json"


def _previous_catalog(paths, path: Path, context: CatalogContext, now: datetime) -> tuple[_CachedCatalog | None, Exception | None]:
    error = None
    candidates = [(path, False)]
    legacy_path = _legacy_cache_path(paths, context)
    if legacy_path is not None:
        candidates.append((legacy_path, True))
    for candidate, legacy in candidates:
        try:
            return _read_cache(candidate, context, now, legacy=legacy), error
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, KeyError) as invalid:
            error = invalid
    return None, error


def _write_cache(path: Path, context: CatalogContext, expansions: list[Expansion], fetched_at: datetime) -> None:
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "context": asdict(context),
        "fetched_at": fetched_at.isoformat(),
        "expansions": [asdict(expansion) for expansion in expansions],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _diagnostic(state: str, context: CatalogContext, now: datetime, previous: _CachedCatalog | None, *, network: bool, error: Exception | None = None, mode: str = "live") -> None:
    age = (now - previous.fetched_at).total_seconds() if previous else None
    current = next((expansion.code for expansion in previous.expansions if expansion.is_current), None) if previous else None
    fields = {
        "catalog_refresh": state,
        "game": context.game,
        "catalog_format": context.format,
        "catalog_rotation": context.rotation,
        "catalog_age_seconds": age,
        "last_successful_refresh_at": previous.fetched_at.isoformat() if previous else None,
        "refresh_error_class": type(error).__name__ if error else None,
        "catalog_entries": len(previous.expansions) if previous else 0,
        "current_expansion": current,
        "network_refresh": network,
        "execution_mode": mode,
    }
    level = logging.ERROR if state == "FAIL_CLOSED" else logging.WARNING if state == "FALLBACK_LKG" else logging.INFO
    log.log(level, "CATALOG_REFRESH = %s game=%s format=%s rotation=%s cache_age_seconds=%s last_successful_refresh_at=%s refresh_error_class=%s entries=%s current=%s network_refresh=%s execution_mode=%s", state, context.game, context.format, context.rotation, age, fields["last_successful_refresh_at"], fields["refresh_error_class"], fields["catalog_entries"], current, network, mode, extra=fields)


def ensure_catalog_fresh(
    cfg: dict,
    paths,
    *,
    session=None,
    decks_url: str | None = None,
    execution_mode: str = "live",
    now: datetime | None = None,
    fetcher: Callable | None = None,
) -> list[Expansion]:
    """Return validated metadata under the fixed automatic live policy.

    ``fetcher(session, *, decks_url)`` is the explicit refresh-client test seam.
    Non-live modes never invoke it or create an HTTP session, regardless of age.
    Legacy TTL and force-refresh configuration cannot override this contract.
    """
    context = catalog_context(cfg, decks_url)
    started_at = now or datetime.now(UTC)
    if started_at.tzinfo is None:
        raise ValueError("Catalog clock must be timezone-aware")
    started_at = started_at.astimezone(UTC)
    mode = str(execution_mode).strip().lower().replace("-", "_")
    if mode != "live" and mode not in _OFFLINE_MODES:
        raise ValueError(f"Unsupported catalog execution mode: {execution_mode!r}")
    path = catalog_cache_path(paths, context)
    previous, cache_error = _previous_catalog(paths, path, context, started_at)
    if mode != "live":
        if previous is not None:
            _diagnostic("FROZEN_CACHE", context, started_at, previous, network=False, mode=mode)
            return previous.expansions
        failure = cache_error or CatalogValidationError("No validated frozen catalog for this context")
        _diagnostic("FAIL_CLOSED", context, started_at, None, network=False, error=failure, mode=mode)
        raise CatalogRefreshError(f"No valid {context.game}/{context.format} catalog in {mode} mode; network refresh is disabled") from failure
    if previous is not None and started_at - previous.fetched_at < MAX_CATALOG_AGE:
        _diagnostic("FRESH_CACHE", context, started_at, previous, network=False)
        return previous.expansions

    owned_session = None
    try:
        if session is None and fetcher is None:
            scraping = cfg.get("scraping", {}) or {}
            owned_session = make_session(timeout=int(scraping.get("timeout_sec", 20) or 20))
            session = owned_session
        fetched = (
            fetcher(session, decks_url=context.source_url)
            if fetcher is not None
            else fetch_expansions_http(session, decks_url=context.source_url, strict=True)
        )
        expansions = validate_catalog(fetched, context)
        completed_at = started_at if now is not None else datetime.now(UTC)
        _write_cache(path, context, expansions, completed_at)
    except Exception as error:
        if previous is not None:
            _diagnostic("FALLBACK_LKG", context, started_at, previous, network=True, error=error)
            return previous.expansions
        _diagnostic("FAIL_CLOSED", context, started_at, None, network=True, error=error)
        raise CatalogRefreshError(f"No valid {context.game}/{context.format} catalog; refresh failed ({type(error).__name__}: {error})") from error
    finally:
        if owned_session is not None:
            owned_session.close()
    refreshed = _CachedCatalog(expansions, completed_at)
    _diagnostic("REFRESHED", context, completed_at, refreshed, network=True)
    return expansions


__all__ = [
    "MAX_CATALOG_AGE", "CatalogContext", "CatalogRefreshError", "CatalogValidationError",
    "catalog_context", "catalog_cache_path", "validate_catalog", "ensure_catalog_fresh",
]
