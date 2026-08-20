from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from pathlib import Path

import pandas as pd
from IPython.display import display


DEFAULT_RANKING_COLS: list[str] = [
    "Deck",
    "Score_%",
    "LB_%",
    "MAS_%",
    "BT_%",
    "SE_%",
    "N_eff",
    "Opp_used",
    "Opp_total",
    "Coverage_%",
]

DEFAULT_RANKING_FORMATS: Mapping[str, str] = {
    "Score_%": "{:.2f}",
    "LB_%": "{:.2f}",
    "MAS_%": "{:.2f}",
    "BT_%": "{:.2f}",
    "SE_%": "{:.2f}",
    "Coverage_%": "{:.1f}",
    "N_eff": "{:,.0f}",
    "Opp_used": "{:,.0f}",
    "Opp_total": "{:,.0f}",
}


def _numeric_series(frame: pd.DataFrame, candidates: Sequence[str]) -> pd.Series | None:
    for col in candidates:
        if col in frame.columns:
            series = frame[col]
            if series.dtype == object:
                series = series.astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
            return pd.to_numeric(series, errors="coerce")
    return None


def _quantile_summary(series: pd.Series, *, label: str) -> pd.DataFrame:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    rows: list[dict[str, Any]] = [{"Metric": f"{label}_count", "Value": int(clean.shape[0])}]
    if clean.empty:
        return pd.DataFrame(rows, columns=["Metric", "Value"])

    quantiles = clean.quantile([0.0, 0.25, 0.50, 0.75, 0.90, 1.0])
    rows.extend(
        [
            {"Metric": f"{label}_min", "Value": round(float(quantiles.loc[0.0]), 4)},
            {"Metric": f"{label}_p25", "Value": round(float(quantiles.loc[0.25]), 4)},
            {"Metric": f"{label}_median", "Value": round(float(quantiles.loc[0.50]), 4)},
            {"Metric": f"{label}_p75", "Value": round(float(quantiles.loc[0.75]), 4)},
            {"Metric": f"{label}_p90", "Value": round(float(quantiles.loc[0.90]), 4)},
            {"Metric": f"{label}_max", "Value": round(float(quantiles.loc[1.0]), 4)},
            {"Metric": f"{label}_sum", "Value": round(float(clean.sum()), 4)},
        ]
    )
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def ranking_preview_frame(
    ranking: pd.DataFrame,
    *,
    top_n: int | None = 15,
    cols: Sequence[str] | None = None,
) -> pd.DataFrame:
    if ranking is None or ranking.empty:
        return pd.DataFrame(columns=list(cols or DEFAULT_RANKING_COLS))

    selected_cols = [col for col in list(cols or DEFAULT_RANKING_COLS) if col in ranking.columns]
    k = int(top_n) if (top_n is not None and top_n > 0) else len(ranking)
    k = min(k, len(ranking))
    return ranking.iloc[:k, :].loc[:, selected_cols].copy()


def share_distribution_frame(top_meta: pd.DataFrame, *, top_n: int = 10) -> pd.DataFrame:
    cols = ["Rank", "Deck", "Share_%", "Share_cum_%", "Count"]
    if top_meta is None or top_meta.empty or "Deck" not in top_meta.columns:
        return pd.DataFrame(columns=cols)

    df = top_meta.copy()
    share = _numeric_series(df, ["Share_%", "share", "Share", "Share_frac"])
    if share is None:
        share = pd.Series([0.0] * len(df), index=df.index)
    if "Share_frac" in df.columns and not any(col in df.columns for col in ["Share_%", "share", "Share"]):
        share = share * 100.0

    count = _numeric_series(df, ["Count", "count", "Players", "players"])
    rank = _numeric_series(df, ["Rank", "rank"])
    if rank is None:
        rank = pd.Series(range(1, len(df) + 1), index=df.index)

    out = pd.DataFrame(
        {
            "Rank": rank.fillna(pd.Series(range(1, len(df) + 1), index=df.index)).astype(int),
            "Deck": df["Deck"].astype(str),
            "Share_%": share.fillna(0.0).astype(float),
            "Count": count if count is not None else pd.NA,
        }
    )
    out = out.sort_values(["Rank", "Share_%", "Deck"], ascending=[True, False, True], kind="mergesort")
    out["Share_cum_%"] = out["Share_%"].cumsum().round(4)
    out["Share_%"] = out["Share_%"].round(4)
    if "Count" in out.columns:
        out["Count"] = pd.to_numeric(out["Count"], errors="coerce").astype("Int64")

    k = max(1, int(top_n))
    return out.loc[:, cols].head(k).reset_index(drop=True)


