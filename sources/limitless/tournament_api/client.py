from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import json
import threading
import time
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode

import requests

DEFAULT_BASE_URL = "https://play.limitlesstcg.com/api"
DEFAULT_USER_AGENT = "PTCGP-Ranking-MARS/LimitlessTournamentAPI"


class JsonCache(Protocol):
    def get_json(self, key: str) -> Any | None: ...

    def set_json(self, key: str, value: Any) -> None: ...


@dataclass(frozen=True)
class RateLimitObservation:
    fetched_at: datetime
    status_code: int
    headers: Mapping[str, str]


class LimitlessTournamentApiClient:
    """Synchronous client for the documented Limitless Tournament API."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20.0,
        max_retries: int = 4,
        backoff_factor: float = 1.0,
        min_request_interval_seconds: float = 0.0,
        cache: JsonCache | None = None,
        sleep_fn=time.sleep,
        monotonic_fn=time.monotonic,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if backoff_factor < 0:
            raise ValueError("backoff_factor must be non-negative")
        if min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds must be non-negative")

        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff_factor = float(backoff_factor)
        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.cache = cache
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._request_lock = threading.Lock()
        self._last_request_started: float | None = None
        self._rate_limit_observations: list[RateLimitObservation] = []

        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": str(user_agent).strip() or DEFAULT_USER_AGENT,
            }
        )

    @property
    def rate_limit_observations(self) -> tuple[RateLimitObservation, ...]:
        return tuple(self._rate_limit_observations)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "LimitlessTournamentApiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _url(self, path: str) -> str:
        clean = "/" + str(path).strip().lstrip("/")
        return self.base_url + clean

    def _cache_key(self, path: str, params: Mapping[str, Any] | None) -> str:
        url = self._url(path)
        clean_params = {
            str(key): value
            for key, value in (params or {}).items()
            if value is not None
        }
        if not clean_params:
            return url
        return f"{url}?{urlencode(sorted(clean_params.items()), doseq=True)}"

    @staticmethod
    def _rate_limit_headers(headers: Mapping[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in headers.items():
            low = str(key).lower()
            if "ratelimit" in low or low == "retry-after":
                out[str(key)] = str(value)
        return out

    def _record_rate_limit(self, response: requests.Response) -> None:
        self._rate_limit_observations.append(
            RateLimitObservation(
                fetched_at=datetime.now(UTC),
                status_code=int(response.status_code),
                headers=self._rate_limit_headers(response.headers),
            )
        )

    def _retry_after_seconds(self, response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        text = str(raw).strip()
        try:
            return max(0.0, float(text))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(text)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    def _wait_for_slot(self) -> None:
        if self._last_request_started is None or self.min_request_interval_seconds <= 0:
            return
        elapsed = self._monotonic() - self._last_request_started
        remaining = self.min_request_interval_seconds - elapsed
        if remaining > 0:
            self._sleep(remaining)

    def _request_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        use_cache: bool = True,
    ) -> Any:
        key = self._cache_key(path, params)
        if use_cache and self.cache is not None:
            cached = self.cache.get_json(key)
            if cached is not None:
                return cached

        with self._request_lock:
            # Check again after acquiring the lock in case another caller filled the cache.
            if use_cache and self.cache is not None:
                cached = self.cache.get_json(key)
                if cached is not None:
                    return cached

            last_response: requests.Response | None = None
            for attempt in range(self.max_retries + 1):
                self._wait_for_slot()
                self._last_request_started = self._monotonic()
                response = self.session.get(
                    self._url(path),
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    timeout=self.timeout,
                )
                last_response = response
                self._record_rate_limit(response)

                retryable = response.status_code == 429 or 500 <= response.status_code < 600
                if retryable and attempt < self.max_retries:
                    retry_after = self._retry_after_seconds(response)
                    delay = retry_after if retry_after is not None else self.backoff_factor * (2**attempt)
                    if delay > 0:
                        self._sleep(delay)
                    continue

                response.raise_for_status()
                try:
                    payload = response.json()
                except requests.JSONDecodeError as exc:
                    raise ValueError(f"Limitless API returned invalid JSON for {path}") from exc

                if use_cache and self.cache is not None:
                    self.cache.set_json(key, payload)
                return payload

            if last_response is not None:
                last_response.raise_for_status()
            raise RuntimeError(f"Limitless API request failed without response: {path}")

    def get_games(self, *, use_cache: bool = True) -> list[dict[str, Any]]:
        payload = self._request_json("/games", use_cache=use_cache)
        if not isinstance(payload, list):
            raise TypeError("GET /games must return a JSON array")
        return payload

    def list_tournaments(
        self,
        *,
        game: str | None = None,
        format: str | None = None,
        organizer_id: int | None = None,
        page_size: int = 50,
        max_pages: int | None = None,
        max_items: int | None = None,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive when provided")
        if max_items is not None and max_items <= 0:
            raise ValueError("max_items must be positive when provided")

        out: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._request_json(
                "/tournaments",
                params={
                    "game": game,
                    "format": format,
                    "organizerId": organizer_id,
                    "limit": int(page_size),
                    "page": page,
                },
                use_cache=use_cache,
            )
            if not isinstance(payload, list):
                raise TypeError("GET /tournaments must return a JSON array")
            out.extend(payload)
            if max_items is not None and len(out) >= max_items:
                return out[:max_items]
            if len(payload) < page_size:
                break
            if max_pages is not None and page >= max_pages:
                break
            page += 1
        return out

    def get_tournament_details(self, tournament_id: str, *, use_cache: bool = True) -> dict[str, Any]:
        payload = self._request_json(f"/tournaments/{self._clean_id(tournament_id)}/details", use_cache=use_cache)
        if not isinstance(payload, dict):
            raise TypeError("tournament details must be a JSON object")
        return payload

    def get_tournament_standings(self, tournament_id: str, *, use_cache: bool = True) -> list[dict[str, Any]]:
        payload = self._request_json(f"/tournaments/{self._clean_id(tournament_id)}/standings", use_cache=use_cache)
        if not isinstance(payload, list):
            raise TypeError("tournament standings must be a JSON array")
        return payload

    def get_tournament_pairings(self, tournament_id: str, *, use_cache: bool = True) -> list[dict[str, Any]]:
        payload = self._request_json(f"/tournaments/{self._clean_id(tournament_id)}/pairings", use_cache=use_cache)
        if not isinstance(payload, list):
            raise TypeError("tournament pairings must be a JSON array")
        return payload

    @staticmethod
    def _clean_id(tournament_id: str) -> str:
        value = str(tournament_id).strip()
        if not value or "/" in value or "?" in value or "#" in value:
            raise ValueError("invalid tournament_id")
        return value


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_USER_AGENT",
    "JsonCache",
    "LimitlessTournamentApiClient",
    "RateLimitObservation",
]
