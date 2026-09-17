"""Offline official excerpts, adversarial DOM and deterministic HTTP replay."""
from copy import deepcopy
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import socket

import pytest

from official_release_observer.parsing import observe, integrity, CATEGORY, HOST, sha
from official_release_observer.timeparse import extract_time
from official_release_observer.reconcile import reconcile, revision
from official_release_observer.network import Fetcher, FetchError, Response
from official_release_observer.discovery import discover
from official_release_observer import storage
from release_candidates.observer_bridge import machine_drafts, phase_b_draft

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests/fixtures/official_release_observer/official_excerpts.json").read_text(encoding="utf-8"))
STAMP = "2026-09-17T12:00:00Z"
PROTECTED = [ROOT / p for p in ("data/reference/ptcg_live_windows.json", "data/reference/pocket_releases.json",
    ".github/tcg-live-latest-completed-meta-state.json", "requirements.txt", ".github/workflows/update-expansion-catalog.yml")]


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    before = [p.read_bytes() for p in PROTECTED]
    def forbidden(*a, **kw):
        raise AssertionError("offline tests must not use network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    yield
    assert [p.read_bytes() for p in PROTECTED] == before


def dom(f=None):
    f = f or FIXTURES[0]
    esc = lambda key: html.escape(str(f[key]), quote=True)
    edit = '<span class="DateUpdated" title="' + html.escape(f['edit_label'], quote=True) + '"></span>' if f['edit_label'] else ''
    return f'''<html><head><title>{esc('title')}</title><link rel="canonical" href="{esc('canonical_url')}"></head>
    <body><h1>{esc('title')}</h1><div id="Discussion_{esc('discussion_id')}" class="ItemDiscussion Role_Administrator Rank-Admin">
    <div class="DiscussionHeader"><span class="Author"><a class="Username" data-userid="{esc('author_id')}" href="{HOST}/en-us/profile/{esc('author')}">{esc('author')}</a></span>
    <span class="AuthorInfo"><span class="RoleTitle">Administrator</span></span>
    <div class="DiscussionMeta"><span class="Category"><a href="{esc('category_url')}">{esc('category')}</a></span>
    <span class="DateCreated"><time datetime="{esc('published_at')}"></time></span>{edit}</div></div>
    <div class="Item-Body"><div class="Message userContent">{f['body']}</div></div></div></body></html>'''


def observation(markup=None, f=None, **kw):
    f = f or FIXTURES[0]
    return observe(f["canonical_url"], markup or dom(f), STAMP, **kw)


def accepted(o):
    return reconcile([o])[0]["accepted_exact_timestamp"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
def test_official_fixture(fixture):
    o = observation(f=fixture)
    assert integrity(o)
    assert o["authority"] == "PASS"
    assert o["state"] == fixture["expected"]
    if o["state"] == "EXACT":
        assert accepted(o).endswith("T17:00:00Z")
        assert o["time"]["date"] in accepted(o)
        assert o["tzdb_version"].startswith("tzdata:")
        assert o["verification_method"] == "MACHINE_POLICY"
    else:
        assert accepted(o) is None


@pytest.mark.parametrize("text,utc,state", [
    ("September 15, 2026 - 10:00 AM PT (17:00 UTC)", "2026-09-15T17:00:00Z", "EXACT"),
    ("January 29, 2026 - 9:00 AM PT (17:00 UTC)", "2026-01-29T17:00:00Z", "EXACT"),
    ("September 15, 2026 - 10:00 AM PT", "2026-09-15T17:00:00Z", "EXACT"),
    ("September 15, 2026 – 10:00 a.m. pdt", "2026-09-15T17:00:00Z", "EXACT"),
    ("January 29, 2026 - 9:00 AM PST", "2026-01-29T17:00:00Z", "EXACT"),
    ("Thursday, March 30th, 2023 - 10:00 AM PDT (17:00 UTC)", "2023-03-30T17:00:00Z", "EXACT"),
    ("September 15, 2026 - 11:30 PM PT (06:30 UTC)", "2026-09-16T06:30:00Z", "EXACT"),
    ("September 15, 2026 - 11:30 PM PT (2026-09-16 06:30:00 UTC)", "2026-09-16T06:30:00Z", "EXACT"),
    ("September 15, 2026 - 5:00 PM UTC", "2026-09-15T17:00:00Z", "EXACT"),
    ("September 15, 2026", None, "DATE_CONFIRMED"),
    ("September 15, 2026 - 10:00 AM", None, "TIMEZONE_AMBIGUOUS"),
    ("September 15, 2026 - 10:00 AM CST", None, "TIMEZONE_AMBIGUOUS"),
    ("January 29, 2026 - 9:00 AM PDT", None, "INVALID_LOCAL_TIME"),
    ("September 15, 2026 - 10:00 AM PST", None, "INVALID_LOCAL_TIME"),
    ("September 15, 2026 - 9:00 AM PT (17:00 UTC)", None, "OFFICIAL_CONFLICT"),
    ("Monday, September 15, 2026 - 10:00 AM PT", None, "OFFICIAL_CONFLICT"),
    ("March 8, 2026 - 2:30 AM PT", None, "INVALID_LOCAL_TIME"),
    ("November 1, 2026 - 1:30 AM PT", None, "TIMEZONE_AMBIGUOUS"),
    ("November 1, 2026 - 1:30 AM PT (08:30 UTC)", "2026-11-01T08:30:00Z", "EXACT"),
    ("November 1, 2026 - 1:30 AM PST", "2026-11-01T09:30:00Z", "EXACT"),
    ("November 1, 2026 - 1:30 AM -07:00", "2026-11-01T08:30:00Z", "EXACT"),
    ("November 1, 2026 - 1:30 AM UTC-08:00", "2026-11-01T09:30:00Z", "EXACT"),
    ("February 30, 2026 - 10:00 AM PT", None, "TIME_UNPARSEABLE"),
    ("September 15, 2026 - 25:00 AM PT", None, "TIME_UNPARSEABLE"),
    ("Tomorrow at reset", None, "TIME_UNPARSEABLE"),
    ("September 15, 2026 - 10:00 AM PT (17:00:01 UTC)", None, "OFFICIAL_CONFLICT"),
    ("September 15, 2026 - 11:30 PM PT (2026-09-15 06:30 UTC)", None, "OFFICIAL_CONFLICT"),
])
def test_civil_time(text, utc, state):
    t = extract_time(text)
    assert t["state"] == state
    assert t["calculated_utc"] == utc


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace("Role_Administrator", "Role_Member"),
    lambda s: s.replace("Rank-Admin", "Rank-NewAdmin"),
    lambda s: s.replace('<span class="RoleTitle">Administrator</span>', ''),
    lambda s: s.replace('class="RoleTitle">Administrator', 'class="RoleTitle">Moderator'),
    lambda s: s.replace('class="Category"><a href="' + CATEGORY, 'class="Category"><a href="' + HOST + '/en-us/categories/other'),
    lambda s: s.replace('data-userid="62799"', 'data-userid="not-an-id"'),
    lambda s: s.replace('class="DiscussionHeader">', 'class="DiscussionHeader"><a data-userid="123">other</a>'),
    lambda s: s.replace('class="Username"', 'class="NotUsername"'),
    lambda s: s.replace('id="Discussion_26004"', 'id="Discussion_99999"'),
    lambda s: s.replace('rel="canonical"', 'rel="other"'),
    lambda s: s.replace('<link rel="canonical"', '<link rel="canonical" href="https://evil.example"><link rel="canonical"'),
    lambda s: s.replace('class="Item-Body"', 'class="Comment"'),
    lambda s: '<html><title>Pardon Our Interruption</title></html>',
])
def test_authority_adversaries(mutation):
    assert accepted(observation(mutation(dom()))) is None


