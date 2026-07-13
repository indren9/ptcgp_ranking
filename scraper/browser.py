
"""Compatibility wrapper for Limitless Selenium browser helpers."""

from __future__ import annotations

from sources.limitless.browser import chrome, close_chrome, make_chrome, polite_sleep, safe_get, wait_css

__all__ = ["make_chrome", "close_chrome", "chrome", "wait_css", "safe_get", "polite_sleep"]
