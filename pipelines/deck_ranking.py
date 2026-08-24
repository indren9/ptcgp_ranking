from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import json
import logging
import os

import pandas as pd
import yaml

from acquisition.production_bridge import bridge_tournament_api_frames, identity_mapping_diagnostics
from core.consolidate import apply_alias_and_aggregate, build_score_table_filtered, maxN_flat
from core.matrices import build_matrices, n_dir_from_WL, topmeta_post_alias
from core.nan_diagnostics import build_nan_diagnostics
from core.nan_filter import choose_dynamic_nan_filter, filter_wr_nan_iterative
from core.normalize import build_alias_index, load_alias_map
from mars.config import MARSConfig
from mars.meta import blend_meta
from mars.pipeline import run_mars as run_mars_core
from mars.report import write_mars_matchup_report
from reporting.logs import configure_logging
from reporting.plots import show_wr_heatmap
from pipelines.limitless_api_acquisition import run_limitless_api_acquisition
from sources.limitless.client import make_session
from sources.limitless.constants import LIMITLESS_DECKS_URL
from sources.limitless.pages.decks import (
    filter_top_meta,
    parse_decklist_table,
    scrape_decklist_html,
    scrape_matchups,
    to_matchup_url,
)
from domain.expansions import Expansion, SET_CODE_RE
from sources.limitless.pages.sets import (
    build_decks_url_for_expansion,
    parse_expansions_from_html,
    resolve_expansion_and_url_from_config,
)
from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot,
    resolve_release,
)
from storage.paths import ProjectPaths, init_paths
from storage.routing import dest_for_key, find_latest, write_csv_versioned_setaware
from storage.writers import save_plot_dual
from config.loader import read_config

log = logging.getLogger("ptcgp")

DEV_FAST_SCRAPE_ENV = "PTCGP_I_KNOW_FAST_SCRAPE_IS_FOR_DEV_ONLY"
OUTPUT_PROFILE_USER = "user"
OUTPUT_PROFILE_REPRODUCIBLE = "reproducible"
OUTPUT_PROFILE_DEBUG = "debug"
OUTPUT_PROFILES = {OUTPUT_PROFILE_USER, OUTPUT_PROFILE_REPRODUCIBLE, OUTPUT_PROFILE_DEBUG}
ACQUISITION_LEGACY_HTML = "legacy_html"
ACQUISITION_TOURNAMENT_API = "tournament_api"
ACQUISITION_SOURCES = {ACQUISITION_LEGACY_HTML, ACQUISITION_TOURNAMENT_API}

ARTIFACT_TIERS: dict[str, str] = {
    "decklist_raw": OUTPUT_PROFILE_REPRODUCIBLE,
    "top_meta_decklist": OUTPUT_PROFILE_REPRODUCIBLE,
    "matchup_raw": OUTPUT_PROFILE_REPRODUCIBLE,
    "score_flat": OUTPUT_PROFILE_DEBUG,
    "wr_matrix": OUTPUT_PROFILE_DEBUG,
    "n_dir_matrix": OUTPUT_PROFILE_DEBUG,
    "nan_diagnostics_pre_filter": OUTPUT_PROFILE_DEBUG,
    "nan_filter_simulation": OUTPUT_PROFILE_DEBUG,
    "wildcard_candidates": OUTPUT_PROFILE_USER,
    "mars_ranking": OUTPUT_PROFILE_USER,
}

PROFILE_LEVEL: dict[str, int] = {
    OUTPUT_PROFILE_USER: 0,
    OUTPUT_PROFILE_REPRODUCIBLE: 1,
    OUTPUT_PROFILE_DEBUG: 2,
}


class EmptyDecklistError(RuntimeError):
    """Raised when a set/format has no parseable Limitless decklist."""

    def __init__(self, message: str, *, urls: list[str] | None = None) -> None:
        super().__init__(message)
        self.urls = urls or []


class InsufficientRankingDataError(RuntimeError):
    """Raised when fetched data is too sparse to produce a MARS ranking."""


def _output_profile(cfg: dict[str, Any]) -> str:
    """
    Return the configured saved-artifact profile.

    The fallback remains ``debug`` for backward compatibility with older
    configs and tests that expect every intermediate CSV to be written.
    """
    saving = cfg.get("saving") or {}
    profile = str(saving.get("output_profile", OUTPUT_PROFILE_DEBUG)).strip().lower()
    if profile not in OUTPUT_PROFILES:
        raise ValueError(
            "saving.output_profile must be one of: "
            + ", ".join(sorted(OUTPUT_PROFILES))
        )
    return profile


def _should_save_artifact(cfg: dict[str, Any], key: str, df: pd.DataFrame | None = None) -> bool:
    profile = _output_profile(cfg)
    required_profile = ARTIFACT_TIERS.get(key, OUTPUT_PROFILE_DEBUG)
    if PROFILE_LEVEL[profile] < PROFILE_LEVEL[required_profile]:
        return False
    if profile == OUTPUT_PROFILE_USER and key == "wildcard_candidates":
        wildcard_cfg = ((cfg.get("analysis") or {}).get("wildcard_pass") or {})
        return bool(wildcard_cfg.get("enabled", False)) and df is not None and not df.empty
    return True


def _should_save_timestamped_csv(cfg: dict[str, Any], key: str) -> bool:
    saving = cfg.get("saving") or {}
    include_time = bool(saving.get("include_time_when_changed", True))
    return include_time and _output_profile(cfg) == OUTPUT_PROFILE_DEBUG


def _save_csv_artifact(
    df: pd.DataFrame,
    paths: ProjectPaths,
    key: str,
    exp,
    cfg: dict[str, Any],
    *,
    changed: bool,
    index: bool = False,
) -> Path | None:
    if not _should_save_artifact(cfg, key, df):
        return None
    return write_csv_versioned_setaware(
        df,
        paths,
        key,
        exp,
        cfg,
        changed=changed and _should_save_timestamped_csv(cfg, key),
        index=index,
    )


def _write_json_artifact(
    data: dict[str, Any],
    paths: ProjectPaths,
    key: str,
    exp,
    cfg: dict[str, Any],
) -> Path:
    dest_dir = dest_for_key(paths, key, exp)
    prefix = "run_manifest"
    latest_path = dest_dir / f"{prefix}_latest.json"
    latest_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    if _output_profile(cfg) == OUTPUT_PROFILE_DEBUG:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ts_path = dest_dir / f"{prefix}_{stamp}.json"
        ts_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return latest_path


def _compact_cfg_summary(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": cfg.get("source") or {},
        "top_meta": cfg.get("top_meta") or {},
        "analysis": cfg.get("analysis") or {},
        "nan_filter": cfg.get("nan_filter") or {},
        "saving": cfg.get("saving") or {},
    }


def _write_run_manifest(result: "DeckRankingResult") -> Path:
    code = getattr(result.expansion, "code", None)
    name = getattr(result.expansion, "name", None)
    manifest_path = dest_for_key(result.paths, "run_manifest", result.expansion) / "run_manifest_latest.json"
    output_paths = {key: str(path) for key, path in sorted(result.outputs.items())}
    output_paths.setdefault("run_manifest", str(manifest_path))
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "profile": result.diagnostics.get("output_profile"),
        "source_scope": result.diagnostics.get("source_scope"),
        "set": {"code": code, "name": name},
        "decks_url": result.decks_url,
        "frames": {
            key: {"rows": int(df.shape[0]), "columns": int(df.shape[1])}
            for key, df in sorted(result.frames.items())
            if isinstance(df, pd.DataFrame)
        },
        "outputs": output_paths,
        "diagnostics": {
            key: value
            for key, value in sorted(result.diagnostics.items())
            if key
            in {
                "acquisition_source",
                "axis_all_count",
                "axis0_count",
                "axis_kept_count",
                "deck_identity_count",
                "deck_identity_map",
                "duplicate_display_names",
                "decklist_rows",
                "estimated_polite_delay_seconds",
                "heatmap_shape",
                "heatmap_top_n",
                "mars_rows",
                "matchup_cache_hits",
                "matchup_pages",
                "output_profile",
                "report_gamma",
                "report_k_used",
                "top_meta_rows",
                "tournament_api_execution_mode",
                "tournament_api_network_calls",
                "tournament_api_replay_run_id",
                "tournament_api_run_id",
                "wildcard_full_scrape",
            }
        },
        "config_summary": _compact_cfg_summary(result.cfg),
    }
    return _write_json_artifact(manifest, result.paths, "run_manifest", result.expansion, result.cfg)