@pytest.mark.parametrize("prefix", ['http://community.pokemon.com', 'https://community.pokemon.com.evil.test',
    'https://user@community.pokemon.com', 'https://community.pokemon.com:444'])
def test_host_policy(prefix):
    o = observe(FIXTURES[0]['canonical_url'].replace(HOST, prefix), dom(), STAMP)
    assert accepted(o) is None


def test_new_administrator_and_global_banner():
    markup = dom().replace('TPCi_GlowingMoon', 'TPCi_NewAccount').replace('62799', '987654')
    markup = markup.replace('<body>', '<body><aside>Maintenance September 16, 2026 - 8:00 AM PT (15:00 UTC)</aside>')
    assert accepted(observation(markup)) == '2026-09-15T17:00:00Z'


@pytest.mark.parametrize("wrapper", ['blockquote', 'q', 'div class="Quote"', 'div hidden', 'div aria-hidden="true"'])
def test_quoted_or_hidden_release_is_not_authority(wrapper):
    f = dict(FIXTURES[0]); f['body'] = '<' + wrapper + '>' + f['body'] + '</' + wrapper.split()[0] + '>'
    assert accepted(observation(f=f)) is None


def test_reply_and_fake_admin_body():
    f = dict(FIXTURES[0]); f['body'] = '<p>I am Administrator Rank-Admin.</p>'
    markup = dom(f).replace('</body>', '<div class="ItemComment">' + FIXTURES[0]['body'] + '</div></body>')
    assert accepted(observation(markup)) is None
    assert accepted(observation(markup.replace('Role_Administrator', 'Role_Member'))) is None


