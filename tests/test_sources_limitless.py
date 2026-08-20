from domain.expansions import Expansion as DomainExpansion
from scraper.sets.models import Expansion as ScraperExpansion
from sources.limitless import LIMITLESS_BASE_URL
from sources.limitless.client import make_session
from scraper.decklist import scrape_decklist_html as legacy_scrape_decklist_html
from scraper.decklist_expansion import scrape_decklist_for_expansion as legacy_scrape_decklist_for_expansion
from scraper.matchups import scrape_matchups as legacy_scrape_matchups
from sources.limitless.pages.decks import (
    _decklist_cache_file,
    extract_matchups_from_html,
    filter_top_meta,
    parse_decklist_table,
    scrape_decklist_for_expansion,
    scrape_decklist_html,
    scrape_matchups,
    to_matchup_url,
)
from scraper.sets.html_parse import parse_expansions_from_html as legacy_parse_expansions_from_html
from scraper.sets.url import build_decks_url_for_expansion as legacy_build_decks_url_for_expansion
from scraper.sets.cache_ttl import load_cached_expansions as legacy_load_cached_expansions
from scraper.sets.catalog import get_expansions_catalog as legacy_get_expansions_catalog
from scraper.sets.api import resolve_expansion_and_url_from_config as legacy_resolve_expansion_and_url_from_config
from scraper.sets.selenium_scan import read_current_expansion_from_selenium as legacy_read_current_expansion_from_selenium
from sources.limitless.pages.sets import (
    DEFAULT_DECKS_URL,
    Expansion,
    FormatOption,
    FormatSetsCatalogEntry,
    build_decks_url_for_expansion,
    expansions_cache_path,
    format_sets_cache_path,
    fetch_catalog_with_policy,
    format_uses_rotation,
    formats_cache_path,
    resolve_format_code,
    get_expansions_catalog,
    get_formats_catalog,
    load_cached_expansions,
    load_cached_format_sets,
    load_cached_formats,
    parse_expansions_from_html,
    parse_formats_from_html,
    read_current_expansion_from_selenium,
    resolve_expansion_and_url_from_config,
    save_cached_expansions,
    save_cached_format_sets,
    save_cached_formats,
    source_game_code,
)


def test_limitless_sources_reexport_current_adapters():
    assert callable(make_session)
    assert Expansion is ScraperExpansion
    assert DomainExpansion is ScraperExpansion
    assert legacy_resolve_expansion_and_url_from_config is resolve_expansion_and_url_from_config
    assert legacy_read_current_expansion_from_selenium is read_current_expansion_from_selenium
    assert LIMITLESS_BASE_URL == "https://play.limitlesstcg.com"
    assert "game=POCKET" in DEFAULT_DECKS_URL


def test_limitless_sets_builds_decks_url_with_expansion():
    url = build_decks_url_for_expansion(Expansion(code="B3a", name="Paradox Drive"))

    assert "game=POCKET" in url
    assert "format=standard" in url
    assert "set=B3a" in url
    assert legacy_build_decks_url_for_expansion is build_decks_url_for_expansion


def test_limitless_sets_builds_decks_url_with_manual_format_code():
    cfg = {"source": {"format": {"mode": "code", "code": "expanded"}}}

    url = build_decks_url_for_expansion(
        Expansion(code="B3a", name="Paradox Drive"),
        "https://play.limitlesstcg.com/decks?game=POCKET&format=standard",
        cfg=cfg,
    )

    assert "format=expanded" in url
    assert resolve_format_code(cfg, "https://example.com/decks?format=standard") == "expanded"


def test_limitless_sets_builds_decks_url_with_tcg_game_and_rotation():
    cfg = {"source": {"game": "PTCG", "format": {"mode": "code", "code": "standard"}}}

    url = build_decks_url_for_expansion(
        Expansion(code="CRI", name="Celestial Guardians"),
        "https://play.limitlesstcg.com/decks?game=PTCG&set=OLD&format=expanded&rotation=2026",
        cfg=cfg,
    )

    assert "game=PTCG" in url
    assert "format=standard" in url
    assert "rotation=2026" in url
    assert "set=CRI" in url
    assert "game=POCKET" not in url
    assert source_game_code(cfg, url) == "PTCG"


