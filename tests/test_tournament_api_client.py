from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import json

import pytest
import requests

from sources.limitless.tournament_api.client import LimitlessTournamentApiClient


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = deque(responses)
        self.calls = []
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.popleft()

    def close(self):
        self.closed = True


class FakeCache:
    def __init__(self):
        self.data = {}
        self.gets = []
        self.sets = []

    def get_json(self, key):
        self.gets.append(key)
        return self.data.get(key)

    def set_json(self, key, value):
        self.sets.append(key)
        self.data[key] = value


def test_client_sets_json_headers_and_uses_only_documented_games_endpoint():
    session = FakeSession([FakeResponse(200, [{"id": "POCKET"}])])
    client = LimitlessTournamentApiClient(session=session)

    result = client.get_games(use_cache=False)

    assert result == [{"id": "POCKET"}]
    assert session.headers["Accept"] == "application/json"
    assert "PTCGP-Ranking" in session.headers["User-Agent"]
    assert session.calls[0][0].endswith("/games")


def test_cache_first_avoids_network_request():
    cache = FakeCache()
    key = "https://play.limitlesstcg.com/api/games"
    cache.data[key] = [{"id": "POCKET"}]
    session = FakeSession([])
    client = LimitlessTournamentApiClient(session=session, cache=cache)

    assert client.get_games() == [{"id": "POCKET"}]
    assert session.calls == []


def test_tournaments_pagination_uses_limit_and_page_until_short_page():
    session = FakeSession(
        [
            FakeResponse(200, [{"id": "a"}, {"id": "b"}]),
            FakeResponse(200, [{"id": "c"}]),
        ]
    )
    client = LimitlessTournamentApiClient(session=session)

    rows = client.list_tournaments(game="POCKET", page_size=2, use_cache=False)

    assert [row["id"] for row in rows] == ["a", "b", "c"]
    assert session.calls[0][1] == {"game": "POCKET", "limit": 2, "page": 1}
    assert session.calls[1][1] == {"game": "POCKET", "limit": 2, "page": 2}


def test_429_respects_retry_after_and_records_rate_limit_headers():
    sleeps = []
    session = FakeSession(
        [
            FakeResponse(429, {"error": "slow"}, {"Retry-After": "3", "X-RateLimit-Remaining": "0"}),
            FakeResponse(200, [{"id": "POCKET"}], {"X-RateLimit-Remaining": "9"}),
        ]
    )
    client = LimitlessTournamentApiClient(
        session=session,
        max_retries=1,
        sleep_fn=sleeps.append,
    )

    assert client.get_games(use_cache=False) == [{"id": "POCKET"}]
    assert sleeps == [3.0]
    assert len(client.rate_limit_observations) == 2
    assert client.rate_limit_observations[0].headers["X-RateLimit-Remaining"] == "0"


def test_5xx_uses_exponential_backoff_then_succeeds():
    sleeps = []
    session = FakeSession(
        [FakeResponse(503, {}), FakeResponse(502, {}), FakeResponse(200, [])]
    )
    client = LimitlessTournamentApiClient(
        session=session,
        max_retries=2,
        backoff_factor=0.5,
        sleep_fn=sleeps.append,
    )

    assert client.get_games(use_cache=False) == []
    assert sleeps == [0.5, 1.0]


def test_endpoint_helpers_use_documented_tournament_paths():
    session = FakeSession(
        [
            FakeResponse(200, {"id": "abc"}),
            FakeResponse(200, []),
            FakeResponse(200, []),
        ]
    )
    client = LimitlessTournamentApiClient(session=session)

    client.get_tournament_details("abc", use_cache=False)
    client.get_tournament_standings("abc", use_cache=False)
    client.get_tournament_pairings("abc", use_cache=False)

    assert [call[0].rsplit("/api", 1)[-1] for call in session.calls] == [
        "/tournaments/abc/details",
        "/tournaments/abc/standings",
        "/tournaments/abc/pairings",
    ]


def test_invalid_tournament_id_cannot_change_path():
    client = LimitlessTournamentApiClient(session=FakeSession([]))
    with pytest.raises(ValueError, match="invalid tournament_id"):
        client.get_tournament_details("../games")


def test_concrete_file_json_cache_roundtrip(tmp_path):
    from storage.acquisition import FileJsonCache

    now = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    cache = FileJsonCache(
        tmp_path / "cache",
        ttl_min=60,
        now_fn=lambda: now,
    )
    cache.set_json("key-a", {"x": [1, 2, 3]})
    assert cache.get_json("key-a") == {"x": [1, 2, 3]}
    assert cache.get_json("missing") is None


def test_file_json_cache_fresh_timestamped_entry_is_hit(tmp_path):
    from storage.acquisition import FileJsonCache

    clock = [datetime(2026, 8, 25, 10, 0, tzinfo=UTC)]
    cache = FileJsonCache(
        tmp_path / "cache",
        ttl_min=10,
        now_fn=lambda: clock[0],
    )

    cache.set_json("fresh", {"value": 1})
    clock[0] += timedelta(minutes=10)

    assert cache.get_json("fresh") == {"value": 1}
    assert cache.hit_count == 1
    assert cache.expired_miss_count == 0


def test_file_json_cache_expired_entry_is_miss(tmp_path):
    from storage.acquisition import FileJsonCache

    clock = [datetime(2026, 8, 25, 10, 0, tzinfo=UTC)]
    cache = FileJsonCache(
        tmp_path / "cache",
        ttl_min=10,
        now_fn=lambda: clock[0],
    )

    cache.set_json("expired", {"value": 1})
    clock[0] += timedelta(minutes=10, seconds=1)

    assert cache.get_json("expired") is None
    assert cache.expired_miss_count == 1


def test_file_json_cache_legacy_entry_without_cached_at_is_miss(tmp_path):
    from storage.acquisition import FileJsonCache

    now = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    cache = FileJsonCache(
        tmp_path / "cache",
        ttl_min=60,
        now_fn=lambda: now,
    )

    cache._path("legacy").write_text(
        json.dumps(
            {
                "cache_key": "legacy",
                "value": {"old": True},
            }
        ),
        encoding="utf-8",
    )

    assert cache.get_json("legacy") is None
    assert cache.legacy_miss_count == 1


def test_file_json_cache_ttl_zero_disables_persistent_read(tmp_path):
    from storage.acquisition import FileJsonCache

    now = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    cache = FileJsonCache(
        tmp_path / "cache",
        ttl_min=0,
        now_fn=lambda: now,
    )

    cache.set_json("zero", {"value": 1})

    assert cache.get_json("zero") is None
    assert cache.hit_count == 0
    assert cache.expired_miss_count == 1