@pytest.mark.parametrize('event', ['maintenance', 'patch notes', 'Ranked Ladder', 'Trainer Trials', 'Build & Battle',
    'Battle Pass', 'physical release', 'prerelease', 'code redemption', 'individual-card early availability', 'rotation-only'])
def test_title_and_event_time_are_not_full_release(event):
    f = dict(FIXTURES[0]); f['body'] = '<p>Release Date: September 15, 2026 - 10:00 AM PT</p><p>' + event + ' begins.</p>'
    assert accepted(observation(f=f)) is None


def test_negated_proposition_and_ambiguous_dates():
    assert accepted(observation(dom().replace("We&#x27;re excited to share", "We are not confirming"))) is None
    f = dict(FIXTURES[0]); f['body'] += '<p>September 16, 2026 - 10:00 AM PT</p>'
    assert accepted(observation(f=f)) is None
    assert accepted(observation(dom().replace('cards can be played', 'cards cannot be played'))) is None
    f = dict(FIXTURES[0]); f['body'] += '<p>The release has been postponed.</p>'
    assert accepted(observation(f=f)) is None


def test_api_schema_and_matching_metadata():
    f = FIXTURES[0]
    api = dict(discussionID=26004, categoryID=20, insertUserID=62799, canonicalUrl=f['canonical_url'],
               body=f['body'], dateInserted=f['published_at'], dateUpdated='2026-09-15T17:11:34+00:00')
    o = observation(api=api)
    assert o['api_status'] == 'MATCHED' and accepted(o)
    assert o['edited_at'] == api['dateUpdated']
    for change in [dict(body=None), dict(discussionID='26004'), dict(categoryID=8), dict(insertUserID=5),
                   dict(dateUpdated='unknown'), dict(body='<p>different</p>'), dict(dateInserted='2020-01-01T00:00:00Z')]:
        bad = observation(api={**api, **change})
        assert bad['state'] == 'API_SCHEMA_DRIFT'
        assert accepted(bad) is None