def test_limitless_sets_builds_decks_url_with_expansion_rotation_override():
    cfg = {"source": {"game": "PTCG", "format": {"mode": "code", "code": "standard"}}}

    url = build_decks_url_for_expansion(
        Expansion(code="ASC", name="Ascended Heroes", rotation="2025"),
        "https://play.limitlesstcg.com/decks?game=PTCG&format=standard&rotation=2026",
        cfg=cfg,
    )

    assert "set=ASC" in url
    assert "rotation=2025" in url


def test_limitless_sets_builds_expanded_url_without_rotation():
    cfg = {"source": {"game": "PTCG", "format": {"mode": "code", "code": "expanded"}}}

    url = build_decks_url_for_expansion(
        Expansion(code="MEG", name="Mega Evolution", rotation="2025"),
        "https://play.limitlesstcg.com/decks?game=PTCG&format=standard&rotation=2026",
        cfg=cfg,
    )

    assert "game=PTCG" in url
    assert "format=expanded" in url
    assert "set=MEG" in url
    assert "rotation=" not in url
    assert format_uses_rotation("standard") is True
    assert format_uses_rotation("expanded") is False


def test_limitless_sets_auto_format_keeps_url_format():
    cfg = {"source": {"format": {"mode": "auto", "code": ""}}}

    assert resolve_format_code(cfg, "https://example.com/decks?game=POCKET&format=custom") == "custom"


def test_limitless_sets_parses_formats_from_select_and_cache(tmp_path):
    html = """
    <select id="format">
      <option value="standard" selected>Standard</option>
      <option value="expanded">Expanded</option>
    </select>
    """

    formats = parse_formats_from_html(html)

    assert formats == [
        FormatOption(code="standard", name="Standard", is_current=True),
        FormatOption(code="expanded", name="Expanded", is_current=False),
    ]

    cache_path = tmp_path / "formats.json"
    save_cached_formats(cache_path, formats)
    loaded, fetched_at = load_cached_formats(cache_path)

    assert loaded == formats
    assert fetched_at is not None


def test_limitless_sets_formats_cache_path_is_scoped_by_game(tmp_path):
    paths = type("Paths", (), {"cache": tmp_path / "cache"})()

    assert formats_cache_path(paths, {"source": {"game": "PTCG"}}).name == "formats_ptcg.json"
    assert format_sets_cache_path(paths, {"source": {"game": "PTCG"}}).name == "format_sets_ptcg.json"


def test_limitless_sets_format_sets_cache_roundtrip(tmp_path):
    entries = [
        FormatSetsCatalogEntry(
            code="standard",
            name="Standard",
            is_current=True,
            expansions=[Expansion(code="CRI", name="Chaos Rising", rotation="2026")],
        ),
        FormatSetsCatalogEntry(code="2016", name="Worlds 2016 (XY-STS)", expansions=[]),
    ]
    cache_path = tmp_path / "format_sets.json"

    save_cached_format_sets(cache_path, entries)
    loaded, fetched_at = load_cached_format_sets(cache_path)

    assert loaded == entries
    assert fetched_at is not None


def test_limitless_sets_formats_catalog_uses_fresh_cache(tmp_path):
    cache_path = tmp_path / "formats.json"
    save_cached_formats(cache_path, [FormatOption(code="standard", name="Standard")])

    formats = get_formats_catalog(session=None, decks_url=DEFAULT_DECKS_URL, cache_path=cache_path)

    assert formats == [FormatOption(code="standard", name="Standard")]


def test_limitless_sets_fetch_catalog_policy_normalizes_format_url(monkeypatch, tmp_path):
    paths = type("Paths", (), {"cache": tmp_path / "cache"})()
    seen = {}

    def fake_get_expansions_catalog(**kwargs):
        seen["decks_url"] = kwargs["decks_url"]
        return []

    monkeypatch.setattr("sources.limitless.pages.sets.get_expansions_catalog", fake_get_expansions_catalog)

    fetch_catalog_with_policy(
        {"source": {"game": "PTCG", "format": {"mode": "code", "code": "expanded"}}},
        paths,
        session=object(),
        decks_url="https://play.limitlesstcg.com/decks?game=PTCG&format=standard&rotation=2026",
    )

    assert "format=expanded" in seen["decks_url"]
    assert "rotation=" not in seen["decks_url"]


