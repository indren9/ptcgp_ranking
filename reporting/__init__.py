"""Reporting helpers for Excel files, display tables, and logs."""

from .excel import write_excel_versioned_styled
from .logs import configure_logging, get_logger
from .plots import show_wr_heatmap
from .tables import (
    diagnostics_preview_frame,
    frame_inventory_frame,
    output_paths_frame,
    ranking_preview_frame,
    saved_outputs_frame,
    show_ranking,
    style_ranking_preview,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "diagnostics_preview_frame",
    "frame_inventory_frame",
    "output_paths_frame",
    "ranking_preview_frame",
    "saved_outputs_frame",
    "show_ranking",
    "show_wr_heatmap",
    "style_ranking_preview",
    "write_excel_versioned_styled",
]