def test_official_conflicts_date_only_and_integrity():
    a = observation()
    b = observation(dom().replace('10:00 AM PT (17:00 UTC)', '11:00 AM PT (18:00 UTC)'))
    c = reconcile([a, b])[0]
    assert c['state'] == 'OFFICIAL_CONFLICT' and c['accepted_exact_timestamp'] is None
    date = observation(dom().replace(' - 10:00 AM PT (17:00 UTC)', ''))
    assert date['state'] == 'DATE_CONFIRMED'
    assert reconcile([a, date])[0]['accepted_exact_timestamp'] == accepted(a)
    assert reconcile([a, a])[0]['corroboration'] is None
    f = dict(FIXTURES[0], discussion_id='99999', canonical_url=FIXTURES[0]['canonical_url'].replace('/26004/', '/99999/'))
    assert reconcile([a, observation(f=f)])[0]['corroboration'] == 'CORROBORATED'
    bad = deepcopy(a); bad['time']['calculated_utc'] = '2026-09-15T00:00:00Z'
    with pytest.raises(ValueError, match='INTEGRITY'):
        reconcile([bad])


def test_revisions_and_fixed_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'ROOT', tmp_path / 'observer')
    store = storage.Store(); a = observation()
    assert revision(None, a) == ('DISCOVERED', False)
    cover = {'complete': True}
    assert store.save([a], cover, STAMP)['candidates'][0]['accepted_exact_timestamp']
    newer = dict(a, retrieved_at='2026-09-18T12:00:00Z')
    assert revision(a, newer) == ('UNCHANGED', False)
    store.save([newer], cover, newer['retrieved_at'])
    assert len(list((store.root / 'observations').glob('*.json'))) == 1
    assert len(store.receipts()) == 2
    edited = observation(dom().replace('</div></div></div></body>', '<p>Editorial note.</p></div></div></div></body>'))
    assert revision(a, edited) == ('SOURCE_CHANGED', False)
    store.save([edited], cover, STAMP)
    changed = observation(dom().replace('10:00 AM PT (17:00 UTC)', '11:00 AM PT (18:00 UTC)'))
    assert revision(edited, changed) == ('SOURCE_CHANGED', True)
    report = store.save([changed], cover, STAMP)
    assert report['candidates'][0]['state'] == 'QUARANTINED'
    assert report['candidates'][0]['accepted_exact_timestamp'] is None
    assert store.save([changed], cover, STAMP)['candidates'][0]['state'] == 'QUARANTINED'
    original = store.root / 'observations' / (a['observation_id'] + '.json')
    assert json.loads(original.read_text(encoding='utf-8')) == a


def test_storage_corruption_and_incomplete_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'ROOT', tmp_path / 'observer')
    store = storage.Store(); a = observation()
    assert store.save([a], {'complete':False}, STAMP)['candidates'][0]['accepted_exact_timestamp'] is None
    path = store.root / 'observations' / (a['observation_id'] + '.json')
    path.write_text('{}')
    with pytest.raises(ValueError):
        store.latest()


def test_bridge_preserves_human_semantics():
    a = observation()
    draft = machine_drafts([a])[0]
    assert draft['state'] == 'MACHINE_VERIFIED' and draft['identity_status'] == 'IDENTITY_UNMAPPED'
    assert not draft['authority_verified'] and not draft['identity_verified']
    mapping = dict(release_code='SYNTH', event_key='release:synth', release_name=a['expansion'], review_reference='test-reviewed-registry')
    candidate = phase_b_draft(a, mapping)
    assert candidate.candidate_state == 'DISCOVERED'
    assert candidate.effective_utc_datetime == accepted(a)
    assert not candidate.identity_verified and not candidate.evidence[0].authority_verified
    assert not candidate.evidence[0].eligible


class Replay:
    def __init__(self, routes):
        self.routes, self.calls, self.clock, self.waits = routes, [], 0.0, []
    def send(self, url, headers):
        self.calls.append((url, headers))
        value = self.routes[url]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    def wait(self, n):
        self.waits.append(n); self.clock += n
    def fetcher(self, **kw):
        return Fetcher(self.send, now=lambda:self.clock, sleep=self.wait, **kw)


def resp(body='', status=200, **headers):
    return Response(status, headers, body)


