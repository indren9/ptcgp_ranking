"""Compatibility wrapper for Limitless expansion catalog fetching."""

from __future__ import annotations

from sources.limitless.pages.sets import fetch_expansions_http, get_expansions_catalog

__all__ = ["fetch_expansions_http", "get_expansions_catalog"]
