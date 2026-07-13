from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import logging
import re

from domain.expansions import strip_expansion_code_prefix

log = logging.getLogger("ptcgp")


ROUTES: dict[str, tuple[str, ...]] = {
    "decklist_raw": ("decklists", "raw"),
    "top_meta_decklist": ("decklists", "top_meta"),
    "matchup_raw": ("matchups", "raw"),
    "score_flat": ("matchups", "scores"),
    "wr_matrix": ("matrices", "winrate"),
    "n_dir_matrix": ("matrices", "match_counts"),
    "top_meta_post_alias": ("decklists", "top_meta"),
    "heatmap_topN": ("matrices", "heatmaps"),
    "nan_diagnostics_pre_filter": ("diagnostics", "nan_filter"),
    "nan_filter_simulation": ("diagnostics", "nan_filter"),
    "wildcard_candidates": ("diagnostics", "wildcards"),
    "mars_ranking": ("rankings", "mars"),
    "report": ("reports", "mars"),
}


LEGACY_ROUTES: dict[str, tuple[str, ...]] = {
    "decklist_raw": ("Decklists", "raw"),
    "top_meta_decklist": ("Decklists", "top_meta"),
    "matchup_raw": ("MatchupData", "raw"),
    "score_flat": ("MatchupData", "flat"),
    "wr_matrix": ("Matrices", "winrate"),
    "n_dir_matrix": ("Matrices", "volumes"),
    "top_meta_post_alias": ("Decklists", "top_meta"),
    "heatmap_topN": ("Matrices", "heatmap"),
    "nan_diagnostics_pre_filter": ("Diagnostics", "NaN"),
    "nan_filter_simulation": ("Diagnostics", "NaN"),
    "wildcard_candidates": ("Diagnostics", "NaN"),
    "mars_ranking": ("RankingData", "MARS_Ranking"),
    "report": ("RankingData", "MARS_Report"),
}


LEGACY_KEY_ALIASES: dict[str, str] = {
    "matchup_score_table": "score_flat",
    "filtered_wr": "wr_matrix",
    "n_dir": "n_dir_matrix",
}


PREFIX_BY_KEY: dict[str, str] = {
    "decklist_raw": "decklist_raw",
    "top_meta_decklist": "top_meta_decklist",
    "matchup_raw": "matchup_raw",
    "score_flat": "matchup_scores",
    "matchup_score_table": "matchup_scores",
    "wr_matrix": "winrate_matrix",
    "filtered_wr": "winrate_matrix",
    "n_dir_matrix": "match_count_matrix",
    "n_dir": "match_count_matrix",
    "mars_ranking": "mars_ranking",
    "nan_diagnostics_pre_filter": "nan_diagnostics_pre_filter",
    "nan_filter_simulation": "nan_filter_simulation",
    "wildcard_candidates": "wildcard_candidates",
}


LEGACY_PREFIX_BY_KEY: dict[str, tuple[str, ...]] = {
    "score_flat": ("score",),
    "matchup_score_table": ("score",),
    "wr_matrix": ("filtered_wr",),
    "filtered_wr": ("filtered_wr",),
    "n_dir_matrix": ("n_dir",),
    "n_dir": ("n_dir",),
}


@dataclass(frozen=True)
class ExpansionRef:
    code: Optional[str]
    name: Optional[str] = None


def _sanitize_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    s = name.strip()
    s = re.sub(r"[\/\\]+", "-", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_\-\.]+", "", s)
    return s[:64] or None


def _route_key(key: str) -> str:
    return LEGACY_KEY_ALIASES.get(key, key)


def _should_prefix_set_code(cfg: dict) -> bool:
    saving = (cfg.get("saving", {}) or {})
    if "filename_prefix_with_set" in saving:
        return bool(saving.get("filename_prefix_with_set"))
    return bool(saving.get("prefix_set_code", False))


def expansions_root(outputs_dir: str | Path) -> Path:
    root = Path(outputs_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def base_for_expansion(outputs_dir: str | Path, exp: Optional[ExpansionRef]) -> Path:
    """
    Resolve the expansion base path without creating expansion-specific folders.
    """
    root = expansions_root(outputs_dir)
    if exp is None or getattr(exp, "code", None) is None:
        return root.resolve()

    code = str(exp.code).strip()
    name = _sanitize_name(strip_expansion_code_prefix(code, getattr(exp, "name", None)))

    if not name:
        try:
            candidates = [d for d in root.iterdir() if d.is_dir() and d.name.startswith(f"{code}__")]
            if candidates:
                pick = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))
                return pick.resolve()
        except Exception:
            pass
        return (root / f"{code}__{code}").resolve()

    return (root / f"{code}__{name}").resolve()


