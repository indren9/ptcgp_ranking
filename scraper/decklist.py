
"""Compatibility wrapper for Limitless decklist helpers."""

from __future__ import annotations

from sources.limitless.constants import LIMITLESS_BASE_URL, LIMITLESS_DECKS_URL
from sources.limitless.pages.decks import (
    filter_top_meta,
    parse_decklist_table,
    parse_decklist_table_to_df,
    scrape_decklist_html,
)

__all__ = [
    "LIMITLESS_BASE_URL",
    "LIMITLESS_DECKS_URL",
    "scrape_decklist_html",
    "parse_decklist_table",
    "parse_decklist_table_to_df",
    "filter_top_meta",
]