@dataclass
class DeckRankingResult:
    cfg: dict[str, Any]
    paths: ProjectPaths
    expansion: Any
    decks_url: str
    catalog: list[Any]
    outputs: dict[str, Path] = field(default_factory=dict)
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        code = getattr(self.expansion, "code", None) or "<AUTO>"
        return (
            f"DeckRankingResult(set={code!r}, frames={len(self.frames)}, "
            f"outputs={len(self.outputs)}, diagnostics={len(self.diagnostics)})"
        )

    def summary_lines(self) -> list[str]:
        code = getattr(self.expansion, "code", None)
        name = getattr(self.expansion, "name", None)
        label = f"{code} - {name}" if code and name else (code or "<AUTO>")
        lines = [f"[SET] {label}"]

        decklist = self.frames.get("decklist_raw")
        if decklist is not None:
            cache_hit = self.diagnostics.get("decklist_cache_hit")
            lines.append(f"[FETCH] decklists: {len(decklist)} rows, cache_hit={cache_hit}")

        matchups = self.frames.get("matchup_raw")
        if matchups is not None:
            deck_count = matchups["Deck A"].nunique() if "Deck A" in matchups.columns else "?"
            cache_hits = self.diagnostics.get("matchup_cache_hits")
            timing = self.diagnostics.get("matchup_scrape_timing") or {}
            cache_misses = timing.get("cache_misses")
            elapsed = timing.get("elapsed_seconds")
            delay_total = timing.get("delay_seconds_total")
            extra = ""
            if elapsed is not None:
                extra = f", elapsed={elapsed:.1f}s"
            if delay_total is not None:
                extra += f", delay={delay_total:.1f}s"
            if cache_misses is not None:
                extra += f", cache_misses={cache_misses}"
            lines.append(f"[FETCH] matchups: {deck_count} decks, {len(matchups)} rows, cache_hits={cache_hits}{extra}")

        score = self.frames.get("score_flat")
        wr = self.frames.get("wr_matrix")
        if score is not None or wr is not None:
            axis = self.diagnostics.get("axis_kept_count")
            if axis is None and wr is not None:
                axis = wr.shape[0]
            dropped = self.diagnostics.get("dropped_decks") or []
            score_rows = len(score) if score is not None else "?"
            nan_filter = self.diagnostics.get("nan_filter") or {}
            nan_bits = ""
            if nan_filter:
                ratio = nan_filter.get("applied_max_nan_ratio")
                mode = nan_filter.get("mode")
                share = nan_filter.get("selected_share_kept_%")
                if ratio is not None:
                    nan_bits = f", nan_filter={mode}@{float(ratio):.2f}"
                if share is not None:
                    nan_bits += f", share={float(share):.1f}%"
            lines.append(f"[CORE] axis={axis}, dropped={len(dropped)}, score_rows={score_rows}{nan_bits}")

        ranking = self.frames.get("mars_ranking")
        if ranking is not None:
            mars_diag = self.diagnostics.get("mars_diag") or {}
            auto_k = mars_diag.get("AUTO_K", {}) if isinstance(mars_diag, dict) else {}
            k_used = auto_k.get("K_used", auto_k.get("K_star", "?"))
            lines.append(f"[MARS] ranking_rows={len(ranking)}, K={k_used}")

        report = self.outputs.get("report_latest") or self.outputs.get("report")
        heatmap = self.outputs.get("heatmap_topN_latest") or self.outputs.get("heatmap_topN")
        if report or heatmap:
            bits = []
            if heatmap:
                bits.append(f"heatmap={heatmap}")
            if report:
                bits.append(f"report={report}")
            lines.append(f"[REPORT] {' | '.join(bits)}")

        return lines

    def summary_text(self) -> str:
        return "\n".join(self.summary_lines())


def _load_config(base_dir: Path, config_path: str | Path | None) -> tuple[dict[str, Any], Path, Path]:
    if config_path is None:
        return read_config(base_dir)

    yaml_file = Path(config_path)
    if not yaml_file.is_absolute():
        yaml_file = base_dir / yaml_file
    if not yaml_file.exists():
        raise FileNotFoundError(f"Config not found: {yaml_file}")

    with yaml_file.open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    alias_file = base_dir / ((cfg.get("alias", {}) or {}).get("file", "config/alias_map.json"))
    log.debug("Config loaded from %s", yaml_file)
    if not alias_file.exists():
        log.warning("[init] alias_map.json not found: %s - continuing without aliases.", alias_file)
    return cfg, yaml_file, alias_file


def _acquisition_source(cfg: dict[str, Any]) -> str:
    source_cfg = cfg.get("source") or {}
    value = str(source_cfg.get("acquisition") or ACQUISITION_LEGACY_HTML).strip().lower()
    if value not in ACQUISITION_SOURCES:
        raise ValueError(
            "source.acquisition must be one of: " + ", ".join(sorted(ACQUISITION_SOURCES))
        )
    return value


def _api_format_from_config(cfg: dict[str, Any]) -> str | None:
    source_cfg = cfg.get("source") or {}
    format_cfg = source_cfg.get("format") or {}
    if isinstance(format_cfg, str):
        value = format_cfg.strip()
        return value.upper() or None
    if isinstance(format_cfg, dict):
        mode = str(format_cfg.get("mode") or "auto").strip().lower()
        if mode == "code":
            value = str(format_cfg.get("code") or "").strip()
            return value.upper() or None
    game = str(source_cfg.get("game") or "POCKET").strip().upper()
    return "STANDARD" if game == "POCKET" else None


def _api_set_params(cfg: dict[str, Any]) -> tuple[str, str | None]:
    set_cfg = ((cfg.get("scraping") or {}).get("set") or {})
    mode = str(set_cfg.get("mode") or "auto").strip().lower()
    code = str(set_cfg.get("code") or "").strip() or None
    return mode, code


def _resolve_base_path(base: Path, value: Any, default: str) -> Path:
    raw = Path(str(value or default)).expanduser()
    return raw.resolve() if raw.is_absolute() else (base / raw).resolve()


def _api_scope_url(*, game: str, format: str | None, set_code: str | None) -> str:
    parsed = urlparse(LIMITLESS_DECKS_URL)
    query: dict[str, str] = {"game": str(game).strip().upper()}
    if format:
        query["format"] = str(format).strip().lower()
    if set_code:
        query["set"] = str(set_code).strip()
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), parsed.fragment))


