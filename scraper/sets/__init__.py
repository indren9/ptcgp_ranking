# scraper/sets/__init__.py

from .models import Expansion
from .url import DEFAULT_DECKS_URL, build_decks_url_for_expansion
from .catalog import get_expansions_catalog
from .api import resolve_expansion_and_url_from_config  # ← entry point nuovo

# (compat opzionale) se vuoi mantenere in vita resolve_set per vecchi notebook:
try:
    from .resolve import resolve_set  # legacy (auto/code/choose)
except Exception:
    resolve_set = None  # non usato nel flusso attuale

__all__ = [
    "Expansion",
    "DEFAULT_DECKS_URL",
    "build_decks_url_for_expansion",
    "get_expansions_catalog",
    "resolve_expansion_and_url_from_config",
    "resolve_set",  # rimane ma non serve nel nuovo notebook
]