def meta_diagnostics_summary_frame(
    *,
    top_meta: pd.DataFrame | None = None,
    nan_diag: pd.DataFrame | None = None,
    wildcard_candidates: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    if top_meta is not None and not top_meta.empty:
        share = _numeric_series(top_meta, ["Share_%", "share", "Share", "Share_frac"])
        if share is not None:
            if "Share_frac" in top_meta.columns and not any(col in top_meta.columns for col in ["Share_%", "share", "Share"]):
                share = share * 100.0
            share = share.dropna()
            rows.extend(
                [
                    {"Metric": "candidate_decks", "Value": int(len(top_meta))},
                    {"Metric": "candidate_share_%", "Value": round(float(share.sum()), 4)},
                    {"Metric": "top1_share_%", "Value": round(float(share.iloc[0]), 4) if not share.empty else 0.0},
                    {"Metric": "top5_share_%", "Value": round(float(share.head(5).sum()), 4)},
                    {"Metric": "median_share_%", "Value": round(float(share.median()), 4) if not share.empty else 0.0},
                ]
            )

    if nan_diag is not None and not nan_diag.empty:
        nan_count = _numeric_series(nan_diag, ["nan_count"])
        nan_ratio = _numeric_series(nan_diag, ["nan_ratio"])
        coverage = _numeric_series(nan_diag, ["coverage_%"])
        total_matches = _numeric_series(nan_diag, ["total_matches"])
        nan_count_values = nan_count.fillna(0) if nan_count is not None else pd.Series([0.0] * len(nan_diag), index=nan_diag.index)
        nan_ratio_values = nan_ratio.fillna(0) if nan_ratio is not None else pd.Series([0.0] * len(nan_diag), index=nan_diag.index)
        rows.extend(
            [
                {"Metric": "diagnostic_decks", "Value": int(len(nan_diag))},
                {
                    "Metric": "critical_nan_decks",
                    "Value": int(((nan_count_values > 0) | (nan_ratio_values > 0)).sum()),
                },
            ]
        )
        if coverage is not None and not coverage.dropna().empty:
            rows.extend(
                [
                    {"Metric": "min_coverage_%", "Value": round(float(coverage.min()), 4)},
                    {"Metric": "median_coverage_%", "Value": round(float(coverage.median()), 4)},
                ]
            )
        if total_matches is not None and not total_matches.dropna().empty:
            rows.extend(
                [
                    {"Metric": "min_total_matches", "Value": round(float(total_matches.min()), 0)},
                    {"Metric": "median_total_matches", "Value": round(float(total_matches.median()), 0)},
                    {"Metric": "max_total_matches", "Value": round(float(total_matches.max()), 0)},
                ]
            )

    if wildcard_candidates is not None:
        rows.append({"Metric": "wildcard_candidates", "Value": int(len(wildcard_candidates))})

    return pd.DataFrame(rows, columns=["Metric", "Value"])


def run_overview_frame(
    *,
    diagnostics: Mapping[str, Any] | None = None,
    frames: Mapping[str, pd.DataFrame] | None = None,
    outputs: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    diagnostics = diagnostics or {}
    frames = frames or {}
    outputs = outputs or {}

    nan_filter = diagnostics.get("nan_filter") if isinstance(diagnostics.get("nan_filter"), Mapping) else {}
    wildcard_summary = (
        diagnostics.get("wildcard_candidates")
        if isinstance(diagnostics.get("wildcard_candidates"), Mapping)
        else {}
    )
    timing = (
        diagnostics.get("matchup_scrape_timing")
        if isinstance(diagnostics.get("matchup_scrape_timing"), Mapping)
        else {}
    )

    def frame_rows(name: str) -> int:
        frame = frames.get(name)
        return int(len(frame)) if isinstance(frame, pd.DataFrame) else 0

    rows = [
        {"Area": "profile", "Metric": "output_profile", "Value": diagnostics.get("output_profile", "debug")},
        {"Area": "scope", "Metric": "full_decklist_rows", "Value": frame_rows("decklist_raw") or diagnostics.get("decklist_rows", 0)},
        {"Area": "scope", "Metric": "matchup_fetch_decks", "Value": frame_rows("top_meta_decklist") or diagnostics.get("top_meta_rows", 0)},
        {"Area": "scope", "Metric": "mars_core_decks", "Value": frame_rows("mars_ranking") or diagnostics.get("mars_rows", 0)},
        {"Area": "nan_filter", "Metric": "axis_all", "Value": diagnostics.get("axis_all_count", 0)},
        {"Area": "nan_filter", "Metric": "axis_candidate_pool", "Value": diagnostics.get("axis0_count", 0)},
        {"Area": "nan_filter", "Metric": "axis_kept", "Value": diagnostics.get("axis_kept_count", 0)},
        {"Area": "nan_filter", "Metric": "dropped_count", "Value": nan_filter.get("dropped_count", 0)},
        {"Area": "wildcard", "Metric": "full_scrape", "Value": diagnostics.get("wildcard_full_scrape", False)},
        {"Area": "wildcard", "Metric": "candidate_rows", "Value": wildcard_summary.get("rows", frame_rows("wildcard_candidates"))},
        {"Area": "scrape", "Metric": "cache_hits", "Value": timing.get("cache_hits", diagnostics.get("matchup_cache_hits", 0))},
        {"Area": "scrape", "Metric": "cache_misses", "Value": timing.get("cache_misses", 0)},
        {"Area": "outputs", "Metric": "saved_outputs", "Value": int(len(outputs))},
        {"Area": "outputs", "Metric": "manifest_written", "Value": "run_manifest" in outputs},
    ]
    return pd.DataFrame(rows, columns=["Area", "Metric", "Value"])


def scrape_timing_frame(diagnostics: Mapping[str, Any] | None = None) -> pd.DataFrame:
    diagnostics = diagnostics or {}
    timing = (
        diagnostics.get("matchup_scrape_timing")
        if isinstance(diagnostics.get("matchup_scrape_timing"), Mapping)
        else {}
    )
    if not timing and "estimated_polite_delay_seconds" not in diagnostics:
        return pd.DataFrame(columns=["Metric", "Value", "Unit"])

    rows = [
        {"Metric": "unique_pages", "Value": timing.get("unique_pages", diagnostics.get("matchup_pages", 0)), "Unit": "pages"},
        {"Metric": "cache_hits", "Value": timing.get("cache_hits", diagnostics.get("matchup_cache_hits", 0)), "Unit": "pages"},
        {"Metric": "cache_misses", "Value": timing.get("cache_misses", 0), "Unit": "pages"},
    ]
    for metric in ["elapsed_seconds", "delay_seconds_total", "avg_seconds_per_page"]:
        if metric in timing:
            rows.append({"Metric": metric, "Value": round(float(timing[metric]), 4), "Unit": "seconds"})
    if "elapsed_seconds" in timing:
        rows.append({"Metric": "elapsed_minutes", "Value": round(float(timing["elapsed_seconds"]) / 60.0, 4), "Unit": "minutes"})
    if "estimated_polite_delay_seconds" in diagnostics:
        delay = float(diagnostics.get("estimated_polite_delay_seconds") or 0.0)
        rows.extend(
            [
                {"Metric": "estimated_polite_delay_seconds", "Value": round(delay, 4), "Unit": "seconds"},
                {"Metric": "estimated_polite_delay_minutes", "Value": round(delay / 60.0, 4), "Unit": "minutes"},
            ]
        )
    return pd.DataFrame(rows, columns=["Metric", "Value", "Unit"])


def analysis_scope_summary_frame(
    *,
    decklist_raw: pd.DataFrame | None = None,
    top_meta: pd.DataFrame | None = None,
    mars_ranking: pd.DataFrame | None = None,
    score_flat: pd.DataFrame | None = None,
    matchup_raw: pd.DataFrame | None = None,
    wildcard_candidates: pd.DataFrame | None = None,
    candidate_share_pct: float | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    raw = decklist_raw.copy() if decklist_raw is not None else pd.DataFrame()

    rows.append({"Metric": "full_decklist_rows", "Value": int(len(raw))})
    rows.append({"Metric": "matchup_fetch_rows", "Value": int(len(top_meta)) if top_meta is not None else 0})
    rows.append({"Metric": "mars_core_rows", "Value": int(len(mars_ranking)) if mars_ranking is not None else 0})
    rows.append({"Metric": "matchup_score_rows", "Value": int(len(score_flat)) if score_flat is not None else 0})
    rows.append({"Metric": "matchup_raw_rows", "Value": int(len(matchup_raw)) if matchup_raw is not None else 0})
    rows.append({"Metric": "wildcard_rows", "Value": int(len(wildcard_candidates)) if wildcard_candidates is not None else 0})

    if candidate_share_pct is not None:
        rows.append({"Metric": "configured_candidate_pool_share_%", "Value": float(candidate_share_pct)})
        raw_share = _numeric_series(raw, ["Share_%", "share", "Share", "Share_frac"]) if not raw.empty else None
        if raw_share is not None and "Share_frac" in raw.columns and not any(col in raw.columns for col in ["Share_%", "share", "Share"]):
            raw_share = raw_share * 100.0
        if raw_share is not None and not raw.empty and float(candidate_share_pct) < 100.0:
            derived = raw.copy()
            derived["_candidate_share_%"] = raw_share.fillna(0.0).astype(float)
            derived = derived.sort_values("_candidate_share_%", ascending=False, kind="mergesort").reset_index(drop=True)
            derived["_candidate_share_cum_%"] = derived["_candidate_share_%"].cumsum()
            if (derived["_candidate_share_cum_%"] >= float(candidate_share_pct)).any():
                pos = int((derived["_candidate_share_cum_%"] >= float(candidate_share_pct)).idxmax())
            else:
                pos = len(derived) - 1
            candidate_core = derived.iloc[: pos + 1]
            rows.extend(
                [
                    {"Metric": "configured_candidate_pool_rows", "Value": int(len(candidate_core))},
                    {
                        "Metric": "configured_candidate_pool_share_actual_%",
                        "Value": round(float(candidate_core["_candidate_share_%"].sum()), 4),
                    },
                ]
            )

    return pd.DataFrame(rows, columns=["Metric", "Value"])


def candidate_vs_full_summary_frame(
    *,
    decklist_raw: pd.DataFrame | None,
    top_meta: pd.DataFrame | None,
    candidate_share_pct: float | None = None,
    request_delay_sec: float | None = None,
    cache_miss_count: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    raw = decklist_raw.copy() if decklist_raw is not None else pd.DataFrame()
    candidate = top_meta.copy() if top_meta is not None else pd.DataFrame()

    raw_share = _numeric_series(raw, ["Share_%", "share", "Share", "Share_frac"]) if not raw.empty else None
    if raw_share is not None and "Share_frac" in raw.columns and not any(col in raw.columns for col in ["Share_%", "share", "Share"]):
        raw_share = raw_share * 100.0

    if (
        candidate_share_pct is not None
        and raw_share is not None
        and not raw.empty
        and (candidate.empty or len(candidate) == len(raw))
        and float(candidate_share_pct) < 100.0
    ):
        derived = raw.copy()
        derived["_candidate_share_%"] = raw_share.fillna(0.0).astype(float)
        derived = derived.sort_values("_candidate_share_%", ascending=False, kind="mergesort").reset_index(drop=True)
        derived["_candidate_share_cum_%"] = derived["_candidate_share_%"].cumsum()
        if (derived["_candidate_share_cum_%"] >= float(candidate_share_pct)).any():
            pos = int((derived["_candidate_share_cum_%"] >= float(candidate_share_pct)).idxmax())
        else:
            pos = len(derived) - 1
        candidate = derived.iloc[: pos + 1].drop(columns=["_candidate_share_%", "_candidate_share_cum_%"]).copy()

    candidate_share = _numeric_series(candidate, ["Share_%", "share", "Share", "Share_frac"]) if not candidate.empty else None
    if candidate_share is not None and "Share_frac" in candidate.columns and not any(col in candidate.columns for col in ["Share_%", "share", "Share"]):
        candidate_share = candidate_share * 100.0

    raw_decks = int(len(raw))
    candidate_decks = int(len(candidate))
    excluded_decks = max(0, raw_decks - candidate_decks)
    raw_share_sum = round(float(raw_share.sum()), 4) if raw_share is not None else 0.0
    candidate_share_sum = round(float(candidate_share.sum()), 4) if candidate_share is not None else 0.0
    excluded_share = round(max(0.0, raw_share_sum - candidate_share_sum), 4)

    rows.extend(
        [
            {"Metric": "full_decklist_rows", "Value": raw_decks},
            {"Metric": "candidate_rows", "Value": candidate_decks},
            {"Metric": "excluded_rows", "Value": excluded_decks},
            {"Metric": "full_share_%", "Value": raw_share_sum},
            {"Metric": "candidate_share_%", "Value": candidate_share_sum},
            {"Metric": "excluded_share_%", "Value": excluded_share},
        ]
    )

    if raw_decks > 0:
        rows.append({"Metric": "candidate_row_ratio_%", "Value": round(100.0 * candidate_decks / raw_decks, 4)})
        rows.append({"Metric": "excluded_row_ratio_%", "Value": round(100.0 * excluded_decks / raw_decks, 4)})

    if "Deck" in raw.columns and "Deck" in candidate.columns and raw_share is not None:
        candidate_deck_set = set(candidate["Deck"].astype(str).str.strip())
        excluded = raw.copy()
        excluded["_share_%"] = raw_share.fillna(0.0).astype(float)
        excluded = excluded[~excluded["Deck"].astype(str).str.strip().isin(candidate_deck_set)].copy()
        excluded = excluded.sort_values("_share_%", ascending=False, kind="mergesort")
        if not excluded.empty:
            rows.extend(
                [
                    {"Metric": "excluded_top_deck", "Value": str(excluded.iloc[0]["Deck"])},
                    {"Metric": "excluded_top_share_%", "Value": round(float(excluded.iloc[0]["_share_%"]), 4)},
                    {"Metric": "excluded_top5_share_%", "Value": round(float(excluded["_share_%"].head(5).sum()), 4)},
                ]
            )

    if request_delay_sec is not None:
        misses = excluded_decks if cache_miss_count is None else int(cache_miss_count)
        rows.append({"Metric": "estimated_extra_pages", "Value": int(max(0, misses))})
        rows.append({"Metric": "estimated_extra_delay_seconds", "Value": round(float(request_delay_sec) * max(0, misses), 2)})
        rows.append({"Metric": "estimated_extra_delay_minutes", "Value": round(float(request_delay_sec) * max(0, misses) / 60.0, 2)})

    return pd.DataFrame(rows, columns=["Metric", "Value"])


def evidence_core_eligibility_frame(
    *,
    decklist_raw: pd.DataFrame | None,
    nan_diag: pd.DataFrame | None,
    min_coverage_pct: float = 60.0,
    min_total_matches: float = 50.0,
    max_nan_ratio: float | None = None,
    top_n: int | None = None,
) -> pd.DataFrame:
    cols = [
        "eligible",
        "Deck",
        "Share_%",
        "coverage_%",
        "nan_ratio",
        "total_matches",
        "observed_opponents",
        "total_opponents",
    ]
    if nan_diag is None or nan_diag.empty or "Deck" not in nan_diag.columns:
        return pd.DataFrame(columns=cols)

    out = nan_diag.copy()
    out["Deck"] = out["Deck"].astype(str).str.strip()
    for col in ["coverage_%", "nan_ratio", "total_matches", "observed_opponents", "total_opponents"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "Share_%" not in out.columns:
        out["Share_%"] = 0.0
    out["Share_%"] = pd.to_numeric(out["Share_%"], errors="coerce").fillna(0.0)

    if decklist_raw is not None and not decklist_raw.empty and "Deck" in decklist_raw.columns:
        raw = decklist_raw.copy()
        raw["Deck"] = raw["Deck"].astype(str).str.strip()
        raw_share = _numeric_series(raw, ["Share_%", "share", "Share", "Share_frac"])
        if raw_share is not None:
            if "Share_frac" in raw.columns and not any(col in raw.columns for col in ["Share_%", "share", "Share"]):
                raw_share = raw_share * 100.0
            share_lookup = dict(zip(raw["Deck"], raw_share.fillna(0.0).astype(float)))
            out["Share_%"] = out["Deck"].map(share_lookup).fillna(out["Share_%"]).astype(float)

    eligible = (
        (out.get("coverage_%", pd.Series([0.0] * len(out), index=out.index)).fillna(0.0) >= float(min_coverage_pct))
        & (out.get("total_matches", pd.Series([0.0] * len(out), index=out.index)).fillna(0.0) >= float(min_total_matches))
    )
    if max_nan_ratio is not None and "nan_ratio" in out.columns:
        eligible &= out["nan_ratio"].fillna(1.0) <= float(max_nan_ratio)
    out["eligible"] = eligible

    out = out.sort_values(
        ["eligible", "coverage_%", "total_matches", "Share_%", "Deck"],
        ascending=[False, False, False, False, True],
        kind="mergesort",
    )
    out_cols = [col for col in cols if col in out.columns]
    out = out.loc[:, out_cols].reset_index(drop=True)
    if top_n is not None:
        out = out.head(max(1, int(top_n)))
    return out


def evidence_core_comparison_frame(
    *,
    decklist_raw: pd.DataFrame | None,
    nan_diag: pd.DataFrame | None,
    share_core_pct: float = 80.0,
    min_coverage_pct: float = 60.0,
    min_total_matches: float = 50.0,
    max_nan_ratio: float | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if decklist_raw is None or decklist_raw.empty or "Deck" not in decklist_raw.columns:
        return pd.DataFrame(columns=["Metric", "Value"])

    raw = decklist_raw.copy()
    raw["Deck"] = raw["Deck"].astype(str).str.strip()
    raw_share = _numeric_series(raw, ["Share_%", "share", "Share", "Share_frac"])
    if raw_share is None:
        raw_share = pd.Series([0.0] * len(raw), index=raw.index)
    if "Share_frac" in raw.columns and not any(col in raw.columns for col in ["Share_%", "share", "Share"]):
        raw_share = raw_share * 100.0
    raw["_share_%"] = raw_share.fillna(0.0).astype(float)
    raw = raw.sort_values("_share_%", ascending=False, kind="mergesort").reset_index(drop=True)
    raw["_share_cum_%"] = raw["_share_%"].cumsum()
    if (raw["_share_cum_%"] >= float(share_core_pct)).any():
        pos = int((raw["_share_cum_%"] >= float(share_core_pct)).idxmax())
    else:
        pos = len(raw) - 1
    share_core = raw.iloc[: pos + 1].copy()
    share_core_set = set(share_core["Deck"])

    evidence = evidence_core_eligibility_frame(
        decklist_raw=decklist_raw,
        nan_diag=nan_diag,
        min_coverage_pct=min_coverage_pct,
        min_total_matches=min_total_matches,
        max_nan_ratio=max_nan_ratio,
        top_n=None,
    )
    evidence_core = evidence[evidence["eligible"]].copy() if not evidence.empty else pd.DataFrame()
    evidence_core_set = set(evidence_core["Deck"].astype(str)) if "Deck" in evidence_core.columns else set()

    overlap = share_core_set & evidence_core_set
    evidence_only = evidence_core_set - share_core_set
    share_only = share_core_set - evidence_core_set
    share_lookup = dict(zip(raw["Deck"], raw["_share_%"]))

    rows.extend(
        [
            {"Metric": "share_core_pct", "Value": float(share_core_pct)},
            {"Metric": "share_core_decks", "Value": int(len(share_core_set))},
            {"Metric": "share_core_share_%", "Value": round(float(share_core["_share_%"].sum()), 4)},
            {"Metric": "evidence_min_coverage_%", "Value": float(min_coverage_pct)},
            {"Metric": "evidence_min_total_matches", "Value": float(min_total_matches)},
            {"Metric": "evidence_core_decks", "Value": int(len(evidence_core_set))},
            {"Metric": "evidence_core_share_%", "Value": round(float(sum(share_lookup.get(deck, 0.0) for deck in evidence_core_set)), 4)},
            {"Metric": "overlap_decks", "Value": int(len(overlap))},
            {"Metric": "evidence_only_decks", "Value": int(len(evidence_only))},
            {"Metric": "evidence_only_share_%", "Value": round(float(sum(share_lookup.get(deck, 0.0) for deck in evidence_only)), 4)},
            {"Metric": "share_only_decks", "Value": int(len(share_only))},
            {"Metric": "share_only_share_%", "Value": round(float(sum(share_lookup.get(deck, 0.0) for deck in share_only)), 4)},
        ]
    )
    if max_nan_ratio is not None:
        rows.append({"Metric": "evidence_max_nan_ratio", "Value": float(max_nan_ratio)})
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def evidence_core_iterative_frame(
    *,
    decklist_raw: pd.DataFrame | None,
    n_dir_matrix: pd.DataFrame | None = None,
    matchup_raw: pd.DataFrame | None = None,
    share_core_pct: float = 80.0,
    min_coverage_vs_axis_pct: float = 60.0,
    min_n_vs_axis: float = 50.0,
    max_additions: int | None = None,
    top_n: int | None = None,
) -> pd.DataFrame:
    cols = [
        "selected",
        "anchor_core",
        "added_by_evidence",
        "iteration_added",
        "Deck",
        "Share_%",
        "coverage_at_add_%",
        "N_at_add",
        "coverage_vs_final_axis_%",
        "N_vs_final_axis",
        "observed_final_axis_opponents",
        "final_axis_opponents",
    ]
    if decklist_raw is None or decklist_raw.empty or "Deck" not in decklist_raw.columns:
        return pd.DataFrame(columns=cols)
    if (n_dir_matrix is None or n_dir_matrix.empty) and (matchup_raw is None or matchup_raw.empty):
        return pd.DataFrame(columns=cols)

    raw = decklist_raw.copy()
    raw["Deck"] = raw["Deck"].astype(str).str.strip()
    share = _numeric_series(raw, ["Share_%", "share", "Share", "Share_frac"])
    if share is None:
        share = pd.Series([0.0] * len(raw), index=raw.index)
    if "Share_frac" in raw.columns and not any(col in raw.columns for col in ["Share_%", "share", "Share"]):
        share = share * 100.0
    raw["_share_%"] = share.fillna(0.0).astype(float)
    raw = raw.sort_values("_share_%", ascending=False, kind="mergesort").reset_index(drop=True)
    raw["_share_cum_%"] = raw["_share_%"].cumsum()

    if matchup_raw is not None and not matchup_raw.empty:
        matchup = matchup_raw.copy()
        required = {"Deck A", "Deck B"}
        if not required.issubset(matchup.columns):
            return pd.DataFrame(columns=cols)
        matchup["Deck A"] = matchup["Deck A"].astype(str).str.strip()
        matchup["Deck B"] = matchup["Deck B"].astype(str).str.strip()
        if "N" in matchup.columns:
            n_values = pd.to_numeric(matchup["N"], errors="coerce").fillna(0.0)
        elif {"W", "L"}.issubset(matchup.columns):
            n_values = pd.to_numeric(matchup["W"], errors="coerce").fillna(0.0) + pd.to_numeric(
                matchup["L"], errors="coerce"
            ).fillna(0.0)
        else:
            return pd.DataFrame(columns=cols)
        matchup["_N"] = pd.Series(n_values.to_numpy(dtype="float64"), index=matchup.index)
        n_mat = matchup.groupby(["Deck A", "Deck B"], sort=False)["_N"].sum().unstack(fill_value=0.0)
        axis_names = sorted(set(n_mat.index.astype(str)) | set(n_mat.columns.astype(str)))
        n_mat = n_mat.reindex(index=axis_names, columns=axis_names, fill_value=0.0)
    else:
        n_mat = n_dir_matrix.copy()
    n_mat.index = [str(x).strip() for x in n_mat.index.tolist()]
    n_mat.columns = [str(x).strip() for x in n_mat.columns.tolist()]
    n_mat = n_mat.apply(pd.to_numeric, errors="coerce").fillna(0.0)

    available = [deck for deck in raw["Deck"].tolist() if deck in n_mat.index and deck in n_mat.columns]
    if not available:
        return pd.DataFrame(columns=cols)

    if (raw["_share_cum_%"] >= float(share_core_pct)).any():
        pos = int((raw["_share_cum_%"] >= float(share_core_pct)).idxmax())
    else:
        pos = len(raw) - 1
    anchors = [deck for deck in raw.iloc[: pos + 1]["Deck"].tolist() if deck in available]
    selected: list[str] = list(dict.fromkeys(anchors))
    selected_set = set(selected)
    anchor_set = set(anchors)
    share_lookup = dict(zip(raw["Deck"], raw["_share_%"]))
    added_info: dict[str, dict[str, Any]] = {}

    def stats_vs_axis(deck: str, axis: Sequence[str]) -> tuple[float, int, float, int]:
        opponents = [op for op in axis if op != deck and op in n_mat.columns]
        if not opponents or deck not in n_mat.index:
            return 0.0, 0, 0.0, 0
        values = n_mat.loc[deck, opponents]
        observed = int((values > 0).sum())
        n_total = float(values.sum())
        coverage = 100.0 * observed / len(opponents)
        return coverage, observed, n_total, len(opponents)

    iteration = 0
    while True:
        if max_additions is not None and len(added_info) >= int(max_additions):
            break
        outside = [deck for deck in available if deck not in selected_set]
        candidates: list[dict[str, Any]] = []
        for deck in outside:
            coverage, observed, n_total, opponents = stats_vs_axis(deck, selected)
            if coverage >= float(min_coverage_vs_axis_pct) and n_total >= float(min_n_vs_axis):
                candidates.append(
                    {
                        "Deck": deck,
                        "coverage_at_add_%": round(float(coverage), 4),
                        "observed_at_add": observed,
                        "N_at_add": round(float(n_total), 0),
                        "axis_opponents_at_add": opponents,
                        "Share_%": float(share_lookup.get(deck, 0.0)),
                    }
                )
        if not candidates:
            break

        candidates_df = pd.DataFrame(candidates).sort_values(
            ["coverage_at_add_%", "N_at_add", "Share_%", "Deck"],
            ascending=[False, False, False, True],
            kind="mergesort",
        )
        pick = candidates_df.iloc[0].to_dict()
        iteration += 1
        deck = str(pick["Deck"])
        selected.append(deck)
        selected_set.add(deck)
        added_info[deck] = {
            "iteration_added": iteration,
            "coverage_at_add_%": pick["coverage_at_add_%"],
            "N_at_add": pick["N_at_add"],
        }

    rows: list[dict[str, Any]] = []
    for deck in available:
        final_coverage, final_observed, final_n, final_opponents = stats_vs_axis(deck, selected)
        is_anchor = deck in anchor_set
        is_added = deck in added_info
        info = added_info.get(deck, {})
        rows.append(
            {
                "selected": deck in selected_set,
                "anchor_core": is_anchor,
                "added_by_evidence": is_added,
                "iteration_added": info.get("iteration_added", 0 if is_anchor else pd.NA),
                "Deck": deck,
                "Share_%": round(float(share_lookup.get(deck, 0.0)), 4),
                "coverage_at_add_%": 100.0 if is_anchor else info.get("coverage_at_add_%", pd.NA),
                "N_at_add": pd.NA if is_anchor else info.get("N_at_add", pd.NA),
                "coverage_vs_final_axis_%": round(float(final_coverage), 4),
                "N_vs_final_axis": round(float(final_n), 0),
                "observed_final_axis_opponents": final_observed,
                "final_axis_opponents": final_opponents,
            }
        )

    out = pd.DataFrame(rows, columns=cols)
    out = out.sort_values(
        ["selected", "anchor_core", "added_by_evidence", "iteration_added", "Share_%", "Deck"],
        ascending=[False, False, False, True, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    if top_n is not None:
        out = out.head(max(1, int(top_n)))
    return out


def evidence_core_iterative_summary_frame(iterative: pd.DataFrame) -> pd.DataFrame:
    if iterative is None or iterative.empty:
        return pd.DataFrame(columns=["Metric", "Value"])

    selected = iterative[iterative["selected"]].copy()
    anchors = iterative[iterative["anchor_core"]].copy()
    added = iterative[iterative["added_by_evidence"]].copy()
    rows = [
        {"Metric": "iterative_selected_decks", "Value": int(len(selected))},
        {"Metric": "iterative_anchor_decks", "Value": int(len(anchors))},
        {"Metric": "iterative_added_decks", "Value": int(len(added))},
        {"Metric": "iterative_selected_share_%", "Value": round(float(pd.to_numeric(selected["Share_%"], errors="coerce").fillna(0.0).sum()), 4)},
        {"Metric": "iterative_anchor_share_%", "Value": round(float(pd.to_numeric(anchors["Share_%"], errors="coerce").fillna(0.0).sum()), 4)},
        {"Metric": "iterative_added_share_%", "Value": round(float(pd.to_numeric(added["Share_%"], errors="coerce").fillna(0.0).sum()), 4)},
    ]
    if not added.empty:
        rows.extend(
            [
                {"Metric": "iterative_added_min_coverage_at_add_%", "Value": round(float(pd.to_numeric(added["coverage_at_add_%"], errors="coerce").min()), 4)},
                {"Metric": "iterative_added_median_coverage_at_add_%", "Value": round(float(pd.to_numeric(added["coverage_at_add_%"], errors="coerce").median()), 4)},
                {"Metric": "iterative_added_min_final_coverage_%", "Value": round(float(pd.to_numeric(added["coverage_vs_final_axis_%"], errors="coerce").min()), 4)},
            ]
        )
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def coverage_volume_summary_frame(nan_diag: pd.DataFrame) -> pd.DataFrame:
    if nan_diag is None or nan_diag.empty:
        return pd.DataFrame(columns=["Metric", "Value"])

    parts: list[pd.DataFrame] = []
    for label, candidates in [
        ("coverage_%", ["coverage_%"]),
        ("nan_ratio", ["nan_ratio"]),
        ("total_matches", ["total_matches"]),
        ("observed_opponents", ["observed_opponents"]),
    ]:
        series = _numeric_series(nan_diag, candidates)
        if series is not None:
            parts.append(_quantile_summary(series, label=label))
    if not parts:
        return pd.DataFrame(columns=["Metric", "Value"])
    return pd.concat(parts, ignore_index=True)


def style_ranking_preview(
    ranking: pd.DataFrame,
    *,
    top_n: int | None = 15,
    cols: Sequence[str] | None = None,
    fmt: Mapping[str, str] | None = None,
    title: str | None = None,
) -> Any:
    out = ranking_preview_frame(ranking, top_n=top_n, cols=cols)
    total = 0 if ranking is None else len(ranking)
    k = len(out)

    formats = dict(DEFAULT_RANKING_FORMATS)
    if fmt:
        formats.update(fmt)

    caption = title or (f"Top {k} / {total}" if k < total else f"Full ranking ({total})")
    return out.style.format({col: formats[col] for col in out.columns if col in formats}).set_caption(caption)


def show_ranking(
    ranking: pd.DataFrame,
    top_n: int | None = 15,
    cols: Sequence[str] | None = None,
    fmt: Mapping[str, str] | None = None,
    title: str | None = None,
    *,
    show: bool = True,
    return_df: bool = False,
    return_styler: bool = False,
):
    if return_df and return_styler:
        raise ValueError("Choose either return_df=True or return_styler=True, not both.")

    if return_df:
        return ranking_preview_frame(ranking, top_n=top_n, cols=cols)

    styler = style_ranking_preview(ranking, top_n=top_n, cols=cols, fmt=fmt, title=title)
    if show:
        display(styler)
        return None
    if return_styler:
        return styler
    return None


def output_paths_frame(outputs: Mapping[str, Any], *, base_dir: str | Path | None = None) -> pd.DataFrame:
    rows = []
    base = Path(base_dir).resolve() if base_dir is not None else None
    for key, path in sorted((outputs or {}).items()):
        path_obj = Path(path)
        display_path = path_obj
        if base is not None:
            try:
                display_path = path_obj.resolve().relative_to(base)
            except Exception:
                display_path = path_obj
        rows.append({"Output": key, "Path": str(display_path)})
    return pd.DataFrame(rows, columns=["Output", "Path"])


USER_OUTPUT_KEYS = {
    "report_latest": ("user", "report"),
    "mars_ranking": ("user", "ranking"),
    "heatmap_topN_latest": ("user", "visual"),
    "wildcard_candidates": ("user", "diagnostic"),
    "run_manifest": ("user", "manifest"),
}

REPRODUCIBLE_OUTPUT_KEYS = {
    "decklist_raw": ("reproducible", "rebuild"),
    "top_meta_decklist": ("reproducible", "rebuild"),
    "matchup_raw": ("reproducible", "rebuild"),
}

DEBUG_OUTPUT_KEYS = {
    "score_flat": ("debug", "intermediate"),
    "wr_matrix": ("debug", "matrix"),
    "n_dir_matrix": ("debug", "matrix"),
    "nan_diagnostics_pre_filter": ("debug", "diagnostic"),
    "nan_filter_simulation": ("debug", "diagnostic"),
    "heatmap_topN": ("debug", "timestamped"),
    "report": ("debug", "timestamped"),
}


def _output_kind(key: str) -> tuple[str, str]:
    if key in USER_OUTPUT_KEYS:
        return USER_OUTPUT_KEYS[key]
    if key in REPRODUCIBLE_OUTPUT_KEYS:
        return REPRODUCIBLE_OUTPUT_KEYS[key]
    if key in DEBUG_OUTPUT_KEYS:
        return DEBUG_OUTPUT_KEYS[key]
    return "unknown", "other"


def saved_outputs_frame(outputs: Mapping[str, Any], *, base_dir: str | Path | None = None) -> pd.DataFrame:
    """
    Show saved artifacts with their intended audience.

    This is notebook-facing: it keeps the normal user view focused on final
    artifacts while still making debug/rebuild files visible when a richer
    output profile is selected.
    """
    base = Path(base_dir).resolve() if base_dir is not None else None
    rows = []
    for key, path in sorted((outputs or {}).items()):
        tier, kind = _output_kind(key)
        path_obj = Path(path)
        display_path = path_obj
        if base is not None:
            try:
                display_path = path_obj.resolve().relative_to(base)
            except Exception:
                display_path = path_obj
        rows.append(
            {
                "Output": key,
                "Tier": tier,
                "Kind": kind,
                "Path": str(display_path),
            }
        )
    return pd.DataFrame(rows, columns=["Output", "Tier", "Kind", "Path"])


def diagnostics_preview_frame(diagnostics: Mapping[str, Any]) -> pd.DataFrame:
    rows = []
    for key, value in sorted((diagnostics or {}).items()):
        if isinstance(value, dict):
            preview = f"<dict: {len(value)} keys>"
        elif isinstance(value, list):
            preview = f"<list: {len(value)} items>"
        elif isinstance(value, tuple):
            preview = f"<tuple: {len(value)} items>"
        else:
            preview = value
        rows.append({"Diagnostic": key, "Value": preview})
    return pd.DataFrame(rows, columns=["Diagnostic", "Value"])


def nan_diagnostics_critical_frame(nan_diag: pd.DataFrame, *, top_n: int = 15) -> pd.DataFrame:
    cols = [
        "Deck",
        "Share_%",
        "observed_opponents",
        "total_opponents",
        "coverage_%",
        "nan_count",
        "nan_ratio",
        "total_matches",
    ]
    if nan_diag is None or nan_diag.empty:
        return pd.DataFrame(columns=cols)

    df = nan_diag.copy()
    for col in ["Share_%", "coverage_%", "nan_count", "nan_ratio", "total_matches"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    critical = df[(df.get("nan_count", 0).fillna(0) > 0) | (df.get("nan_ratio", 0).fillna(0) > 0)].copy()
    if critical.empty:
        return pd.DataFrame(columns=[col for col in cols if col in df.columns])

    sort_cols = [col for col in ["nan_ratio", "nan_count", "coverage_%", "total_matches", "Share_%", "Deck"] if col in critical.columns]
    ascending = {
        "nan_ratio": False,
        "nan_count": False,
        "coverage_%": True,
        "total_matches": True,
        "Share_%": False,
        "Deck": True,
    }
    critical = critical.sort_values(
        sort_cols,
        ascending=[ascending[col] for col in sort_cols],
        kind="mergesort",
    )

    out_cols = [col for col in cols if col in critical.columns]
    k = max(1, int(top_n))
    return critical.loc[:, out_cols].head(k).reset_index(drop=True)


def wildcard_review_frame(
    wildcard_candidates: pd.DataFrame,
    *,
    core_wr_baseline: float | None = None,
    min_coverage_high_confidence: float = 100.0,
    min_n_high_confidence: float = 300.0,
    min_coverage_strong: float = 70.0,
    min_n_strong: float = 75.0,
    min_coverage_watch: float = 60.0,
    min_n_watch: float = 50.0,
    top_n: int | None = 40,
) -> pd.DataFrame:
    display_cols = [
        "promotion_tier",
        "evidence_tier",
        "performance_tier",
        "Deck",
        "Share_%",
        "coverage_vs_core_%",
        "N_vs_core",
        "WR_vs_core_weighted_%",
        "in_candidate_pool",
        "dropped_by_nan_filter",
        "coverage_all_%",
        "nan_ratio_all",
    ]
    if wildcard_candidates is None or wildcard_candidates.empty:
        return pd.DataFrame(columns=display_cols)

    wc = wildcard_candidates.copy()
    numeric_cols = [
        "WR_vs_core_weighted_%",
        "coverage_vs_core_%",
        "N_vs_core",
        "Share_%",
        "coverage_all_%",
        "nan_ratio_all",
    ]
    for col in numeric_cols:
        if col in wc.columns:
            wc[col] = pd.to_numeric(wc[col], errors="coerce")

    baseline = 50.0 if core_wr_baseline is None else float(core_wr_baseline)

    def evidence_tier(row: pd.Series) -> str:
        coverage = float(row.get("coverage_vs_core_%", 0.0) or 0.0)
        n = float(row.get("N_vs_core", 0.0) or 0.0)
        if coverage >= min_coverage_strong and n >= min_n_strong:
            return "strong_evidence"
        if coverage >= min_coverage_watch and n >= min_n_watch:
            return "watch_evidence"
        return "low_evidence"

    def performance_tier(row: pd.Series) -> str:
        wr_raw = row.get("WR_vs_core_weighted_%", pd.NA)
        if pd.isna(wr_raw):
            return "unknown_performance"
        wr = float(wr_raw)
        if wr >= baseline:
            return "above_core_baseline"
        if wr >= 50.0:
            return "above_even"
        return "below_even"

    wc["evidence_tier"] = wc.apply(evidence_tier, axis=1)
    wc["performance_tier"] = wc.apply(performance_tier, axis=1)

    def promotion_tier(row: pd.Series) -> str:
        coverage = float(row.get("coverage_vs_core_%", 0.0) or 0.0)
        n = float(row.get("N_vs_core", 0.0) or 0.0)
        if (
            coverage >= min_coverage_high_confidence
            and n >= min_n_high_confidence
            and row.get("performance_tier") == "above_core_baseline"
        ):
            return "high_confidence_candidate"
        if row.get("evidence_tier") == "strong_evidence" and row.get("performance_tier") in {
            "above_core_baseline",
            "above_even",
        }:
            return "watchlist"
        return "not_recommended"

    wc["promotion_tier"] = wc.apply(promotion_tier, axis=1)

    promotion_order = {"high_confidence_candidate": 0, "watchlist": 1, "not_recommended": 2}
    evidence_order = {"strong_evidence": 0, "watch_evidence": 1, "low_evidence": 2}
    performance_order = {
        "above_core_baseline": 0,
        "above_even": 1,
        "unknown_performance": 2,
        "below_even": 3,
    }
    wc["_promotion_order"] = wc["promotion_tier"].map(promotion_order).fillna(99)
    wc["_evidence_order"] = wc["evidence_tier"].map(evidence_order).fillna(99)
    wc["_performance_order"] = wc["performance_tier"].map(performance_order).fillna(99)
    wc = wc.sort_values(
        [
            "_promotion_order",
            "_evidence_order",
            "_performance_order",
            "WR_vs_core_weighted_%",
            "coverage_vs_core_%",
            "N_vs_core",
            "Share_%",
        ],
        ascending=[True, True, True, False, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)

    out_cols = [col for col in display_cols if col in wc.columns]
    out = wc.loc[:, out_cols].copy()
    if top_n is not None:
        out = out.head(max(1, int(top_n)))
    return out.reset_index(drop=True)


def core_weighted_wr_baseline(score_flat: pd.DataFrame | None) -> float | None:
    if score_flat is None or score_flat.empty:
        return None
    if not {"Deck A", "W", "L"} <= set(score_flat.columns):
        return None

    sf = score_flat.copy()
    sf["W"] = pd.to_numeric(sf["W"], errors="coerce").fillna(0.0)
    sf["L"] = pd.to_numeric(sf["L"], errors="coerce").fillna(0.0)
    core_perf = sf.groupby("Deck A", sort=False)[["W", "L"]].sum()
    denom = core_perf["W"] + core_perf["L"]
    core_perf = core_perf[denom > 0].copy()
    if core_perf.empty:
        return None
    core_perf["WR_vs_core_weighted_%"] = 100.0 * core_perf["W"] / (core_perf["W"] + core_perf["L"])
    return float(core_perf["WR_vs_core_weighted_%"].median())


def wildcard_summary_frame(
    wildcard_review: pd.DataFrame | None,
    *,
    core_wr_baseline: float | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    wc = wildcard_review.copy() if wildcard_review is not None else pd.DataFrame()

    rows.append({"Area": "baseline", "Metric": "core_weighted_wr_median_%", "Value": None if core_wr_baseline is None else round(float(core_wr_baseline), 4)})
    rows.append({"Area": "wildcards", "Metric": "candidate_rows", "Value": int(len(wc))})

    if wc.empty:
        return pd.DataFrame(rows, columns=["Area", "Metric", "Value"])

    for area, col in [
        ("promotion", "promotion_tier"),
        ("evidence", "evidence_tier"),
        ("performance", "performance_tier"),
    ]:
        if col not in wc.columns:
            continue
        counts = wc[col].value_counts(dropna=False)
        for label, count in counts.items():
            rows.append({"Area": area, "Metric": str(label), "Value": int(count)})

    numeric_specs = [
        ("coverage_vs_core_%", "coverage_vs_core"),
        ("N_vs_core", "n_vs_core"),
        ("WR_vs_core_weighted_%", "wr_vs_core_weighted"),
    ]
    for col, label in numeric_specs:
        if col not in wc.columns:
            continue
        values = pd.to_numeric(wc[col], errors="coerce").dropna()
        if values.empty:
            continue
        rows.extend(
            [
                {"Area": label, "Metric": "min", "Value": round(float(values.min()), 4)},
                {"Area": label, "Metric": "median", "Value": round(float(values.median()), 4)},
                {"Area": label, "Metric": "max", "Value": round(float(values.max()), 4)},
            ]
        )

    return pd.DataFrame(rows, columns=["Area", "Metric", "Value"])


def frame_inventory_frame(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for name, frame in sorted((frames or {}).items()):
        rows.append(
            {
                "Frame": name,
                "Rows": len(frame),
                "Columns": len(frame.columns),
            }
        )
    return pd.DataFrame(rows, columns=["Frame", "Rows", "Columns"])


__all__ = [
    "DEFAULT_RANKING_COLS",
    "DEFAULT_RANKING_FORMATS",
    "analysis_scope_summary_frame",
    "candidate_vs_full_summary_frame",
    "coverage_volume_summary_frame",
    "core_weighted_wr_baseline",
    "evidence_core_comparison_frame",
    "evidence_core_eligibility_frame",
    "evidence_core_iterative_frame",
    "evidence_core_iterative_summary_frame",
    "ranking_preview_frame",
    "diagnostics_preview_frame",
    "frame_inventory_frame",
    "meta_diagnostics_summary_frame",
    "nan_diagnostics_critical_frame",
    "output_paths_frame",
    "run_overview_frame",
    "saved_outputs_frame",
    "scrape_timing_frame",
    "share_distribution_frame",
    "show_ranking",
    "style_ranking_preview",
    "wildcard_review_frame",
    "wildcard_summary_frame",
]
