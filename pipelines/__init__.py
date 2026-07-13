"""Pipeline entry points."""

from pipelines.deck_ranking import DeckRankingResult, run_deck_ranking

__all__ = ["DeckRankingResult", "run_deck_ranking"]
