import scraper.browser
from sources.limitless import browser


def test_limitless_browser_legacy_wrapper_exports_same_functions():
    assert scraper.browser.chrome is browser.chrome
    assert scraper.browser.make_chrome is browser.make_chrome
    assert scraper.browser.safe_get is browser.safe_get


def test_limitless_safe_get_without_wait_calls_driver_get():
    class Driver:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)

    driver = Driver()

    browser.safe_get(driver, "https://example.com")

    assert driver.urls == ["https://example.com"]