def test_limitless_sets_parses_expansions_from_select_and_legacy_wrapper():
    html = """
    <select>
      <option value="A1">Genetic Apex</option>
      <option value="B3a" selected>Paradox Drive</option>
    </select>
    """

    expansions = parse_expansions_from_html(html)

    assert legacy_parse_expansions_from_html is parse_expansions_from_html
    assert [exp.code for exp in expansions] == ["A1", "B3a"]
    assert expansions[1].is_current is True


def test_limitless_sets_strips_code_prefix_from_new_limitless_names():
    html = """
    <select>
      <option value="B3b" selected>B3b - Everyday Wonders</option>
    </select>
    """

    expansions = parse_expansions_from_html(html)

    assert expansions == [Expansion(code="B3b", name="Everyday Wonders", is_current=True)]


def test_limitless_sets_accepts_tcg_letter_set_codes_from_select():
    html = """
    <select id="set">
      <optgroup label="2026 (TEF-on)">
        <option data-set="CRI" data-rotation="2026" selected>Chaos Rising</option>
        <option data-set="POR" data-rotation="2026">Perfect Order</option>
      </optgroup>
    </select>
    """

    expansions = parse_expansions_from_html(html)

    assert expansions == [
        Expansion(code="CRI", name="Chaos Rising", is_current=True, rotation="2026"),
        Expansion(code="POR", name="Perfect Order", is_current=False, rotation="2026"),
    ]


def test_limitless_sets_preserves_tcg_rotation_from_select():
    html = """
    <select id="set">
      <optgroup label="2025 (SVI-on)">
        <option data-set="ASC" data-rotation="2025">Ascended Heroes</option>
      </optgroup>
    </select>
    """

    expansions = parse_expansions_from_html(html)

    assert expansions == [Expansion(code="ASC", name="Ascended Heroes", is_current=False, rotation="2025")]


def test_limitless_sets_ignores_game_and_format_selects_before_tcg_set_select():
    html = """
    <select id="game">
      <option value="POCKET">Pokémon TCG Pocket</option>
      <option value="PTCG" selected>Pokémon TCG</option>
    </select>
    <select id="format">
      <option value="standard" selected>Standard</option>
    </select>
    <select id="set">
      <optgroup label="2026 (TEF-on)">
        <option data-set="CRI" data-rotation="2026" selected>Chaos Rising</option>
        <option data-set="POR" data-rotation="2026">Perfect Order</option>
      </optgroup>
    </select>
    """

    expansions = parse_expansions_from_html(html)

    assert expansions == [
        Expansion(code="CRI", name="Chaos Rising", is_current=True, rotation="2026"),
        Expansion(code="POR", name="Perfect Order", is_current=False, rotation="2026"),
    ]


def test_limitless_sets_parses_expansions_from_links():
    html = """
    <a class="active" href="/decks?game=POCKET&set=B3a">Paradox Drive</a>
    <a href="/decks?game=POCKET&set=A1">Genetic Apex</a>
    """

    expansions = parse_expansions_from_html(html)

    assert [exp.code for exp in expansions] == ["B3a", "A1"]
    assert expansions[0].is_current is True


def test_limitless_sets_parses_tcg_letter_set_codes_from_links():
    html = """
    <a class="active" href="/decks?game=PTCG&format=standard&rotation=2026&set=CRI">Chaos Rising</a>
    <a href="/decks?game=PTCG&format=standard&rotation=2026&set=PRE">Perfect Order</a>
    """

    expansions = parse_expansions_from_html(html)

    assert [(exp.code, exp.name, exp.is_current, exp.rotation) for exp in expansions] == [
        ("CRI", "Chaos Rising", True, "2026"),
        ("PRE", "Perfect Order", False, "2026"),
    ]


def test_limitless_sets_does_not_parse_deck_links_as_expansions():
    html = """
    <table>
      <tr>
        <td><a href="/decks/dragapult?format=standard&rotation=2026&set=CRI">Dragapult</a></td>
      </tr>
      <tr>
        <td><a href="/decks/n-zoroark?format=standard&rotation=2026&set=CRI">N's Zoroark</a></td>
      </tr>
    </table>
    """

    assert parse_expansions_from_html(html) == []


