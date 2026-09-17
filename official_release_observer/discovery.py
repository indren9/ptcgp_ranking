"""RSS + advertised sitemap reconciliation + category landing, bounded by IDs."""
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .network import FetchError
from .parsing import CATEGORY, HOST, DISCUSSION, safe_url


def xml(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations unsupported")
    return ET.fromstring(text)


def discover(fetcher, *, max_sources=40, known_urls=()):
    if not 1 <= max_sources <= 200:
        raise ValueError("source bound must be 1..200")
    urls, errors, surfaces = {}, [], []
    def add(url, surface):
        if safe_url(url) and urlsplit(url).hostname == "community.pokemon.com":
            match = DISCUSSION.fullmatch(urlsplit(url).path)
            if match:
                urls.setdefault(match[1], url)
                surfaces.append({"id": match[1], "surface": surface})
    for url in known_urls:
        add(url, "known_revision_recheck")
    try:
        response, _ = fetcher.get(CATEGORY)
        landing = BeautifulSoup(response.body, "html.parser")
        if not landing.h1 or "TCG Live News & Announcements" not in landing.h1.get_text():
            raise ValueError("CATEGORY_SCHEMA_DRIFT")
        links = landing.select('link[rel="alternate"][type="application/rss+xml"]')
        feeds = [x.get("href") for x in links if x.get("href") == CATEGORY + "/feed.rss"]
        if len(feeds) != 1:
            raise ValueError("RSS_NOT_ADVERTISED")
        try:
            feed, _ = fetcher.get(feeds[0])
            root = xml(feed.body)
            if root.tag != "rss" or root.find("channel") is None:
                raise ValueError("RSS_SCHEMA_DRIFT")
            for item in root.findall("./channel/item"):
                add(item.findtext("link", ""), "rss")
        except (FetchError, ValueError, ET.ParseError) as exc:
            errors.append("RSS: " + str(exc))
        for a in landing.select('a[href]'):
            add(a["href"], "landing")
    except (FetchError, ValueError) as exc:
        errors.append("LANDING: " + str(exc))
    try:
        robots, _ = fetcher.get(HOST + "/robots.txt")
        index_url = HOST + "/sitemapindex.xml"
        if not any(line.strip().lower() == "sitemap: " + index_url.lower() for line in robots.body.splitlines()):
            raise ValueError("SITEMAP_NOT_ADVERTISED")
        response, _ = fetcher.get(index_url)
        index = xml(response.body)
        shards = [n.text for n in index.findall("{*}sitemap/{*}loc")
                  if n.text and n.text.startswith(HOST + "/en-us/sitemap-category-tcg-live-news-announcements-")]
        if not shards or len(shards) > 10:
            raise ValueError("SITEMAP_SHARD_BOUND_OR_SCHEMA")
        for shard in shards:
            response, _ = fetcher.get(shard)
            root = xml(response.body)
            if not root.tag.endswith("urlset"):
                raise ValueError("SITEMAP_SCHEMA_DRIFT")
            for node in root.findall("{*}url/{*}loc"):
                add(node.text or "", "sitemap")
    except (FetchError, ValueError, ET.ParseError) as exc:
        errors.append("SITEMAP: " + str(exc))
    # Known URLs first for revisions, then sources in stable discovery order.
    selected = list(urls.values())[:max_sources]
    if not urls:
        errors.append("NO_DISCUSSION_URLS: discovery coverage cannot be established")
    if len(urls) > max_sources:
        errors.append("SOURCE_BOUND: remaining URLs require reconciliation in a later run")
    return dict(urls=selected, discovered_count=len(urls), coverage_errors=errors,
                complete=not errors, surfaces=surfaces)
