import hashlib

from sources.limitless.client import cache_is_fresh, fetch_html, make_session


def test_limitless_make_session_sets_timeout_and_user_agent():
    session = make_session(user_agent="test-agent", timeout=7)

    assert session.request_timeout == 7
    assert session.headers["User-Agent"] == "test-agent"


def test_limitless_fetch_html_uses_fresh_cache(tmp_path):
    url = "https://example.com/decks"
    cache_file = tmp_path / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}.html"
    cache_file.write_text("<html>cached</html>", encoding="utf-8")

    class NoNetworkSession:
        def get(self, *args, **kwargs):
            raise AssertionError("network should not be called on cache hit")

    metrics = []
    html, from_cache = fetch_html(
        url,
        session=NoNetworkSession(),
        cache_dir=tmp_path,
        ttl_minutes=60,
        force_refresh=False,
        rate_limit_seconds=0,
        metrics=metrics,
    )

    assert cache_is_fresh(cache_file, ttl_minutes=60)
    assert html == "<html>cached</html>"
    assert from_cache is True
    assert metrics[0]["cache_hit"] is True
    assert metrics[0]["delay_seconds"] == 0.0


def test_limitless_fetch_html_applies_jittered_delay(tmp_path, monkeypatch):
    sleeps = []

    class DummyResponse:
        text = "<html>fresh</html>"

        def raise_for_status(self):
            return None

    class DummySession:
        request_timeout = 1

        def get(self, url, timeout=1):
            return DummyResponse()

    monkeypatch.setattr("sources.limitless.client.random.uniform", lambda low, high: 1.25)
    monkeypatch.setattr("sources.limitless.client.time.sleep", lambda seconds: sleeps.append(seconds))

    metrics = []
    html, from_cache = fetch_html(
        "https://example.com/decks",
        session=DummySession(),
        cache_dir=tmp_path,
        ttl_minutes=60,
        force_refresh=True,
        rate_limit_seconds=4.0,
        rate_limit_jitter_frac=0.5,
        metrics=metrics,
    )

    assert html == "<html>fresh</html>"
    assert from_cache is False
    assert sleeps == [5.0]
    assert metrics[0]["cache_hit"] is False
    assert metrics[0]["delay_seconds"] == 5.0
    assert metrics[0]["elapsed_seconds"] >= 0.0