def test_limitless_sets_cache_helpers_are_native_and_legacy_compatible(tmp_path):
    cache_path = tmp_path / "expansions.json"
    save_cached_expansions(
        cache_path,
        [
            Expansion(code="B3a", name="Paradox Drive", is_current=False),
            Expansion(code="A1", name="Genetic Apex", is_current=True),
        ],
    )

    expansions, fetched_at, burst_until = load_cached_expansions(cache_path)

    assert legacy_load_cached_expansions is load_cached_expansions
    assert fetched_at is not None
    assert burst_until is None
    assert [(exp.code, exp.is_current) for exp in expansions] == [("B3a", True), ("A1", False)]


def test_limitless_sets_expansions_cache_path_is_scoped_by_game_and_format(tmp_path):
    paths = type("Paths", (), {"cache": tmp_path / "cache"})()

    pocket = expansions_cache_path(paths, {"source": {"game": "POCKET"}})
    tcg_standard = expansions_cache_path(paths, {"source": {"game": "PTCG", "format": {"mode": "code", "code": "standard"}}})
    tcg_expanded = expansions_cache_path(paths, {"source": {"game": "PTCG", "format": {"mode": "code", "code": "expanded"}}})

    assert pocket.name == "expansions_pocket_standard.json"
    assert tcg_standard.name == "expansions_ptcg_standard.json"
    assert tcg_expanded.name == "expansions_ptcg_expanded.json"
    assert pocket.parent == tmp_path / "cache"
    assert tcg_standard.parent == tmp_path / "cache"
    assert pocket != tcg_standard
    assert tcg_standard != tcg_expanded


def test_limitless_sets_catalog_uses_fresh_cache_without_session(tmp_path):
    cache_path = tmp_path / "expansions.json"
    save_cached_expansions(cache_path, [Expansion(code="B3a", name="Paradox Drive")])

    expansions = get_expansions_catalog(session=None, decks_url=DEFAULT_DECKS_URL, cache_path=cache_path)

    assert legacy_get_expansions_catalog is get_expansions_catalog
    assert [exp.code for exp in expansions] == ["B3a"]


def test_limitless_sets_expanded_catalog_strips_cached_rotation(tmp_path):
    cache_path = tmp_path / "expansions.json"
    save_cached_expansions(cache_path, [Expansion(code="MEG", name="Mega Evolution", rotation="2025")])

    expansions = get_expansions_catalog(
        session=None,
        decks_url="https://play.limitlesstcg.com/decks?game=PTCG&format=expanded",
        cache_path=cache_path,
        cfg={"source": {"game": "PTCG", "format": {"mode": "code", "code": "expanded"}}},
    )

    assert expansions == [Expansion(code="MEG", name="Mega Evolution", is_current=True, rotation=None)]


def test_limitless_sets_reads_current_expansion_from_selenium(monkeypatch):
    import sources.limitless.pages.sets as sets_page

    class FakeOption:
        text = "B3a - Paradox Drive"

        def get_attribute(self, name):
            return "B3a" if name == "value" else None

    class FakeSelect:
        def __init__(self, element):
            self.first_selected_option = FakeOption()

    class FakeWait:
        def __init__(self, browser, wait_seconds):
            self.browser = browser

        def until(self, condition):
            return object()

    class FakeEC:
        @staticmethod
        def presence_of_element_located(locator):
            return locator

    class FakeBrowser:
        current_url = "https://play.limitlesstcg.com/decks?game=POCKET&format=standard"

        def get(self, url):
            self.current_url = url

    monkeypatch.setattr(sets_page, "_selenium_deps", lambda: (type("By", (), {"ID": "id"}), FakeEC, FakeSelect, FakeWait))

    exp = read_current_expansion_from_selenium(FakeBrowser(), decks_url=DEFAULT_DECKS_URL)

    assert exp == Expansion(code="B3a", name="B3a - Paradox Drive", is_current=True)


