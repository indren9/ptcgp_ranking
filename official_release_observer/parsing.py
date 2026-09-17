"""DOM-contained, versioned authority and event profiles, not title heuristics."""
from datetime import datetime
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from . import AUTHORITY_POLICY_VERSION, PARSER_VERSION, SOURCE_ADAPTER_VERSION
from .timeparse import extract_time, pacific

HOST = "https://community.pokemon.com"
CATEGORY = HOST + "/en-us/categories/tcg-live-news-announcements"
DISCUSSION = re.compile(r"/en-us/discussion/(\d+)/[^/]+$")


def normalized(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def safe_url(url):
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and u.hostname in {"community.pokemon.com", "www.pokemon.com", "pokemon.com"}
                and not u.username and not u.password and u.port in (443, None) and not u.fragment
                and not any(c.isspace() for c in url))
    except ValueError:
        return False


def challenge(text):
    return any(x in text.lower() for x in ("pardon our interruption", "cf-chl-", "just a moment...", "verify you are human"))


def seal(record):
    data = {k: v for k, v in record.items() if k not in {"observation_id", "retrieved_at"}}
    record["observation_id"] = sha(stable(data))
    return record


def integrity(record):
    try:
        stamp = datetime.fromisoformat(record["retrieved_at"].replace("Z", "+00:00"))
        return (stamp.utcoffset() is not None and
                record.get("observation_id") == seal(dict(record))["observation_id"] and
                record.get("excerpt_sha256") == sha(record.get("excerpt", "")))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def clean_scope(scope):
    clean = BeautifulSoup(str(scope), "html.parser")
    for node in clean.select('blockquote, q, .Quote, .QuoteText, .UserQuote, script, style, aside, [hidden], [aria-hidden="true"], [data-testid="product-info-block-vg"]'):
        node.decompose()
    return clean


def classify(body, title, editorial=False):
    blocks = [normalized(n.get_text(" ", strip=True)) for n in body.select("p, li")
              if not n.find_parent(["li", "blockquote"])]
    if re.search(r"\b(?:postponed|delayed|rescheduled|correction|cancelled|canceled|except)\b|\bnot (?:yet )?(?:available|playable)\b", " ".join(blocks), re.I):
        return None, [], None, "EVENT_AMBIGUOUS"
    if editorial:
        # A deliberately narrow reviewed editorial sentence binds expansion and clock.
        pattern = re.compile(r"(?:L\s*eap|Jump) into the latest Pokémon Trading Card Game expansion,\s*(.+?),\s*in Pokémon Trading Card Game Live when it becomes available on (.+?)\.\s*(?:You|$)")
        for block in blocks:
            m = pattern.search(block)
            if m:
                name, temporal = m.groups()
                if "opportunity to play" not in block:
                    continue
                return name, [block], temporal, "expansion"
        # Reviewed date-only wording with explicit year, not nearby event end time.
        for block in blocks:
            m = re.fullmatch(r"(.+?) releases in Pokémon TCG Live on (.+?)\.", block)
            if m:
                return m[1], [block], m[2], "expansion"
        return None, [], None, "EVENT_AMBIGUOUS"
    playable = [b for b in blocks if re.fullmatch(r".{1,160} cards can be played in all game modes\.", b)]
    if len(playable) != 1:
        negative = re.search(r"maintenance|patch notes|trainer trials|build & battle|ranked ladder|battle pass|prerelease|rotation|code redemption", title, re.I)
        return None, [], None, "NOT_EXPANSION_EVENT" if negative else "EVENT_AMBIGUOUS"
    name = playable[0].removesuffix(" cards can be played in all game modes.")
    proposition = re.compile(r"We're excited to share (?:that )?the new " + re.escape(name) +
                             r" expansion (?:availability (?:with you )?today|is available starting today)[.!]", re.I)
    props = [b for b in blocks if proposition.fullmatch(b)]
    dates = [b for b in blocks if re.match(r"(?:Release Date:|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d)", b, re.I)]
    if len(props) != 1 or len(dates) != 1:
        return name, [], None, "EVENT_AMBIGUOUS"
    kind = "combined" if any(re.fullmatch(r"The \d{4} Pokémon TCG Standard format goes into effect\.", b) for b in blocks) else "expansion"
    return name, [props[0], dates[0], playable[0]], dates[0], kind


