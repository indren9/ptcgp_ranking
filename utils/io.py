"""Compatibility wrappers for storage and reporting helpers.

The active implementation moved to storage.* and reporting.*. This module keeps
the historical `utils.io` import path alive for notebooks and older modules.
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import logging

import pandas as pd

from reporting.excel import write_excel_versioned_styled
from storage.paths import ProjectPaths as Paths, init_paths
from storage.routing import ROUTES
from storage.writers import (
    run_stamp as _run_stamp,
    save_plot_dual,
    save_plot_timestamped,
    write_csv_versioned,
    write_excel_versioned,
)

log = logging.getLogger("ptcgp") if logging.getLogger("ptcgp").handlers else logging.getLogger(__name__)


def _dest(paths: Paths, prefix: str) -> Path:
    """
    Legacy route resolver for callers that still import utils.io._dest.
    New code should use storage.routing.dest_for_key/dir_for_key.
    """
    if prefix not in ROUTES:
        log.warning("[route] Prefix sconosciuto '%s' - invio a outputs/", prefix)
        return paths.outputs
    dest = paths.outputs
    for part in ROUTES[prefix]:
        dest = dest / part
    return dest


def _df_content_hash(df: pd.DataFrame) -> str:
    """Stable hash for DataFrame content, kept for notebook compatibility."""
    return hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "Paths",
    "ROUTES",
    "init_paths",
    "_dest",
    "_run_stamp",
    "_df_content_hash",
    "write_csv_versioned",
    "save_plot_timestamped",
    "save_plot_dual",
    "write_excel_versioned",
    "write_excel_versioned_styled",
]