def test_limitless_sets_reads_current_tcg_expansion_from_selenium_data_set(monkeypatch):
    import sources.limitless.pages.sets as sets_page

    class FakeOption:
        text = "Chaos Rising"

        def get_attribute(self, name):
            return "CRI" if name == "data-set" else None

    class FakeSelect:
        def __init__(self, element):
            self.first_selected_option = FakeOption()

    class FakeWait:
        def __init__(self, browser, wait_seconds):
            self.browser = browser

        def until(self, condition):
            return object()

    class FakeEC:
        @staticmethod
        def presence_of_element_located(locator):
            return locator

    class FakeBrowser:
        current_url = "https://play.limitlesstcg.com/decks?game=PTCG&format=standard"

        def get(self, url):
            self.current_url = url

    monkeypatch.setattr(sets_page, "_selenium_deps", lambda: (type("By", (), {"ID": "id"}), FakeEC, FakeSelect, FakeWait))

    exp = read_current_expansion_from_selenium(FakeBrowser(), decks_url="https://play.limitlesstcg.com/decks?game=PTCG")

    assert exp == Expansion(code="CRI", name="Chaos Rising", is_current=True)


def test_limitless_decks_parser_available_from_new_namespace():
    html = """
    <table>
      <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
      <tbody><tr><td>1</td><td><a href="/deck/1">Pikachu</a></td><td>12%</td><td>3</td></tr></tbody>
    </table>
    """

    df = parse_decklist_table(html)

    assert df.loc[1, "Deck"] == "Pikachu"
    assert df.loc[1, "Share"] == "12%"
    assert df.loc[1, "Count"] == 3
    assert to_matchup_url("/deck/1").endswith("/deck/1/matchups")


def test_limitless_tcg_decklist_parser_matches_contract():
    html = """
    <table>
      <thead><tr><th>#</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
      <tbody>
        <tr>
          <td>1</td>
          <td><a href="/decks/dragapult-ex?format=standard&rotation=2026&set=CRI">Dragapult</a></td>
          <td>7.89%</td>
          <td>2299</td>
        </tr>
      </tbody>
    </table>
    """

    df = parse_decklist_table(html)

    assert list(df.columns) == ["Deck", "Share", "Count", "URL"]
    assert df.loc[1, "Deck"] == "Dragapult"
    assert df.loc[1, "Share"] == "7.89%"
    assert df.loc[1, "Count"] == 2299
    assert "rotation=2026" in df.loc[1, "URL"]
    assert to_matchup_url(df.loc[1, "URL"]).endswith("/matchups?format=standard&rotation=2026&set=CRI")


def test_limitless_tcg_matchup_parser_matches_contract():
    html = """
    <table>
      <thead><tr><th>Deck</th><th>Record</th><th>Winrate</th><th>Matches</th></tr></thead>
      <tbody>
        <tr><td><a href="/decks/slowking-scr">Slowking</a></td><td>297 - 328 - 10</td><td>46.77%</td><td>635</td></tr>
      </tbody>
    </table>
    """

    rows = extract_matchups_from_html(html, "Dragapult")

    assert rows == [
        {
            "Deck A": "Dragapult",
            "Deck B": "Slowking",
            "W": 297,
            "L": 328,
            "T": 10,
            "N": 635,
            "Winrate": 46.77,
        }
    ]


def test_limitless_decks_scrape_html_uses_fresh_cache(tmp_path):
    url = "https://play.limitlesstcg.com/decks?game=POCKET"
    cache_path = _decklist_cache_file(tmp_path, url)
    cache_path.write_text("<html><table></table></html>", encoding="utf-8")

    html, from_cache = scrape_decklist_html(
        url,
        cache_dir=tmp_path,
        ttl_minutes=720,
        force_refresh=False,
        headless=True,
    )

    assert legacy_scrape_decklist_html is scrape_decklist_html
    assert from_cache is True
    assert html == "<html><table></table></html>"