def observe(url, html, retrieved_at, *, api=None, redirect_chain=None):
    """A single source observation. Acceptance happens only after reconciliation."""
    dt = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("retrieval time must include timezone")
    try:
        tzversion = pacific()[1]
    except (OSError, ImportError):
        tzversion = "UNAVAILABLE"
    chain = list(redirect_chain or [url])
    r = dict(schema_version=1, source_tier="B", fetched_url=chain[0], canonical_url=url, redirect_chain=chain,
             source_id=None, title=None, category=None, author_id=None, author_name=None,
             role_markers=[], published_at=None, edited_at=None, raw_edit_label=None,
             retrieved_at=retrieved_at, verification_method="MACHINE_POLICY",
             authority_policy_version=AUTHORITY_POLICY_VERSION, parser_version=PARSER_VERSION,
             source_adapter_version=SOURCE_ADAPTER_VERSION, tzdb_version=tzversion,
             authority="AUTHORITY_UNVERIFIED", state="AUTHORITY_UNVERIFIED", expansion=None,
             provisional_identity=None, event_kind=None, time=None, excerpt="", excerpt_sha256=sha(""),
             content_digest=sha(normalized(html)), metadata_fingerprint=None, api_status="NOT_USED")
    def finish():
        r["metadata_fingerprint"] = sha(stable({k: r[k] for k in (
            "canonical_url", "source_id", "title", "category", "author_id", "author_name", "role_markers",
            "published_at", "edited_at", "raw_edit_label", "authority", "api_status")}))
        return seal(r)
    if (not safe_url(url) or chain[-1] != url or
            any(not safe_url(x) or urlsplit(x).hostname != urlsplit(url).hostname for x in chain)):
        return finish()
    if challenge(html):
        r["state"] = "CHALLENGE"
        return finish()
    soup = BeautifulSoup(html, "html.parser")
    canon = soup.select('link[rel="canonical"]')
    if len(canon) != 1 or not safe_url(canon[0].get("href", "")):
        return finish()
    canonical = canon[0]["href"]
    u, c = urlsplit(url), urlsplit(canonical)
    if u.hostname != c.hostname:
        return finish()
    r["canonical_url"] = canonical
    editorial = u.hostname != "community.pokemon.com"
    if editorial:
        article_path = re.compile(r"/us/(?:pokemon-news|news)/([^/]+)/?$")
        source_path, canonical_path = article_path.fullmatch(u.path), article_path.fullmatch(c.path)
        if not source_path or not canonical_path or source_path[1] != canonical_path[1]:
            return finish()
        scopes = soup.select("main > article, main > div > article")
        if len(scopes) != 1:
            r["state"] = "PARSER_DRIFT"
            return finish()
        scope = scopes[0]
        r.update(source_id=c.path, category="pokemon.com editorial", author_name="Pokémon editorial",
                 role_markers=["editorial_article"], title=normalized(soup.title.get_text()) if soup.title else None)
        published = soup.select_one("main time")
        r["published_at"] = published.get("datetime") if published else None
    else:
        uid, cid = DISCUSSION.fullmatch(u.path), DISCUSSION.fullmatch(c.path)
        if not uid or not cid or uid[1] != cid[1]:
            return finish()
        if any(not DISCUSSION.fullmatch(urlsplit(x).path) or
               DISCUSSION.fullmatch(urlsplit(x).path)[1] != cid[1] for x in chain):
            return finish()
        r["source_id"] = cid[1]
        posts = soup.select("div.ItemDiscussion")
        if len(posts) != 1 or posts[0].get("id") != "Discussion_" + cid[1]:
            return finish()
        post = posts[0]
        headers = post.select(".DiscussionHeader")
        if len(headers) != 1:
            return finish()
        header = headers[0]
        authors = header.select(".Author .Username[data-userid]")
        cats = header.select(".DiscussionMeta .Category a")
        roles = header.select(".AuthorInfo .RoleTitle")
        if len(authors) != 1 or len(cats) != 1 or len(roles) != 1:
            return finish()
        author, cat = authors[0], cats[0]
        role_classes = [x for x in post.get("class", []) if x.startswith(("Role_", "Rank-"))]
        r.update(author_id=author["data-userid"], author_name=normalized(author.get_text()),
                 role_markers=sorted(role_classes + [normalized(roles[0].get_text())]),
                 category=cat.get("href"), title=normalized(soup.h1.get_text()) if soup.h1 else None)
        profiles = header.select("[data-userid]")
        if (cat.get("href") != CATEGORY or normalized(cat.get_text()) != "Pokémon TCG Live News & Announcements"
                or set(role_classes) != {"Role_Administrator", "Rank-Admin"}
                or normalized(roles[0].get_text()) != "Administrator"
                or not r["author_id"].isdigit() or not r["author_name"]
                or any(x["data-userid"] != r["author_id"] for x in profiles)
                or author.get("href") != HOST + "/en-us/profile/" + r["author_name"]):
            return finish()
        scopes = post.select(".Item-Body > .Message.userContent")
        if len(scopes) != 1:
            return finish()
        scope = scopes[0]
        pub = header.select_one(".DateCreated time[datetime]")
        edit = header.select_one(".DateUpdated")
        r.update(published_at=pub["datetime"] if pub else None,
                 raw_edit_label=edit.get("title") if edit else None)
        if api is not None:
            # Optional metadata never replaces HTML authority or temporal semantics.
            if (not isinstance(api, dict) or type(api.get("discussionID")) is not int
                    or str(api["discussionID"]) != r["source_id"] or api.get("categoryID") != 20
                    or str(api.get("insertUserID")) != r["author_id"] or api.get("canonicalUrl") != canonical
                    or not isinstance(api.get("body"), str)
                    or normalized(BeautifulSoup(api["body"], "html.parser").get_text(" ", strip=True)) != normalized(scope.get_text(" ", strip=True))):
                r.update(state="API_SCHEMA_DRIFT", api_status="REJECTED")
                return finish()
            for field in ("dateInserted", "dateUpdated"):
                value = api.get(field)
                try:
                    if value is not None and datetime.fromisoformat(value).utcoffset() is None:
                        raise ValueError()
                except (ValueError, TypeError):
                    r.update(state="API_SCHEMA_DRIFT", api_status="REJECTED")
                    return finish()
            if api.get("dateInserted") != r["published_at"]:
                r.update(state="API_SCHEMA_DRIFT", api_status="REJECTED")
                return finish()
            r.update(edited_at=api.get("dateUpdated"), api_status="MATCHED")
    r["authority"] = "PASS"
    r["content_digest"] = sha(normalized(scope.get_text(" ", strip=True)))
    body = clean_scope(scope)
    name, excerpts, temporal, kind = classify(body, r["title"] or "", editorial)
    r.update(expansion=name, event_kind=kind, excerpt="\n".join(excerpts))
    r["excerpt_sha256"] = sha(r["excerpt"])
    if not temporal or len(r["excerpt"]) > 1200:
        r["state"] = kind if not temporal else "EVENT_AMBIGUOUS"
        return finish()
    r["provisional_identity"] = "ptcg:ptcgl:expansion:" + sha(normalized(name).casefold())[:24]
    r["time"] = extract_time(temporal)
    r["state"] = r["time"]["state"]
    return finish()
