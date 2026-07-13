"""Compatibility wrappers for expansion-aware storage routing.

The canonical implementation lives in storage.routing. This module keeps the
historical `utils.expansion_routing` import path alive for notebooks and older
modules.
"""

from __future__ import annotations

from storage.routing import (
    ExpansionRef,
    base_for_expansion,
    dest_for_key,
    dir_for_key,
    expansions_root,
    find_latest,
    resolve_auto_from_outputs,
    write_csv_versioned_setaware,
)

__all__ = [
    "ExpansionRef",
    "expansions_root",
    "base_for_expansion",
    "resolve_auto_from_outputs",
    "dest_for_key",
    "dir_for_key",
    "find_latest",
    "write_csv_versioned_setaware",
]
