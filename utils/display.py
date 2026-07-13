"""Compatibility wrappers for notebook display helpers.

The canonical implementation lives in reporting.tables and reporting.plots.
"""

from __future__ import annotations

from reporting.plots import show_wr_heatmap
from reporting.tables import show_ranking

__all__ = ["show_ranking", "show_wr_heatmap"]