def _api_catalog_context(
    *,
    base: Path,
    cfg: dict[str, Any],
    acquisition_started_at: datetime,
) -> tuple[Expansion, str, list[Expansion], Path]:
    source_cfg = cfg.get("source") or {}
    api_cfg = source_cfg.get("tournament_api") or {}
    catalog_path = _resolve_base_path(
        base,
        api_cfg.get("release_catalog"),
        "data/reference/pocket_releases.json",
    )
    release_catalog = load_release_catalog_snapshot(catalog_path)
    set_mode, set_code = _api_set_params(cfg)
    release = resolve_release(
        release_catalog,
        mode=set_mode,
        code=set_code,
        acquisition_started_at=acquisition_started_at,
    )
    game = str(source_cfg.get("game") or "POCKET").strip().upper()
    fmt = _api_format_from_config(cfg)
    exp = Expansion(code=release.code, name=release.name, is_current=release.is_current, rotation=None)
    scope_url = _api_scope_url(game=game, format=fmt, set_code=release.code)
    catalog = [
        Expansion(code=item.code, name=item.name, is_current=item.is_current, rotation=None)
        for item in release_catalog.releases
    ]
    return exp, scope_url, catalog, catalog_path


def _run_tournament_api_acquisition_for_production(
    *,
    base: Path,
    cfg: dict[str, Any],
    paths: ProjectPaths,
    acquisition_started_at: datetime,
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any], Expansion, str, list[Expansion]]:
    source_cfg = cfg.get("source") or {}
    api_cfg = source_cfg.get("tournament_api") or {}
    game = str(source_cfg.get("game") or "POCKET").strip().upper()
    fmt = _api_format_from_config(cfg)
    set_mode, set_code = _api_set_params(cfg)
    execution_mode = str(api_cfg.get("execution_mode") or "live").strip().lower()
    replay_run_id = str(api_cfg.get("replay_run_id") or "").strip() or None
    raw_store_root = _resolve_base_path(base, api_cfg.get("raw_store_root"), "data/raw/limitless_api")
    cache_root = _resolve_base_path(base, api_cfg.get("cache_root"), "cache/limitless_api")
    _, _, catalog, catalog_path = _api_catalog_context(
        base=base,
        cfg=cfg,
        acquisition_started_at=acquisition_started_at,
    )

    api_result = run_limitless_api_acquisition(
        game=game,
        format=fmt,
        set_mode=set_mode,
        set_code=set_code,
        acquisition_started_at=None if execution_mode == "offline" else acquisition_started_at,
        execution_mode=execution_mode,
        raw_store_root=raw_store_root,
        release_catalog=catalog_path,
        cache_root=cache_root,
        replay_run_id=replay_run_id,
        reuse_latest_raw=bool(api_cfg.get("reuse_latest_raw", True)),
    )

    bridged = bridge_tournament_api_frames(api_result.frames)
    scope = api_result.manifest.scope
    exp = Expansion(code=scope.set_code, name=scope.set_name, is_current=False, rotation=None)
    decks_url = _api_scope_url(game=scope.game, format=scope.format, set_code=scope.set_code)
    paths = init_paths(base, cfg, source_url=decks_url)

    top_meta_path = _save_csv_artifact(
        bridged.top_meta_decklist, paths, "top_meta_decklist", exp, cfg, changed=False, index=False
    )
    matchup_path = _save_csv_artifact(
        bridged.matchup_raw, paths, "matchup_raw", exp, cfg, changed=False, index=False
    )
    outputs: dict[str, Path] = {}
    if top_meta_path is not None:
        outputs["top_meta_decklist"] = top_meta_path
    if matchup_path is not None:
        outputs["matchup_raw"] = matchup_path

    identity_diag = dict(identity_mapping_diagnostics(bridged.deck_identity_map))
    frames = {
        "top_meta_decklist": bridged.top_meta_decklist,
        "matchup_raw": bridged.matchup_raw,
        "dense_score": bridged.dense_score,
        "deck_identity_map": bridged.deck_identity_map,
        "acquisition_top_meta_decklist": api_result.frames.top_meta_decklist,
        "acquisition_matchup_raw": api_result.frames.matchup_raw,
        "acquisition_dense_score": api_result.frames.dense_score,
    }
    diagnostics = {
        "acquisition_source": ACQUISITION_TOURNAMENT_API,
        "tournament_api_execution_mode": execution_mode,
        "tournament_api_run_id": api_result.manifest.run_id,
        "tournament_api_replay_run_id": replay_run_id,
        "tournament_api_network_calls": api_result.diagnostics.get("network_calls"),
        "tournament_api_diagnostics": dict(api_result.diagnostics),
        "deck_identity_count": identity_diag["count"],
        "deck_identity_map": identity_diag["mapping"],
        "duplicate_display_names": identity_diag["duplicate_display_names"],
    }
    return frames, outputs, diagnostics, exp, decks_url, catalog