def test_cache_validators_and_304():
    url = FIXTURES[0]['canonical_url']
    replay = Replay({HOST+'/robots.txt':resp('User-agent: *\nAllow: /'),
                     url:[resp('hello', etag='"v1"', **{'cache-control':'max-age=10'}), resp('',304)]})
    fetcher = replay.fetcher()
    assert fetcher.get(url)[0].body == 'hello'
    assert fetcher.get(url)[0].body == 'hello'
    assert len([u for u,h in replay.calls if u==url]) == 1
    replay.clock += 11
    assert fetcher.get(url)[0].body == 'hello'
    assert replay.calls[-1][1]['If-None-Match'] == '"v1"'


@pytest.mark.parametrize('failure', [resp('',403),resp('Pardon Our Interruption'),resp('',304),
    resp('',302,location='https://evil.test/'), FetchError('timeout')])
def test_network_fail_closed(failure):
    url=FIXTURES[0]['canonical_url']; r=Replay({HOST+'/robots.txt':resp('User-agent: *\nAllow: /'),url:failure})
    with pytest.raises(FetchError):r.fetcher().get(url)
    assert len(r.calls)<=4


def test_robots_retry_after_and_budget():
    url=FIXTURES[0]['canonical_url']
    r=Replay({HOST+'/robots.txt':resp('User-agent: *\nDisallow: /en-us/discussion/')})
    with pytest.raises(FetchError,match='ROBOTS_DISALLOWED'):r.fetcher().get(url)
    assert len(r.calls)==1
    r=Replay({HOST+'/robots.txt':resp('User-agent: *\nAllow: /'),url:[resp('',429,**{'retry-after':'7'}),resp('ok')]})
    assert r.fetcher().get(url)[0].body=='ok' and 7 in r.waits
    r=Replay({HOST+'/robots.txt':resp('User-agent: *\nAllow: /'),url:resp('',429,**{'retry-after':'300'})})
    with pytest.raises(FetchError,match='RETRY_DEFERRED'):r.fetcher().get(url)
    with pytest.raises(FetchError,match='BUDGET'):r.fetcher(budget=1).get(url)


def discovery_replay(missing_feed=False):
    a,b=FIXTURES[0]['canonical_url'],FIXTURES[1]['canonical_url']
    sitemap=HOST+'/en-us/sitemap-category-tcg-live-news-announcements-1-1000.xml'
    return Replay({HOST+'/robots.txt':resp('User-agent: *\nDisallow: /search/\nSitemap: '+HOST+'/sitemapindex.xml'),
        CATEGORY:resp('<h1>Pokémon TCG Live News &amp; Announcements</h1><link rel="alternate" type="application/rss+xml" href="'+CATEGORY+'/feed.rss"><a href="'+a+'">release</a>'),
        CATEGORY+'/feed.rss':resp('<rss><channel>'+('' if missing_feed else '<item><link>'+a+'</link></item>')+'</channel></rss>'),
        HOST+'/sitemapindex.xml':resp('<sitemapindex><sitemap><loc>'+sitemap+'</loc></sitemap></sitemapindex>'),
        sitemap:resp('<urlset><url><loc>'+a+'</loc></url><url><loc>'+b+'</loc></url></urlset>')})


def test_missing_rss_items_sitemap_reconciliation_and_dedup():
    r=discovery_replay(True); result=discover(r.fetcher())
    assert result['complete'] and result['discovered_count']==2 and len(result['urls'])==2
    assert any(x['surface']=='sitemap' for x in result['surfaces'])
    r=discovery_replay(); result=discover(r.fetcher(),max_sources=1)
    assert not result['complete'] and len(result['urls'])==1
    assert not any('/p2' in u or '/search/' in u for u,h in r.calls)


def test_discovery_failure_is_not_empty_success():
    r=discovery_replay();r.routes[CATEGORY+'/feed.rss']=resp('<not-rss/>')
    result=discover(r.fetcher())
    assert not result['complete'] and result['urls']