def test_limitless_decks_scrapes_decklist_for_expansion_from_cache(tmp_path):
    exp = Expansion(code="B3a", name="Paradox Drive")
    url = build_decks_url_for_expansion(exp)
    cache_path = _decklist_cache_file(tmp_path, url)
    cache_path.write_text(
        """
        <table>
          <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
          <tbody><tr><td>1</td><td>Pikachu</td><td>12%</td><td>3</td></tr></tbody>
        </table>
        """,
        encoding="utf-8",
    )

    df, html, cache_hit = scrape_decklist_for_expansion(
        None,
        tmp_path,
        exp,
        ttl_minutes=720,
        force_refresh=False,
    )

    assert legacy_scrape_decklist_for_expansion is scrape_decklist_for_expansion
    assert cache_hit is True
    assert "Pikachu" in html
    assert df.loc[1, "Deck"] == "Pikachu"


def test_limitless_decks_filter_top_meta_from_new_namespace():
    html = """
    <table>
      <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
      <tbody>
        <tr><td>1</td><td>Pikachu</td><td>50%</td><td>10</td></tr>
        <tr><td>2</td><td>Mewtwo</td><td>30%</td><td>6</td></tr>
        <tr><td>3</td><td>Charizard</td><td>20%</td><td>4</td></tr>
      </tbody>
    </table>
    """

    top = filter_top_meta(parse_decklist_table(html), threshold_pct=75)

    assert top["Deck"].tolist() == ["Pikachu", "Mewtwo"]
    assert top["share_cum"].tolist() == [50.0, 80.0]


def test_limitless_decks_filter_top_meta_none_keeps_all_decks():
    html = """
    <table>
      <thead><tr><th>Rank</th><th>Deck</th><th>Share</th><th>Count</th></tr></thead>
      <tbody>
        <tr><td>1</td><td>Pikachu</td><td>50%</td><td>10</td></tr>
        <tr><td>2</td><td>Mewtwo</td><td>30%</td><td>6</td></tr>
        <tr><td>3</td><td>Charizard</td><td>20%</td><td>4</td></tr>
      </tbody>
    </table>
    """

    top = filter_top_meta(parse_decklist_table(html), threshold_pct=None)

    assert top["Deck"].tolist() == ["Pikachu", "Mewtwo", "Charizard"]


def test_limitless_decks_extracts_matchups_from_new_namespace():
    html = """
    <table>
      <thead><tr><th>Deck</th><th>Matches</th><th>Score</th><th>Win %</th></tr></thead>
      <tbody>
        <tr><td><a>Mewtwo</a></td><td>12</td><td>7-5</td><td>58.3%</td></tr>
        <tr><td>Charizard</td><td>3</td><td>1-1-1</td><td></td></tr>
      </tbody>
    </table>
    """

    rows = extract_matchups_from_html(html, "Pikachu")

    assert rows[0] == {"Deck A": "Pikachu", "Deck B": "Mewtwo", "W": 7, "L": 5, "T": 0, "N": 12, "Winrate": 58.3}
    assert rows[1]["Winrate"] == 33.33


def test_limitless_decks_scrape_matchups_from_new_namespace(tmp_path):
    class DummyResponse:
        text = """
        <table>
          <thead><tr><th>Deck</th><th>Matches</th><th>Score</th><th>Win %</th></tr></thead>
          <tbody><tr><td>Mewtwo</td><td>2</td><td>1-1</td><td>50%</td></tr></tbody>
        </table>
        """

        def raise_for_status(self):
            return None

    class DummySession:
        request_timeout = 1

        def __init__(self):
            self.calls = 0

        def get(self, url, timeout=1):
            self.calls += 1
            return DummyResponse()

    session = DummySession()
    url = "https://play.limitlesstcg.com/deck/1/matchups"

    df, total, cache_hits, diagnostics = scrape_matchups(
        [("Pikachu", url), ("Duplicate", url)],
        session=session,
        cache_dir=tmp_path,
        ttl_minutes=720,
        force_refresh=True,
        rate_limit_seconds=0,
        collect_diagnostics=True,
    )

    assert legacy_scrape_matchups is scrape_matchups
    assert session.calls == 1
    assert total == 1
    assert cache_hits == 0
    assert df.loc[0, "Deck A"] == "Pikachu"
    assert df.loc[0, "N"] == 2
    assert diagnostics["requested_pages"] == 2
    assert diagnostics["unique_pages"] == 1
    assert diagnostics["duplicate_pages"] == 1
    assert diagnostics["cache_misses"] == 1
    assert diagnostics["rows"] == 1
    assert diagnostics["delay_seconds_total"] == 0.0