def _is_nullish(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null"})


def _float_config(value: Any, default: float) -> float:
    return float(default) if _is_nullish(value) else float(value)


def _int_config(value: Any, default: int) -> int:
    return int(default) if _is_nullish(value) else int(value)


def _optional_int_config(value: Any) -> int | None:
    return None if _is_nullish(value) else int(value)


def _optional_float_config(value: Any) -> float | None:
    return None if _is_nullish(value) else float(value)


def _share_percent_series(df: pd.DataFrame) -> pd.Series:
    if "Share_frac" in df.columns:
        return pd.to_numeric(df["Share_frac"], errors="coerce").fillna(0.0) * 100.0
    if "Share_%" in df.columns:
        return pd.to_numeric(df["Share_%"], errors="coerce").fillna(0.0)
    if "Share" in df.columns:
        return pd.to_numeric(
            df["Share"]
            .astype(str)
            .str.replace("\xa0", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.strip(),
            errors="coerce",
        ).fillna(0.0)
    return pd.Series([0.0] * len(df), index=df.index)


def _candidate_pool_from_top_meta(top_meta_alias: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    pool_cfg = ((cfg.get("analysis") or {}).get("candidate_pool") or {})
    share_target = _optional_float_config(pool_cfg.get("share_pct"))
    if share_target is None or share_target >= 100.0:
        return top_meta_alias.copy(), {
            "enabled": False,
            "share_pct": share_target,
            "axis_all_count": len(top_meta_alias),
            "axis_candidate_count": len(top_meta_alias),
            "share_candidate_%": round(float(_share_percent_series(top_meta_alias).sum()), 4),
            "dropped_by_pool_count": 0,
        }

    df = top_meta_alias.copy()
    df["_candidate_share_%"] = _share_percent_series(df)
    df = df.sort_values("_candidate_share_%", ascending=False, kind="mergesort").reset_index(drop=True)
    df["_candidate_share_cum_%"] = df["_candidate_share_%"].cumsum()
    if (df["_candidate_share_cum_%"] >= share_target).any():
        pos = int((df["_candidate_share_cum_%"] >= share_target).idxmax())
    else:
        pos = len(df) - 1
    candidate = df.iloc[: pos + 1].drop(columns=["_candidate_share_%", "_candidate_share_cum_%"]).copy()
    share_candidate = float(df.iloc[: pos + 1]["_candidate_share_%"].sum())
    return candidate, {
        "enabled": True,
        "share_pct": float(share_target),
        "axis_all_count": len(top_meta_alias),
        "axis_candidate_count": len(candidate),
        "share_candidate_%": round(share_candidate, 4),
        "dropped_by_pool_count": max(0, len(top_meta_alias) - len(candidate)),
    }


def _scrape_rate_from_config(scraping: dict[str, Any]) -> tuple[float, float, bool]:
    rate_limit = float(scraping.get("request_delay_sec", 5.0))
    rate_jitter = float(scraping.get("request_delay_jitter_frac", 0.25))
    dev_fast = os.environ.get(DEV_FAST_SCRAPE_ENV) == "1"
    if dev_fast:
        return 0.0, 0.0, True
    return rate_limit, rate_jitter, False


def _decklist_url_fallbacks(url: str, cfg: dict[str, Any]) -> list[str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    game = (query.get("game", [((cfg.get("source") or {}).get("game") or "")])[0] or "").upper()
    fmt = (query.get("format", [None])[0] or "").lower()
    rotation = query.get("rotation", [None])[0]
    if game != "PTCG" or not rotation:
        return []

    candidates: list[str] = []

    def add(candidate_query: dict[str, list[str]]) -> None:
        candidate = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode({key: values[0] for key, values in candidate_query.items() if values and values[0] is not None}),
                parsed.fragment,
            )
        )
        if candidate != url and candidate not in candidates:
            candidates.append(candidate)

    no_rotation = {key: list(values) for key, values in query.items()}
    no_rotation.pop("rotation", None)
    add(no_rotation)

    # Rotation buckets are a Standard-only selector on Limitless. If a stale or
    # manually supplied non-Standard URL carries rotation, only retry without it.
    if fmt == "standard":
        for year in ("2026", "2025", "2024", "2023", "2022", "2021"):
            if year == str(rotation):
                continue
            alt = {key: list(values) for key, values in query.items()}
            alt["rotation"] = [year]
            add(alt)

    return candidates


def _scrape_parse_decklist_with_fallbacks(
    *,
    decks_url: str,
    cfg: dict[str, Any],
    paths: ProjectPaths,
    ttl_min: int,
    force_refresh: bool,
    headless: bool,
) -> tuple[pd.DataFrame, str, bool, str]:
    tried: list[str] = []
    last_error: Exception | None = None
    for idx, url in enumerate([decks_url, *_decklist_url_fallbacks(decks_url, cfg)]):
        tried.append(url)
        html, from_cache = scrape_decklist_html(
            url,
            cache_dir=paths.cache,
            ttl_minutes=ttl_min,
            force_refresh=force_refresh,
            headless=headless,
        )
        try:
            df_decklist = parse_decklist_table(html)
        except RuntimeError as exc:
            last_error = exc
            if idx == 0:
                log.warning("[decklist fallback] url non parsabile, provo alternative: %s", url)
            else:
                log.warning("[decklist fallback] alternativa non parsabile: %s", url)
            continue
        if idx > 0:
            log.warning("[decklist fallback] uso url alternativa: %s", url)
        return df_decklist, html, from_cache, url

    raise EmptyDecklistError(
        f"Decklist vuota o non parsabile per tutte le URL provate ({len(tried)}).",
        urls=tried,
    ) from last_error


def _wildcard_pass_enabled(cfg: dict[str, Any]) -> bool:
    wild_cfg = ((cfg.get("analysis") or {}).get("wildcard_pass") or {})
    return bool(wild_cfg.get("enabled", False))


def _build_wildcard_candidates(
    *,
    cfg: dict[str, Any],
    top_meta_alias_all: pd.DataFrame,
    nan_diag_df: pd.DataFrame,
    df_agg: pd.DataFrame,
    n_dir_all: pd.DataFrame,
    axis0: list[str],
    axis_kept: list[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    wild_cfg = ((cfg.get("analysis") or {}).get("wildcard_pass") or {})
    enabled = bool(wild_cfg.get("enabled", False))
    min_coverage = _float_config(wild_cfg.get("min_coverage_vs_core_pct"), 60.0)
    min_n = _int_config(wild_cfg.get("min_n_vs_core"), 50)

    columns = [
        "Deck",
        "Share_%",
        "in_candidate_pool",
        "dropped_by_nan_filter",
        "coverage_vs_core_%",
        "observed_core_opponents",
        "core_opponents",
        "N_vs_core",
        "WR_vs_core_weighted_%",
        "coverage_all_%",
        "observed_all_opponents",
        "total_all_opponents",
        "total_matches_all",
        "nan_ratio_all",
    ]
    if not enabled or not axis_kept:
        return pd.DataFrame(columns=columns), {
            "enabled": enabled,
            "candidate_count": 0,
            "min_coverage_vs_core_%": min_coverage,
            "min_n_vs_core": min_n,
        }

    core_axis = [str(deck).strip() for deck in axis_kept]
    core_set = set(core_axis)
    pool_set = {str(deck).strip() for deck in axis0}
    n_all = n_dir_all.copy()
    n_all.index = n_all.index.astype(str).str.strip()
    n_all.columns = n_all.columns.astype(str).str.strip()

    perf_lookup: dict[str, tuple[float, float]] = {}
    perf_required = {"Deck A", "Deck B", "W", "L"}
    if df_agg is not None and not df_agg.empty and perf_required.issubset(df_agg.columns):
        perf = df_agg.copy()
        perf["Deck A"] = perf["Deck A"].astype(str).str.strip()
        perf["Deck B"] = perf["Deck B"].astype(str).str.strip()
        perf = perf[perf["Deck B"].isin(core_set)].copy()
        if not perf.empty:
            perf["W"] = pd.to_numeric(perf["W"], errors="coerce").fillna(0.0)
            perf["L"] = pd.to_numeric(perf["L"], errors="coerce").fillna(0.0)
            grouped = perf.groupby("Deck A", sort=False)[["W", "L"]].sum()
            perf_lookup = {
                str(deck).strip(): (float(row["W"]), float(row["L"]))
                for deck, row in grouped.iterrows()
            }

    diag = nan_diag_df.copy()
    diag["Deck"] = diag["Deck"].astype(str).str.strip()
    if "Share_%" not in diag.columns:
        shares = top_meta_alias_all.copy()
        shares["Deck"] = shares["Deck"].astype(str).str.strip()
        diag = diag.merge(
            pd.DataFrame({"Deck": shares["Deck"], "Share_%": _share_percent_series(shares)}),
            on="Deck",
            how="left",
        )

    rows: list[dict[str, Any]] = []
    for _, row in diag.iterrows():
        deck = str(row["Deck"]).strip()
        if deck in core_set:
            continue
        if deck not in n_all.index:
            continue
        core_cols = [opponent for opponent in core_axis if opponent in n_all.columns and opponent != deck]
        if not core_cols:
            continue
        vs_core = pd.to_numeric(n_all.loc[deck, core_cols], errors="coerce").fillna(0.0)
        observed_core = int((vs_core > 0).sum())
        n_vs_core = float(vs_core.sum())
        coverage_vs_core = 100.0 * observed_core / len(core_cols)
        if coverage_vs_core < min_coverage or n_vs_core < min_n:
            continue
        wins_vs_core, losses_vs_core = perf_lookup.get(deck, (0.0, 0.0))
        denom_vs_core = wins_vs_core + losses_vs_core
        wr_vs_core = round(100.0 * wins_vs_core / denom_vs_core, 4) if denom_vs_core > 0 else pd.NA
        rows.append(
            {
                "Deck": deck,
                "Share_%": float(row.get("Share_%", 0.0) or 0.0),
                "in_candidate_pool": deck in pool_set,
                "dropped_by_nan_filter": deck in pool_set and deck not in core_set,
                "coverage_vs_core_%": round(coverage_vs_core, 4),
                "observed_core_opponents": observed_core,
                "core_opponents": len(core_cols),
                "N_vs_core": int(round(n_vs_core)),
                "WR_vs_core_weighted_%": wr_vs_core,
                "coverage_all_%": float(row.get("coverage_%", 0.0) or 0.0),
                "observed_all_opponents": int(row.get("observed_opponents", 0) or 0),
                "total_all_opponents": int(row.get("total_opponents", 0) or 0),
                "total_matches_all": int(row.get("total_matches", 0) or 0),
                "nan_ratio_all": float(row.get("nan_ratio", 0.0) or 0.0),
            }
        )

    out = pd.DataFrame(rows, columns=columns)
    if not out.empty:
        out = out.sort_values(
            ["coverage_vs_core_%", "N_vs_core", "WR_vs_core_weighted_%", "Share_%"],
            ascending=[False, False, False, False],
            kind="mergesort",
        ).reset_index(drop=True)
    diagnostics = {
        "enabled": enabled,
        "candidate_count": len(out),
        "min_coverage_vs_core_%": min_coverage,
        "min_n_vs_core": min_n,
    }
    return out, diagnostics


def _scrape_decklists_and_matchups(
    *,
    cfg: dict[str, Any],
    paths: ProjectPaths,
    exp,
    decks_url: str,
    show_progress: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any], Any]:
    scraping = cfg.get("scraping", {}) or {}
    top_meta_cfg = cfg.get("top_meta", {}) or {}

    ttl_min = int(scraping.get("cache_ttl_min", 720))
    force_refresh = bool(scraping.get("force_refresh", False))
    rate_limit, rate_jitter, dev_fast_scrape = _scrape_rate_from_config(scraping)
    timeout = int(scraping.get("timeout_sec", 20) or scraping.get("request_timeout_sec", 20))
    headless = bool((scraping.get("selenium", {}) or {}).get("headless", True))
    top_thresh_raw = top_meta_cfg.get("threshold_pct", None)
    top_thresh = None if top_thresh_raw is None else float(top_thresh_raw)

    if dev_fast_scrape:
        log.warning(
            "[DEV FAST SCRAPE] %s=1 attivo: delay e jitter delle richieste matchup sono disattivati.",
            DEV_FAST_SCRAPE_ENV,
        )

    log.info(
        "[scrape] url=%s | ttl_min=%s | headless=%s | refresh=%s | rate=%.2fs | jitter=%.0f%% | top_thresh=%s | dev_fast=%s",
        decks_url,
        ttl_min,
        headless,
        force_refresh,
        rate_limit,
        rate_jitter * 100,
        "ALL" if top_thresh is None else f"{top_thresh:.1f}%",
        dev_fast_scrape,
    )

    df_decklist, html, decklist_from_cache, decks_url = _scrape_parse_decklist_with_fallbacks(
        decks_url=decks_url,
        cfg=cfg,
        paths=paths,
        ttl_min=ttl_min,
        force_refresh=force_refresh,
        headless=headless,
    )
    exp, exp_source = _resolve_expansion_from_decklist_page(exp, html, df_decklist)
    url_query = parse_qs(urlparse(decks_url).query)
    url_format = (url_query.get("format", [""])[0] or "").lower()
    url_rotation = url_query.get("rotation", [None])[0]
    if url_format == "standard" and getattr(exp, "code", None) and url_rotation and getattr(exp, "rotation", None) != url_rotation:
        exp = Expansion(
            code=getattr(exp, "code", None),
            name=getattr(exp, "name", None),
            is_current=bool(getattr(exp, "is_current", False)),
            rotation=url_rotation,
        )
        exp_source = exp_source or "decklist-url-rotation"
    if exp_source:
        log.info("[SET AUTO] source=%s | code=%s | name=%s", exp_source, getattr(exp, "code", None), getattr(exp, "name", None))

    decklist_path = _save_csv_artifact(
        df_decklist,
        paths,
        "decklist_raw",
        exp,
        cfg,
        changed=False,
        index=True,
    )

    df_top = filter_top_meta(df_decklist, threshold_pct=top_thresh)
    df_top["Matchup URL"] = df_top["URL"].map(to_matchup_url)
    top_meta_path = _save_csv_artifact(
        df_top,
        paths,
        "top_meta_decklist",
        exp,
        cfg,
        changed=False,
        index=False,
    )

    matchup_urls = [
        (str(row["Deck"]), str(row["Matchup URL"]))
        for _, row in df_top.dropna(subset=["Matchup URL"]).iterrows()
    ]
    if not matchup_urls:
        raise RuntimeError("No matchup URL found in top-meta.")

    polite_delay_seconds = len(matchup_urls) * rate_limit
    wildcard_full_scrape = _wildcard_pass_enabled(cfg) and top_thresh is None
    if wildcard_full_scrape:
        log.warning(
            "[WILDCARD] wildcard_pass enabled with top_meta.threshold_pct=null: "
            "%d matchup pages will be considered. With rate=%.2fs, polite waiting alone can take about %.1f minutes before cache.",
            len(matchup_urls),
            rate_limit,
            polite_delay_seconds / 60.0,
        )

    session_kwargs = dict(
        max_retries=int(scraping.get("max_retries", 3)),
        backoff=float(scraping.get("backoff_factor", 0.7)),
        timeout=timeout,
    )
    if scraping.get("user_agent"):
        session_kwargs["user_agent"] = scraping.get("user_agent")
    session = make_session(**session_kwargs)
    try:
        scrape_result = scrape_matchups(
            matchup_urls,
            session=session,
            cache_dir=paths.cache,
            ttl_minutes=ttl_min,
            force_refresh=force_refresh,
            rate_limit_seconds=rate_limit,
            rate_limit_jitter_frac=rate_jitter,
            progress=show_progress,
            pbar_desc=f"Matchups Top {len(matchup_urls)}",
            collect_diagnostics=True,
        )
        if len(scrape_result) == 4:
            df_matchups, total_pages, cache_hits, scrape_timing = scrape_result
        else:
            df_matchups, total_pages, cache_hits = scrape_result
            scrape_timing = {}
    finally:
        session.close()

    required = {"Deck A", "Deck B", "W", "L", "T", "N", "Winrate"}
    missing = required - set(df_matchups.columns)
    if missing:
        raise KeyError(f"[matchup_raw] missing required columns: {sorted(missing)}")

    matchup_path = _save_csv_artifact(
        df_matchups,
        paths,
        "matchup_raw",
        exp,
        cfg,
        changed=False,
        index=False,
    )

    frames = {
        "decklist_raw": df_decklist,
        "top_meta_decklist": df_top,
        "matchup_raw": df_matchups,
    }
    outputs = {}
    if decklist_path is not None:
        outputs["decklist_raw"] = decklist_path
    if top_meta_path is not None:
        outputs["top_meta_decklist"] = top_meta_path
    if matchup_path is not None:
        outputs["matchup_raw"] = matchup_path
    diagnostics = {
        "decklist_cache_hit": decklist_from_cache,
        "decklist_rows": len(df_decklist),
        "top_meta_rows": len(df_top),
        "matchup_url_count": len(matchup_urls),
        "matchup_pages": total_pages,
        "matchup_cache_hits": cache_hits,
        "developer_fast_scrape": dev_fast_scrape,
        "wildcard_full_scrape": wildcard_full_scrape,
        "estimated_polite_delay_seconds": polite_delay_seconds,
        "matchup_scrape_timing": scrape_timing,
    }
    if exp_source:
        diagnostics["set_resolution_source"] = exp_source
    if scrape_timing:
        log.info(
            "[FETCH] matchup timing: pages=%s cache_hit=%s cache_miss=%s elapsed=%.1fs delay=%.1fs avg_page=%.2fs",
            scrape_timing.get("unique_pages"),
            scrape_timing.get("cache_hits"),
            scrape_timing.get("cache_misses"),
            scrape_timing.get("elapsed_seconds", 0.0),
            scrape_timing.get("delay_seconds_total", 0.0),
            scrape_timing.get("avg_seconds_per_page", 0.0),
        )
    return frames, outputs, diagnostics, exp


def _resolve_expansion_from_decklist_page(exp, html: str, df_decklist: pd.DataFrame):
    if getattr(exp, "code", None):
        return exp, None

    try:
        expansions = parse_expansions_from_html(html)
    except Exception:
        expansions = []

    if expansions:
        selected = next((item for item in expansions if getattr(item, "is_current", False)), None) or expansions[0]
        if getattr(selected, "code", None):
            return selected, "decklist-html"

    codes: list[str] = []
    if df_decklist is not None and "URL" in df_decklist.columns:
        for url in df_decklist["URL"].dropna().astype(str):
            try:
                code = parse_qs(urlparse(url).query).get("set", [None])[0]
            except Exception:
                code = None
            if code and SET_CODE_RE.fullmatch(str(code)) and code not in codes:
                codes.append(str(code))

    if len(codes) == 1:
        return Expansion(code=codes[0], name=None, is_current=True), "decklist-urls"

    return exp, None


def _load_contract_frame(
    *,
    paths: ProjectPaths,
    key: str,
    exp,
    cfg: dict[str, Any],
) -> pd.DataFrame:
    path = find_latest(paths, key, exp, cfg)
    if path is None:
        raise FileNotFoundError(f"{key}_latest not found for the current set.")
    return pd.read_csv(path)


def _load_matrix_contract(
    *,
    paths: ProjectPaths,
    key: str,
    exp,
    cfg: dict[str, Any],
) -> pd.DataFrame:
    path = find_latest(paths, key, exp, cfg)
    if path is None:
        raise FileNotFoundError(f"{key}_latest not found for the current set.")
    return pd.read_csv(path, index_col=0)


def _top_meta_for_mars(
    cfg: dict[str, Any],
    paths: ProjectPaths,
    df_top_meta: pd.DataFrame,
    *,
    alias_index_override: dict[str, str] | None = None,
) -> pd.DataFrame:
    if alias_index_override is not None:
        return topmeta_post_alias(df_top_meta, dict(alias_index_override))
    alias_cfg = cfg.get("alias") or {}
    if not bool(alias_cfg.get("apply", True)):
        return df_top_meta
    alias_path = paths.base / alias_cfg.get("file", "config/alias_map.json")
    alias_idx = build_alias_index(load_alias_map(alias_path))
    return topmeta_post_alias(df_top_meta, alias_idx)


def _build_core_matrices(
    *,
    cfg: dict[str, Any],
    paths: ProjectPaths,
    exp,
    df_matchup_raw: pd.DataFrame | None = None,
    df_top_meta: pd.DataFrame | None = None,
    preserve_zero_evidence: bool = False,
    alias_index_override: dict[str, str] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any]]:
    if df_matchup_raw is None:
        df_matchup_raw = _load_contract_frame(paths=paths, key="matchup_raw", exp=exp, cfg=cfg)
    if df_top_meta is None:
        df_top_meta = _load_contract_frame(paths=paths, key="top_meta_decklist", exp=exp, cfg=cfg)

    if alias_index_override is None:
        alias_cfg = cfg.get("alias") or {}
        apply_alias = bool(alias_cfg.get("apply", True))
        alias_path = paths.base / alias_cfg.get("file", "config/alias_map.json")
        alias_idx = build_alias_index(load_alias_map(alias_path)) if apply_alias else {}
    else:
        alias_idx = dict(alias_index_override)

    nan_cfg = cfg.get("nan_filter") or {}
    nan_filter_mode = str(nan_cfg.get("mode", "fixed")).strip().lower()
    max_nan_ratio = _float_config(nan_cfg.get("max_nan_ratio"), 0.15)
    min_nan_allowed = _int_config(nan_cfg.get("min_nan_allowed"), 1)
    use_ceil = bool(nan_cfg.get("use_ceil", False))

    top_meta_alias_all = topmeta_post_alias(df_top_meta, alias_idx)
    axis_all = top_meta_alias_all["Deck"].astype(str).str.strip().tolist()
    if len(axis_all) < 2:
        raise InsufficientRankingDataError(
            f"Asse iniziale troppo piccolo (T0={len(axis_all)}). Controlla top-meta/alias."
        )

    top_meta_alias, candidate_pool_diag = _candidate_pool_from_top_meta(top_meta_alias_all, cfg)
    axis0 = top_meta_alias["Deck"].astype(str).str.strip().tolist()
    if len(axis0) < 2:
        raise RuntimeError(f"Candidate pool troppo piccolo (T0={len(axis0)}). Controlla analysis.candidate_pool.")

    df_max = maxN_flat(df_matchup_raw)
    df_agg = apply_alias_and_aggregate(df_max, alias_idx)
    w_all, l_all, _, wr_all = build_matrices(df_agg, axis_all)
    n_dir_all = n_dir_from_WL(w_all, l_all)
    nan_diag_df, nan_diag_summary = build_nan_diagnostics(wr_all, n_dir_all, top_meta_alias_all)
    w0, l0, _, wr0 = build_matrices(df_agg, axis0)

    nan_filter_selection: dict[str, Any] = {"mode": nan_filter_mode}
    nan_filter_simulation = pd.DataFrame()
    if nan_filter_mode == "dynamic":
        dyn_cfg = nan_cfg.get("dynamic", {}) or {}
        min_axis_raw = dyn_cfg.get("min_axis_count", 40)
        max_nan_ratio, nan_filter_simulation, nan_filter_selection = choose_dynamic_nan_filter(
            wr0,
            top_meta_alias,
            min_nan_ratio=_float_config(dyn_cfg.get("min_nan_ratio"), max_nan_ratio),
            max_nan_ratio=_float_config(dyn_cfg.get("max_nan_ratio"), 0.50),
            step=_float_config(dyn_cfg.get("step"), 0.05),
            target_share_pct=_float_config(dyn_cfg.get("target_share_pct"), 80.0),
            min_axis_count=_optional_int_config(min_axis_raw),
            min_nan_allowed=min_nan_allowed,
            use_ceil=use_ceil,
        )
    elif nan_filter_mode != "fixed":
        raise ValueError("nan_filter.mode deve essere 'fixed' oppure 'dynamic'.")

    log.info(
        "[nan-filter] mode=%s | selected_max_nan_ratio=%.3f | axis_all=%d | axis_pool=%d | min_nan_allowed=%d | ceil=%s",
        nan_filter_mode,
        max_nan_ratio,
        len(axis_all),
        len(axis0),
        min_nan_allowed,
        use_ceil,
    )
    wr_kept, dropped = filter_wr_nan_iterative(
        wr0,
        max_nan_ratio=max_nan_ratio,
        min_nan_allowed=min_nan_allowed,
        use_ceil=use_ceil,
    )
    axis_kept = wr_kept.index.tolist()
    log.debug("[nan-filter] kept=%d | dropped=%d", len(axis_kept), len(dropped))

    score_df = build_score_table_filtered(
        df_agg,
        axis_kept,
        preserve_zero_evidence=preserve_zero_evidence,
    )
    w_mat, l_mat, _, wr_mat = build_matrices(score_df, axis_kept)
    n_dir = n_dir_from_WL(w_mat, l_mat)
    wildcard_df, wildcard_summary = _build_wildcard_candidates(
        cfg=cfg,
        top_meta_alias_all=top_meta_alias_all,
        nan_diag_df=nan_diag_df,
        df_agg=df_agg,
        n_dir_all=n_dir_all,
        axis0=axis0,
        axis_kept=axis_kept,
    )

    score_path = _save_csv_artifact(score_df, paths, "score_flat", exp, cfg, changed=True, index=False)
    wr_path = _save_csv_artifact(wr_mat, paths, "wr_matrix", exp, cfg, changed=True, index=True)
    n_dir_path = _save_csv_artifact(n_dir, paths, "n_dir_matrix", exp, cfg, changed=True, index=True)
    nan_diag_path = _save_csv_artifact(
        nan_diag_df,
        paths,
        "nan_diagnostics_pre_filter",
        exp,
        cfg,
        changed=True,
        index=False,
    )
    wildcard_path = _save_csv_artifact(
        wildcard_df,
        paths,
        "wildcard_candidates",
        exp,
        cfg,
        changed=True,
        index=False,
    )
    nan_filter_sim_path = None
    if not nan_filter_simulation.empty:
        nan_filter_sim_path = _save_csv_artifact(
            nan_filter_simulation,
            paths,
            "nan_filter_simulation",
            exp,
            cfg,
            changed=True,
            index=False,
        )

    frames = {
        "score_flat": score_df,
        "wr_matrix": wr_mat,
        "n_dir_matrix": n_dir,
        "nan_diagnostics_pre_filter": nan_diag_df,
        "nan_filter_simulation": nan_filter_simulation,
        "wildcard_candidates": wildcard_df,
    }
    outputs = {}
    if score_path is not None:
        outputs["score_flat"] = score_path
    if wr_path is not None:
        outputs["wr_matrix"] = wr_path
    if n_dir_path is not None:
        outputs["n_dir_matrix"] = n_dir_path
    if nan_diag_path is not None:
        outputs["nan_diagnostics_pre_filter"] = nan_diag_path
    if wildcard_path is not None:
        outputs["wildcard_candidates"] = wildcard_path
    if nan_filter_sim_path is not None:
        outputs["nan_filter_simulation"] = nan_filter_sim_path
    diagnostics = {
        "axis_all_count": len(axis_all),
        "axis0_count": len(axis0),
        "axis_kept_count": len(axis_kept),
        "dropped_decks": dropped,
        "candidate_pool": candidate_pool_diag,
        "nan_diagnostics_pre_filter": nan_diag_summary,
        "wildcard_candidates": wildcard_summary,
        "nan_filter": {
            **nan_filter_selection,
            "applied_max_nan_ratio": max_nan_ratio,
            "dropped_count": len(dropped),
        },
    }
    return frames, outputs, diagnostics


def _run_mars_stage(
    *,
    cfg: dict[str, Any],
    paths: ProjectPaths,
    exp,
    score_df: pd.DataFrame | None = None,
    wr_matrix: pd.DataFrame | None = None,
    n_dir_matrix: pd.DataFrame | None = None,
    top_meta_df: pd.DataFrame | None = None,
    alias_index_override: dict[str, str] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any]]:
    if score_df is None:
        score_df = _load_contract_frame(paths=paths, key="score_flat", exp=exp, cfg=cfg)
    if wr_matrix is None:
        wr_matrix = _load_matrix_contract(paths=paths, key="wr_matrix", exp=exp, cfg=cfg)
    if n_dir_matrix is None:
        n_dir_matrix = _load_matrix_contract(paths=paths, key="n_dir_matrix", exp=exp, cfg=cfg)
    if top_meta_df is None:
        top_meta_df = _load_contract_frame(paths=paths, key="top_meta_decklist", exp=exp, cfg=cfg)

    mars_cfg = MARSConfig(**(cfg.get("mars") or {}))
    top_meta_mars = _top_meta_for_mars(
        cfg, paths, top_meta_df, alias_index_override=alias_index_override
    )
    try:
        ranking, diag, coverage_df, missing_pairs_long = run_mars_core(
            filtered_wr=wr_matrix,
            n_dir=n_dir_matrix,
            score_flat=score_df,
            top_meta_df=top_meta_mars,
            cfg=mars_cfg,
        )
    except ValueError as exc:
        if "Missing post-filter score_flat" in str(exc):
            raise InsufficientRankingDataError(str(exc)) from exc
        raise

    ranking_path = _save_csv_artifact(
        ranking,
        paths,
        "mars_ranking",
        exp,
        cfg,
        changed=True,
        index=True,
    )

    frames = {
        "mars_ranking": ranking,
        "mars_coverage": coverage_df,
        "mars_missing_pairs": missing_pairs_long,
    }
    outputs = {"mars_ranking": ranking_path} if ranking_path is not None else {}
    diagnostics = {
        "mars_diag": diag,
        "mars_rows": len(ranking),
    }
    log.debug("[MARS] ranking_rows=%d | saved=%s", len(ranking), ranking_path)
    return frames, outputs, diagnostics


def _label_for_expansion(exp) -> str:
    code = getattr(exp, "code", None)
    name = getattr(exp, "name", None)
    if code and name:
        return f"{code} - {name}"
    return code or "<AUTO>"


def _save_heatmap_stage(
    *,
    cfg: dict[str, Any],
    paths: ProjectPaths,
    exp,
    ranking: pd.DataFrame | None,
    wr_matrix: pd.DataFrame | None,
    top_n: int = 10,
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any]]:
    if ranking is None or ranking.empty:
        raise RuntimeError("Missing ranking: cannot generate heatmap.")
    if wr_matrix is None or wr_matrix.empty:
        raise RuntimeError("Missing wr_matrix: cannot generate heatmap.")
    if len(ranking) < 2:
        log.warning("Ranking has fewer than 2 decks: heatmap skipped.")
        return {}, {}, {"heatmap_skipped": True}

    top_n = max(2, min(int(top_n), len(ranking)))
    label = _label_for_expansion(exp)
    fig, _, wr_sub = show_wr_heatmap(
        ranking,
        wr=wr_matrix,
        top_n=top_n,
        annot=True,
        fmt=".1f",
        title=f"WR Heatmap - Top {top_n} (set {label})",
        save=False,
    )

    heatmap_dir = dest_for_key(paths, key="heatmap_topN", exp=exp)
    versioned_path, latest_path = save_plot_dual(
        fig,
        base_dir=heatmap_dir,
        prefix="wr_heatmap",
        tag=f"T{top_n}",
        fmt="png",
        dpi=300,
        also_versioned=_output_profile(cfg) == OUTPUT_PROFILE_DEBUG,
    )
    try:
        import matplotlib.pyplot as plt

        plt.close(fig)
    except Exception:
        pass

    outputs = {"heatmap_topN_latest": latest_path}
    if versioned_path is not None:
        outputs["heatmap_topN"] = versioned_path
    diagnostics = {
        "heatmap_top_n": top_n,
        "heatmap_shape": tuple(wr_sub.shape),
    }
    log.debug("[HEATMAP] top_n=%d | saved=%s", top_n, latest_path)
    return {"heatmap_wr_sub": wr_sub}, outputs, diagnostics