def test_empty_discovery_is_degraded():
    r=discovery_replay(True)
    r.routes[CATEGORY]=resp('<h1>Pokémon TCG Live News &amp; Announcements</h1><link rel="alternate" type="application/rss+xml" href="'+CATEGORY+'/feed.rss">')
    shard=HOST+'/en-us/sitemap-category-tcg-live-news-announcements-1-1000.xml'
    r.routes[shard]=resp('<urlset/>')
    result=discover(r.fetcher())
    assert not result['complete'] and not result['urls']


def test_authority_loss_quarantines_prior_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'ROOT', tmp_path/'observer')
    store=storage.Store(); before=observation()
    store.save([before], {'complete':True}, STAMP)
    after=observation(dom().replace('Role_Administrator','Role_Member'))
    report=store.save([after], {'complete':True}, STAMP)
    assert before['observation_id'] in report['quarantined_observations']
    assert all(c['accepted_exact_timestamp'] is None for c in report['candidates'])
    # A return to old content cannot silently clear the hold.
    report=store.save([before], {'complete':True}, STAMP)
    assert report['candidates'][0]['state']=='QUARANTINED'


def test_banner_changes_do_not_create_evidence_revisions():
    a=observation(); b=observation(dom().replace('<body>','<body><aside>Global banner changed.</aside>'))
    assert a['observation_id']==b['observation_id']


def test_no_store_and_retry_http_date():
    url=FIXTURES[0]['canonical_url']
    r=Replay({HOST+'/robots.txt':resp('User-agent: *\nAllow: /'),
        url:resp('ok',**{'cache-control':'no-store','etag':'x'})})
    f=r.fetcher();f.get(url);f.get(url)
    assert len([u for u,h in r.calls if u==url])==2
    assert all('If-None-Match' not in h for u,h in r.calls)
    r=Replay({HOST+'/robots.txt':resp('User-agent: *\nAllow: /'),
        url:[resp('',503,**{'retry-after':'Thu, 01 Jan 1970 00:00:12 GMT'}),resp('ok')]})
    assert r.fetcher().get(url)[0].body=='ok'
    assert r.clock>=12


def test_missing_versioned_tzdb_fails_closed(monkeypatch):
    import official_release_observer.timeparse as module
    def missing():raise ModuleNotFoundError('tzdata unavailable')
    monkeypatch.setattr(module,'pacific',missing)
    assert module.extract_time('September 15, 2026 - 10:00 AM PT')['calculated_utc'] is None


def test_cli_offline_has_no_save_or_promotion(tmp_path, capsys):
    from official_release_observer.__main__ import main
    source=tmp_path/'capture.html';source.write_text(dom(),encoding='utf-8')
    assert main(['--html',str(source),'--url',FIXTURES[0]['canonical_url']])==0
    report=json.loads(capsys.readouterr().out)
    assert report['candidates'][0]['accepted_exact_timestamp']=='2026-09-15T17:00:00Z'
    assert report['candidates'][0]['canonical_status']=='CANONICAL_PENDING'


def test_editorial_secondary_event_binding():
    url='https://www.pokemon.com/us/pokemon-news/synthetic-live-release'
    markup=f'''<html><head><title>Expansion Live availability</title><link rel="canonical" href="{url}"></head><body><main><article><p>Leap into the latest Pokémon Trading Card Game expansion, Example Set, in Pokémon Trading Card Game Live when it becomes available on Thursday, March 26, 2026, at 10:00 a.m. PDT. You’ll have the opportunity to play with new cards.</p></article></main></body></html>'''
    o=observe(url,markup,STAMP)
    assert accepted(o)=='2026-03-26T17:00:00Z'
    date=markup.replace('Leap into the latest Pokémon Trading Card Game expansion, Example Set, in Pokémon Trading Card Game Live when it becomes available on Thursday, March 26, 2026, at 10:00 a.m. PDT. You’ll have the opportunity to play with new cards.', 'Example Set releases in Pokémon TCG Live on March 26, 2026.')
    o=observe(url,date,STAMP)
    assert o['state']=='DATE_CONFIRMED' and accepted(o) is None