def resolve_auto_from_outputs(outputs_dir: str | Path) -> ExpansionRef:
    """
    Pick the most recently modified expansion output folder.
    Expected folder name: <CODE>__<SANITIZED_NAME>.
    """
    base = Path(outputs_dir)
    try:
        direct = [d for d in base.iterdir() if d.is_dir() and "__" in d.name]
        nested = [d for d in base.rglob("*__*") if d.is_dir()] if base.exists() else []
        seen: dict[Path, Path] = {d.resolve(): d for d in [*direct, *nested]}
        candidates = list(seen.values())
    except FileNotFoundError:
        candidates = []
    if not candidates:
        return ExpansionRef(code=None, name=None)
    pick = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))
    code, name = pick.name.split("__", 1)
    return ExpansionRef(code=code, name=name)


def route_for_key(key: str) -> tuple[str, ...] | None:
    return ROUTES.get(_route_key(key))


def _legacy_route_for_key(key: str) -> tuple[str, ...] | None:
    return LEGACY_ROUTES.get(_route_key(key))


def dest_for_key(paths, key: str, exp: Optional[ExpansionRef]) -> Path:
    """
    Return and create the output directory for a logical output key.
    """
    base = base_for_expansion(paths.outputs, exp)
    route = route_for_key(key)
    if route is None:
        log.warning("[route] Prefix sconosciuto '%s' - invio a outputs/", key)
        dest = base / key
    else:
        dest = base / Path(*route)
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def dir_for_key(paths, key: str, exp: Optional[ExpansionRef]) -> Path:
    """
    Return the output directory for a logical output key without creating it.
    """
    base = base_for_expansion(paths.outputs, exp)
    route = route_for_key(key)
    if route is None:
        return (base / key).resolve()
    return (base / Path(*route)).resolve()


def _prefix_for_key(key: str) -> str:
    return PREFIX_BY_KEY.get(key, key)


def find_latest(paths, key: str, exp: Optional[ExpansionRef], cfg: dict) -> Optional[Path]:
    """
    Find the latest contract CSV for the logical key in the expansion folder.
    """
    route_key = _route_key(key)
    dest_dirs = [dir_for_key(paths, route_key, exp)]
    legacy_route = _legacy_route_for_key(route_key)
    if legacy_route is not None:
        legacy_dir = (base_for_expansion(paths.outputs, exp) / Path(*legacy_route)).resolve()
        if legacy_dir not in dest_dirs:
            dest_dirs.append(legacy_dir)

    bases = [_prefix_for_key(route_key), *LEGACY_PREFIX_BY_KEY.get(route_key, ())]
    code = getattr(exp, "code", None)
    add_code_prefix = _should_prefix_set_code(cfg)

    candidates: list[Path] = []
    for dest_dir in dest_dirs:
        for base in bases:
            if code and add_code_prefix:
                candidates.append(dest_dir / f"{code}_{base}_latest.csv")
            candidates.append(dest_dir / f"{base}_latest.csv")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def write_csv_versioned_setaware(
    df,
    paths,
    key: str,
    exp: Optional[ExpansionRef],
    cfg: dict,
    *,
    changed: bool,
    index: bool = False,
):
    """
    Write a CSV through the expansion-aware storage routing.
    """
    from storage.writers import write_csv_versioned

    dest_dir = dest_for_key(paths, key, exp)
    base_prefix = _prefix_for_key(key)
    code = getattr(exp, "code", None)
    add_code_prefix = _should_prefix_set_code(cfg)
    prefix = f"{code}_{base_prefix}" if (add_code_prefix and code) else base_prefix
    return write_csv_versioned(df, dest_dir, prefix, changed=changed, index=index)


__all__ = [
    "ROUTES",
    "ExpansionRef",
    "expansions_root",
    "base_for_expansion",
    "resolve_auto_from_outputs",
    "route_for_key",
    "dest_for_key",
    "dir_for_key",
    "find_latest",
    "write_csv_versioned_setaware",
]