def _write_report_stage(
    *,
    cfg: dict[str, Any],
    paths: ProjectPaths,
    exp,
    ranking: pd.DataFrame | None,
    wr_matrix: pd.DataFrame | None,
    n_dir_matrix: pd.DataFrame | None,
    score_df: pd.DataFrame | None,
    top_meta_df: pd.DataFrame | None,
    mars_diag: dict[str, Any] | None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any]]:
    if ranking is None or ranking.empty:
        ranking = _load_contract_frame(paths=paths, key="mars_ranking", exp=exp, cfg=cfg)
    if wr_matrix is None or wr_matrix.empty:
        wr_matrix = _load_matrix_contract(paths=paths, key="wr_matrix", exp=exp, cfg=cfg)
    if n_dir_matrix is None or n_dir_matrix.empty:
        n_dir_matrix = _load_matrix_contract(paths=paths, key="n_dir_matrix", exp=exp, cfg=cfg)
    if score_df is None or score_df.empty:
        score_df = _load_contract_frame(paths=paths, key="score_flat", exp=exp, cfg=cfg)
    if top_meta_df is None or top_meta_df.empty:
        top_meta_df = _load_contract_frame(paths=paths, key="top_meta_decklist", exp=exp, cfg=cfg)

    if len(ranking) < 1 or wr_matrix.shape[0] < 2:
        log.warning("Report saltato: ranking/WR insufficienti.")
        return {}, {}, {"report_skipped": True}

    mars_cfg = MARSConfig(**(cfg.get("mars") or {}))
    top_meta_mars = _top_meta_for_mars(cfg, paths, top_meta_df)
    axis = list(wr_matrix.index)
    p_weights, meta_info = blend_meta(axis, n_dir_matrix, top_meta_mars, mars_cfg)

    auto_k = (mars_diag or {}).get("AUTO_K", {}) if isinstance(mars_diag, dict) else {}
    k_used = float(auto_k.get("K_used", auto_k.get("K_star", 1.0)))
    gamma = meta_info.get("gamma")

    report_dir = dest_for_key(paths, key="report", exp=exp)

    versioned_path, latest_path, meta = write_mars_matchup_report(
        ranking_df=ranking,
        filtered_wr=wr_matrix,
        n_dir=n_dir_matrix,
        p_blend=p_weights,
        K_used=k_used,
        score_flat=score_df,
        mu=mars_cfg.MU,
        gamma=gamma,
        include_posterior_se=False,
        include_binom_se=True,
        include_counts=True,
        include_self_row=True,
        include_weight_col=False,
        include_mas_contrib_col=False,
        out_dir=report_dir,
        base_name="mars_matchup_report",
        also_versioned=_output_profile(cfg) == OUTPUT_PROFILE_DEBUG,
        keep_legend_image=_output_profile(cfg) == OUTPUT_PROFILE_DEBUG,
    )

    outputs = {"report_latest": latest_path}
    if versioned_path is not None:
        outputs["report"] = versioned_path
    diagnostics = {
        "report_meta": meta,
        "report_k_used": k_used,
        "report_gamma": gamma,
    }
    log.debug("[REPORT] versioned=%s | latest=%s", versioned_path, latest_path)
    return {}, outputs, diagnostics


