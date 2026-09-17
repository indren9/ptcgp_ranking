"""Bounded GET-only transport with robots, validators and no challenge bypass."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

from .parsing import safe_url, challenge


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: str


def transport(url, headers):
    try:
        with requests.get(url, headers=headers, timeout=(10, 25), allow_redirects=False, stream=True) as response:
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 4_000_000:
                    raise FetchError("RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            body = b"".join(chunks).decode("utf-8-sig", errors="strict")
            return Response(response.status_code, {k.lower(): v for k, v in response.headers.items()}, body)
    except (requests.RequestException, UnicodeError) as exc:
        raise FetchError("TRANSPORT_FAILED: " + type(exc).__name__) from exc


class Fetcher:
    def __init__(self, send=transport, *, budget=80, retries=2, spacing=2.0,
                 sleep=time.sleep, now=time.time):
        if not 1 <= budget <= 500 or not 0 <= retries <= 3 or spacing < 2:
            raise ValueError("invalid bounded transport configuration")
        self.send, self.budget, self.retries, self.spacing = send, budget, retries, spacing
        self.sleep, self.now = sleep, now
        self.requests = 0
        self.last_request = None
        self.cache, self.robots = {}, {}
        self.receipts = []

    def _request(self, url):
        if not safe_url(url):
            raise FetchError("UNAPPROVED_URL")
        cached = self.cache.get(url)
        if cached and cached[1] > self.now():
            self.receipts.append(dict(url=url, status="CACHE_FRESH"))
            return cached[0]
        headers = {"User-Agent": "OfficialReleaseObserver/1.0 (read-only research)", "Accept": "text/html, application/xml, application/json"}
        if cached:
            for field, header in (("etag", "If-None-Match"), ("last-modified", "If-Modified-Since")):
                if field in cached[0].headers:
                    headers[header] = cached[0].headers[field]
        for attempt in range(self.retries + 1):
            if self.requests >= self.budget:
                raise FetchError("REQUEST_BUDGET_EXHAUSTED")
            if self.last_request is not None:
                self.sleep(max(0, self.spacing - (self.now() - self.last_request)))
            self.last_request = self.now()
            self.requests += 1
            try:
                response = self.send(url, headers)
            except FetchError:
                if attempt == self.retries:
                    raise
                self.sleep(min(2 ** attempt, 30))
                continue
            response = Response(response.status, {k.lower(): v for k, v in response.headers.items()}, response.body)
            self.receipts.append(dict(url=url, status=response.status))
            if response.status in {429, 500, 502, 503, 504}:
                if attempt == self.retries:
                    raise FetchError("HTTP_" + str(response.status))
                retry = response.headers.get("retry-after", "")
                try:
                    delay = float(retry) if retry else 2 ** attempt
                except ValueError:
                    try:
                        delay = parsedate_to_datetime(retry).timestamp() - self.now()
                    except (TypeError, ValueError, OverflowError):
                        raise FetchError("INVALID_RETRY_AFTER")
                if delay > 60:
                    raise FetchError("RETRY_DEFERRED")
                self.sleep(max(0, delay))
                continue
            if response.status == 304:
                if not cached:
                    raise FetchError("304_WITHOUT_SNAPSHOT")
                response = Response(200, {**cached[0].headers, **response.headers}, cached[0].body)
            if response.status == 200:
                if challenge(response.body):
                    raise FetchError("CHALLENGE")
                control = response.headers.get("cache-control", "").lower()
                if "no-store" not in control:
                    m = re.search(r"(?:^|,)\s*max-age=(\d+)", control)
                    age = int(response.headers.get("age", "0")) if response.headers.get("age", "0").isdigit() else 0
                    if response.headers.get("date"):
                        try:
                            age = max(age, int(self.now() - parsedate_to_datetime(response.headers["date"]).timestamp()))
                        except (ValueError, TypeError, OverflowError):
                            age = 86400
                    ttl = max(0, int(m[1]) - age) if m and "no-cache" not in control else 0
                    self.cache[url] = (response, self.now() + min(ttl, 86400))
                else:
                    self.cache.pop(url, None)
            return response
        raise FetchError("RETRY_EXHAUSTED")

    def _allowed(self, url):
        u = urlsplit(url)
        origin = u.scheme + "://" + u.netloc
        if origin not in self.robots:
            robots = self._request(origin + "/robots.txt")
            parser = RobotFileParser()
            if robots.status == 404:
                parser.parse([])
            elif robots.status == 200:
                parser.parse(robots.body.splitlines())
            else:
                raise FetchError("ROBOTS_UNAVAILABLE")
            self.robots[origin] = parser
        parser = self.robots[origin]
        delay = parser.crawl_delay("OfficialReleaseObserver")
        if delay:
            self.spacing = max(self.spacing, delay)
        rate = parser.request_rate("OfficialReleaseObserver")
        if rate and rate.requests:
            self.spacing = max(self.spacing, rate.seconds / rate.requests)
        if not parser.can_fetch("OfficialReleaseObserver", url):
            raise FetchError("ROBOTS_DISALLOWED")

    def get(self, url):
        original_host = urlsplit(url).hostname
        chain = []
        for _ in range(4):
            if not safe_url(url) or urlsplit(url).hostname != original_host:
                raise FetchError("UNAPPROVED_REDIRECT")
            self._allowed(url)
            response = self._request(url)
            chain.append(url)
            if response.status in {301, 302, 303, 307, 308}:
                if not response.headers.get("location"):
                    raise FetchError("REDIRECT_WITHOUT_LOCATION")
                url = urljoin(url, response.headers["location"])
                continue
            if response.status != 200:
                raise FetchError("HTTP_" + str(response.status))
            return response, tuple(chain)
        raise FetchError("REDIRECT_LIMIT")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
