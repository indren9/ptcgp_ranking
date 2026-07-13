
"""Compatibility wrapper for Limitless deck matchup helpers."""

from __future__ import annotations

from sources.limitless.pages.decks import extract_matchups_from_html, scrape_matchups, to_matchup_url

__all__ = ["extract_matchups_from_html", "scrape_matchups", "to_matchup_url"]