def run_deck_ranking(
    *,
    base_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    run_scrape: bool = True,
    run_core: bool | None = None,
    run_mars: bool = False,
    run_heatmap: bool = False,
    run_report: bool = False,
    heatmap_top_n: int = 10,
    configure_logs: bool = True,
    show_progress: bool = False,
) -> DeckRankingResult:
    """Run the production ranking pipeline with one acquisition-source boundary.

    ``source.acquisition`` defaults to ``legacy_html``. Tournament API is an
    explicit opt-in and feeds the common downstream core with its dense score
    contract keyed by canonical deck IDs.
    """
    base = Path(base_dir or Path.cwd()).resolve()
    if configure_logs:
        configure_logging()
    cfg, _, _ = _load_config(base, config_path)
    paths = init_paths(base, cfg)
    if configure_logs:
        logging_cfg = cfg.get("logging") or {}
        configure_logging(
            level=logging_cfg.get("level", "INFO"),
            quiet_http=bool(logging_cfg.get("quiet_http", True)),
        )
    if output_dir is not None:
        cfg.setdefault("paths", {})["output_dir"] = str(output_dir)

    acquisition_source = _acquisition_source(cfg)
    preserve_zero_evidence = False
    alias_index_override: dict[str, str] | None = None
    core_input_required = False
    core_input: pd.DataFrame | None = None

    # The only source-specific production dispatch lives in this block.
    if acquisition_source == ACQUISITION_LEGACY_HTML:
        decks_url_cfg = (cfg.get("scraping", {}) or {}).get("decks_url") or LIMITLESS_DECKS_URL
        exp, decks_url, catalog = resolve_expansion_and_url_from_config(
            cfg,
            paths,
            decks_url=decks_url_cfg,
        )
        paths = init_paths(base, cfg, source_url=decks_url)
        result = DeckRankingResult(
            cfg=cfg, paths=paths, expansion=exp, decks_url=decks_url, catalog=catalog
        )

        if run_scrape:
            frames, outputs, diagnostics, exp = _scrape_decklists_and_matchups(
                cfg=cfg,
                paths=paths,
                exp=exp,
                decks_url=decks_url,
                show_progress=show_progress,
            )
            decks_url = build_decks_url_for_expansion(exp, decks_url, cfg=cfg)
            result.expansion = exp
            result.decks_url = decks_url
            result.frames.update(frames)
            result.outputs.update(outputs)
            result.diagnostics.update(diagnostics)
        core_input = result.frames.get("matchup_raw")
    else:
        acquisition_started_at = datetime.now(UTC)
        exp, decks_url, catalog, _ = _api_catalog_context(
            base=base,
            cfg=cfg,
            acquisition_started_at=acquisition_started_at,
        )
        paths = init_paths(base, cfg, source_url=decks_url)
        result = DeckRankingResult(
            cfg=cfg, paths=paths, expansion=exp, decks_url=decks_url, catalog=catalog
        )

        if run_scrape:
            frames, outputs, diagnostics, exp, decks_url, catalog = (
                _run_tournament_api_acquisition_for_production(
                    base=base,
                    cfg=cfg,
                    paths=paths,
                    acquisition_started_at=acquisition_started_at,
                )
            )
            paths = init_paths(base, cfg, source_url=decks_url)
            result.paths = paths
            result.expansion = exp
            result.decks_url = decks_url
            result.catalog = catalog
            result.frames.update(frames)
            result.outputs.update(outputs)
            result.diagnostics.update(diagnostics)

        # Dense contract is the authoritative API input to the common core.
        core_input = result.frames.get("dense_score")
        preserve_zero_evidence = True
        alias_index_override = {}
        core_input_required = True

    result.diagnostics["source_scope"] = list(result.paths.outputs.relative_to(result.paths.output_root).parts)
    result.diagnostics["output_profile"] = _output_profile(cfg)

    should_run_core = run_scrape if run_core is None else run_core
    if should_run_core:
        if core_input_required and core_input is None:
            raise RuntimeError(
                "tournament_api core rebuild requires dense_score from acquisition in the same run"
            )
        frames, outputs, diagnostics = _build_core_matrices(
            cfg=cfg,
            paths=result.paths,
            exp=result.expansion,
            df_matchup_raw=core_input,
            df_top_meta=result.frames.get("top_meta_decklist"),
            preserve_zero_evidence=preserve_zero_evidence,
            alias_index_override=alias_index_override,
        )
        result.frames.update(frames)
        result.outputs.update(outputs)
        result.diagnostics.update(diagnostics)

    if run_mars:
        frames, outputs, diagnostics = _run_mars_stage(
            cfg=cfg,
            paths=result.paths,
            exp=result.expansion,
            score_df=result.frames.get("score_flat"),
            wr_matrix=result.frames.get("wr_matrix"),
            n_dir_matrix=result.frames.get("n_dir_matrix"),
            top_meta_df=result.frames.get("top_meta_decklist"),
            alias_index_override=alias_index_override,
        )
        result.frames.update(frames)
        result.outputs.update(outputs)
        result.diagnostics.update(diagnostics)

    if run_heatmap:
        frames, outputs, diagnostics = _save_heatmap_stage(
            cfg=cfg,
            paths=result.paths,
            exp=result.expansion,
            ranking=result.frames.get("mars_ranking"),
            wr_matrix=result.frames.get("wr_matrix"),
            top_n=heatmap_top_n,
        )
        result.frames.update(frames)
        result.outputs.update(outputs)
        result.diagnostics.update(diagnostics)

    if run_report:
        frames, outputs, diagnostics = _write_report_stage(
            cfg=cfg,
            paths=result.paths,
            exp=result.expansion,
            ranking=result.frames.get("mars_ranking"),
            wr_matrix=result.frames.get("wr_matrix"),
            n_dir_matrix=result.frames.get("n_dir_matrix"),
            score_df=result.frames.get("score_flat"),
            top_meta_df=result.frames.get("top_meta_decklist"),
            mars_diag=result.diagnostics.get("mars_diag"),
        )
        result.frames.update(frames)
        result.outputs.update(outputs)
        result.diagnostics.update(diagnostics)

    if result.frames or result.outputs:
        manifest_path = _write_run_manifest(result)
        result.outputs["run_manifest"] = manifest_path

    for line in result.summary_lines():
        log.info(line)
    return result
