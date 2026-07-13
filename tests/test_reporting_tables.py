import pandas as pd
import pytest
from pathlib import Path

from reporting.tables import (
    analysis_scope_summary_frame,
    candidate_vs_full_summary_frame,
    coverage_volume_summary_frame,
    diagnostics_preview_frame,
    evidence_core_comparison_frame,
    evidence_core_eligibility_frame,
    evidence_core_iterative_frame,
    evidence_core_iterative_summary_frame,
    frame_inventory_frame,
    meta_diagnostics_summary_frame,
    nan_diagnostics_critical_frame,
    output_paths_frame,
    ranking_preview_frame,
    share_distribution_frame,
    show_ranking,
    style_ranking_preview,
    wildcard_review_frame,
)


def sample_ranking() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Deck": "Pikachu", "Score_%": 55.123, "Coverage_%": 98.7, "extra": "a"},
            {"Deck": "Mewtwo", "Score_%": 45.456, "Coverage_%": 91.2, "extra": "b"},
            {"Deck": "Charizard", "Score_%": 40.0, "Coverage_%": 80.0, "extra": "c"},
        ]
    )


def test_ranking_preview_frame_keeps_known_columns_and_top_n():
    preview = ranking_preview_frame(sample_ranking(), top_n=2)

    assert preview["Deck"].tolist() == ["Pikachu", "Mewtwo"]
    assert preview.columns.tolist() == ["Deck", "Score_%", "Coverage_%"]


def test_show_ranking_return_df_preserves_legacy_api():
    preview = show_ranking(sample_ranking(), top_n=1, show=False, return_df=True)

    assert preview.shape == (1, 3)
    assert preview.iloc[0]["Deck"] == "Pikachu"


def test_show_ranking_rejects_two_return_modes():
    with pytest.raises(ValueError):
        show_ranking(sample_ranking(), show=False, return_df=True, return_styler=True)


def test_style_ranking_preview_returns_styler():
    styler = style_ranking_preview(sample_ranking(), top_n=2, title="Top test")

    assert styler.caption == "Top test"


def test_utils_display_show_ranking_is_compatible_wrapper():
    from utils.display import show_ranking as legacy_show_ranking

    preview = legacy_show_ranking(sample_ranking(), top_n=1, show=False, return_df=True)
    assert preview.iloc[0]["Deck"] == "Pikachu"


def test_output_paths_frame_can_show_relative_paths(tmp_path):
    output = tmp_path / "outputs" / "ranking.csv"
    frame = output_paths_frame({"ranking": output}, base_dir=tmp_path)

    assert frame.to_dict("records") == [{"Output": "ranking", "Path": str(Path("outputs") / "ranking.csv")}]


def test_diagnostics_preview_frame_compacts_nested_values():
    frame = diagnostics_preview_frame({"mars_diag": {"K": 1.0}, "dropped": ["a", "b"], "rows": 2})

    assert frame.loc[frame["Diagnostic"] == "mars_diag", "Value"].item() == "<dict: 1 keys>"
    assert frame.loc[frame["Diagnostic"] == "dropped", "Value"].item() == "<list: 2 items>"
    assert frame.loc[frame["Diagnostic"] == "rows", "Value"].item() == 2


def test_frame_inventory_frame_reports_shapes():
    frame = frame_inventory_frame({"ranking": sample_ranking()})

    assert frame.to_dict("records") == [{"Frame": "ranking", "Rows": 3, "Columns": 4}]


def test_nan_diagnostics_critical_frame_hides_full_coverage_rows():
    diagnostics = pd.DataFrame(
        {
            "Deck": ["Top Deck", "Sparse Deck", "Ok Deck"],
            "Share_%": [30.0, 1.0, 10.0],
            "observed_opponents": [2, 1, 2],
            "total_opponents": [2, 2, 2],
            "coverage_%": [100.0, 50.0, 100.0],
            "nan_count": [0, 1, 0],
            "nan_ratio": [0.0, 0.5, 0.0],
            "total_matches": [1000, 5, 200],
        }
    )

    frame = nan_diagnostics_critical_frame(diagnostics)

    assert frame["Deck"].tolist() == ["Sparse Deck"]


def test_nan_diagnostics_critical_frame_empty_when_no_nan():
    diagnostics = pd.DataFrame(
        {
            "Deck": ["Top Deck", "Ok Deck"],
            "Share_%": [30.0, 10.0],
            "coverage_%": [100.0, 100.0],
            "nan_count": [0, 0],
            "nan_ratio": [0.0, 0.0],
        }
    )

    frame = nan_diagnostics_critical_frame(diagnostics)

    assert frame.empty


def test_share_distribution_frame_accepts_tcg_share_column():
    top_meta = pd.DataFrame(
        {
            "Rank": [1, 2, 3],
            "Deck": ["Dragapult", "Slowking", "Other"],
            "share": [7.88, 5.81, 1.13],
            "Count": [2398, 1769, 344],
        }
    )

    frame = share_distribution_frame(top_meta, top_n=2)

    assert frame["Deck"].tolist() == ["Dragapult", "Slowking"]
    assert frame["Share_%"].tolist() == [7.88, 5.81]
    assert frame["Share_cum_%"].tolist() == [7.88, 13.69]


def test_candidate_vs_full_summary_frame_reports_tail_and_delay():
    raw = pd.DataFrame(
        {
            "Deck": ["A", "B", "C", "D", "E"],
            "share": [40.0, 25.0, 15.0, 3.0, 2.0],
        }
    )
    candidate = raw.iloc[:3].copy()

    frame = candidate_vs_full_summary_frame(decklist_raw=raw, top_meta=candidate, request_delay_sec=5.0)

    values = dict(zip(frame["Metric"], frame["Value"]))
    assert values["full_decklist_rows"] == 5
    assert values["candidate_rows"] == 3
    assert values["excluded_rows"] == 2
    assert values["candidate_share_%"] == 80.0
    assert values["excluded_share_%"] == 5.0
    assert values["excluded_top_deck"] == "D"
    assert values["estimated_extra_delay_seconds"] == 10.0


def test_candidate_vs_full_summary_frame_derives_candidate_pool_from_full_top_meta():
    raw = pd.DataFrame(
        {
            "Deck": ["A", "B", "C", "D", "E"],
            "share": [40.0, 25.0, 15.0, 3.0, 2.0],
        }
    )

    frame = candidate_vs_full_summary_frame(
        decklist_raw=raw,
        top_meta=raw,
        candidate_share_pct=80.0,
        request_delay_sec=5.0,
    )

    values = dict(zip(frame["Metric"], frame["Value"]))
    assert values["full_decklist_rows"] == 5
    assert values["candidate_rows"] == 3
    assert values["excluded_rows"] == 2
    assert values["excluded_share_%"] == 5.0


def test_analysis_scope_summary_frame_separates_fetch_from_mars_core():
    raw = pd.DataFrame(
        {
            "Deck": ["A", "B", "C", "D"],
            "share": [50.0, 30.0, 10.0, 1.0],
        }
    )
    top_meta = raw.copy()
    ranking = pd.DataFrame({"Deck": ["A", "B"]})
    score = pd.DataFrame({"Deck A": ["A"], "Deck B": ["B"]})
    matchup_raw = pd.DataFrame({"Deck A": ["A", "C"], "Deck B": ["B", "A"]})
    wildcards = pd.DataFrame({"Deck": ["C"]})

    frame = analysis_scope_summary_frame(
        decklist_raw=raw,
        top_meta=top_meta,
        mars_ranking=ranking,
        score_flat=score,
        matchup_raw=matchup_raw,
        wildcard_candidates=wildcards,
        candidate_share_pct=80.0,
    )

    values = dict(zip(frame["Metric"], frame["Value"]))
    assert values["full_decklist_rows"] == 4
    assert values["matchup_fetch_rows"] == 4
    assert values["mars_core_rows"] == 2
    assert values["matchup_score_rows"] == 1
    assert values["matchup_raw_rows"] == 2
    assert values["wildcard_rows"] == 1
    assert values["configured_candidate_pool_rows"] == 2
    assert values["configured_candidate_pool_share_actual_%"] == 80.0


def test_evidence_core_eligibility_frame_marks_decks_by_coverage_and_volume():
    raw = pd.DataFrame({"Deck": ["A", "B", "C"], "share": [50.0, 20.0, 1.0]})
    nan_diag = pd.DataFrame(
        {
            "Deck": ["A", "B", "C"],
            "coverage_%": [100.0, 55.0, 80.0],
            "nan_ratio": [0.0, 0.45, 0.2],
            "total_matches": [1000, 200, 10],
            "observed_opponents": [2, 1, 2],
            "total_opponents": [2, 2, 2],
        }
    )

    frame = evidence_core_eligibility_frame(
        decklist_raw=raw,
        nan_diag=nan_diag,
        min_coverage_pct=60.0,
        min_total_matches=50.0,
    )

    eligible = dict(zip(frame["Deck"], frame["eligible"]))
    assert eligible == {"A": True, "C": False, "B": False}


def test_evidence_core_comparison_frame_reports_evidence_only_tail():
    raw = pd.DataFrame(
        {
            "Deck": ["A", "B", "C", "D"],
            "share": [50.0, 30.0, 10.0, 1.0],
        }
    )
    nan_diag = pd.DataFrame(
        {
            "Deck": ["A", "B", "C", "D"],
            "coverage_%": [100.0, 100.0, 100.0, 100.0],
            "nan_ratio": [0.0, 0.0, 0.0, 0.0],
            "total_matches": [1000, 900, 800, 700],
        }
    )

    frame = evidence_core_comparison_frame(
        decklist_raw=raw,
        nan_diag=nan_diag,
        share_core_pct=80.0,
        min_coverage_pct=60.0,
        min_total_matches=50.0,
    )

    values = dict(zip(frame["Metric"], frame["Value"]))
    assert values["share_core_decks"] == 2
    assert values["evidence_core_decks"] == 4
    assert values["evidence_only_decks"] == 2
    assert values["evidence_only_share_%"] == 11.0


def test_evidence_core_iterative_frame_adds_decks_against_current_axis():
    raw = pd.DataFrame(
        {
            "Deck": ["A", "B", "C", "D"],
            "share": [50.0, 30.0, 10.0, 1.0],
        }
    )
    n_dir = pd.DataFrame(
        [
            [0, 100, 20, 0],
            [100, 0, 20, 0],
            [20, 20, 0, 0],
            [0, 0, 0, 0],
        ],
        index=["A", "B", "C", "D"],
        columns=["A", "B", "C", "D"],
    )

    frame = evidence_core_iterative_frame(
        decklist_raw=raw,
        n_dir_matrix=n_dir,
        share_core_pct=80.0,
        min_coverage_vs_axis_pct=100.0,
        min_n_vs_axis=30.0,
    )

    selected = frame[frame["selected"]]
    assert selected["Deck"].tolist() == ["A", "B", "C"]
    added = frame[frame["added_by_evidence"]].iloc[0]
    assert added["Deck"] == "C"
    assert added["iteration_added"] == 1
    assert added["coverage_at_add_%"] == 100.0
    assert added["N_at_add"] == 40.0
    assert not frame.loc[frame["Deck"] == "D", "selected"].item()


def test_evidence_core_iterative_frame_can_build_axis_from_raw_matchups():
    raw = pd.DataFrame({"Deck": ["A", "B", "C"], "share": [50.0, 30.0, 10.0]})
    matchups = pd.DataFrame(
        {
            "Deck A": ["A", "B", "C", "C"],
            "Deck B": ["B", "A", "A", "B"],
            "W": [10, 10, 3, 3],
            "L": [10, 10, 3, 3],
        }
    )

    frame = evidence_core_iterative_frame(
        decklist_raw=raw,
        matchup_raw=matchups,
        share_core_pct=80.0,
        min_coverage_vs_axis_pct=100.0,
        min_n_vs_axis=10.0,
    )

    added = frame[frame["added_by_evidence"]].iloc[0]
    assert added["Deck"] == "C"
    assert added["N_at_add"] == 12.0


def test_evidence_core_iterative_frame_handles_nullable_raw_matchup_n():
    raw = pd.DataFrame({"Deck": ["A", "B", "C"], "share": [50.0, 30.0, 10.0]})
    matchups = pd.DataFrame(
        {
            "Deck A": ["A", "B", "C", "C"],
            "Deck B": ["B", "A", "A", "B"],
            "N": pd.Series([20.0, 20.0, 6.0, 6.0], dtype="Float64"),
        }
    )

    frame = evidence_core_iterative_frame(
        decklist_raw=raw,
        matchup_raw=matchups,
        share_core_pct=80.0,
        min_coverage_vs_axis_pct=100.0,
        min_n_vs_axis=10.0,
    )

    added = frame[frame["added_by_evidence"]].iloc[0]
    assert added["Deck"] == "C"
    assert added["N_at_add"] == 12.0


def test_evidence_core_iterative_summary_frame_reports_added_share():
    raw = pd.DataFrame({"Deck": ["A", "B", "C"], "share": [50.0, 30.0, 10.0]})
    n_dir = pd.DataFrame(
        [[0, 100, 20], [100, 0, 20], [20, 20, 0]],
        index=["A", "B", "C"],
        columns=["A", "B", "C"],
    )
    iterative = evidence_core_iterative_frame(
        decklist_raw=raw,
        n_dir_matrix=n_dir,
        share_core_pct=80.0,
        min_coverage_vs_axis_pct=100.0,
        min_n_vs_axis=30.0,
    )

    frame = evidence_core_iterative_summary_frame(iterative)

    values = dict(zip(frame["Metric"], frame["Value"]))
    assert values["iterative_selected_decks"] == 3
    assert values["iterative_anchor_decks"] == 2
    assert values["iterative_added_decks"] == 1
    assert values["iterative_selected_share_%"] == 90.0
    assert values["iterative_added_share_%"] == 10.0


def test_meta_diagnostics_summary_frame_reports_candidate_coverage_and_wildcards():
    top_meta = pd.DataFrame({"Deck": ["A", "B"], "Share_%": [55.0, 25.0]})
    nan_diag = pd.DataFrame(
        {
            "Deck": ["A", "B"],
            "coverage_%": [100.0, 50.0],
            "nan_count": [0, 1],
            "nan_ratio": [0.0, 0.5],
            "total_matches": [1000, 100],
        }
    )
    wildcards = pd.DataFrame({"Deck": ["C"]})

    frame = meta_diagnostics_summary_frame(top_meta=top_meta, nan_diag=nan_diag, wildcard_candidates=wildcards)

    values = dict(zip(frame["Metric"], frame["Value"]))
    assert values["candidate_decks"] == 2
    assert values["candidate_share_%"] == 80.0
    assert values["critical_nan_decks"] == 1
    assert values["wildcard_candidates"] == 1


def test_coverage_volume_summary_frame_reports_quantiles():
    nan_diag = pd.DataFrame(
        {
            "coverage_%": [100.0, 75.0, 50.0],
            "nan_ratio": [0.0, 0.25, 0.5],
            "total_matches": [1000, 500, 100],
            "observed_opponents": [10, 8, 5],
        }
    )

    frame = coverage_volume_summary_frame(nan_diag)

    values = dict(zip(frame["Metric"], frame["Value"]))
    assert values["coverage_%_median"] == 75.0
    assert values["nan_ratio_max"] == 0.5
    assert values["total_matches_min"] == 100.0


def test_wildcard_review_frame_separates_evidence_from_performance():
    wildcards = pd.DataFrame(
        {
            "Deck": ["Solid Bad", "Thin Good", "Solid Good", "High Confidence"],
            "Share_%": [0.4, 0.2, 0.3, 0.1],
            "coverage_vs_core_%": [80.0, 50.0, 75.0, 100.0],
            "N_vs_core": [100, 20, 90, 320],
            "WR_vs_core_weighted_%": [45.0, 65.0, 60.0, 62.0],
            "in_candidate_pool": [False, False, False, False],
            "dropped_by_nan_filter": [False, False, False, False],
        }
    )

    frame = wildcard_review_frame(wildcards, core_wr_baseline=55.0)

    solid_bad = frame[frame["Deck"] == "Solid Bad"].iloc[0]
    thin_good = frame[frame["Deck"] == "Thin Good"].iloc[0]
    solid_good = frame[frame["Deck"] == "Solid Good"].iloc[0]
    high_confidence = frame[frame["Deck"] == "High Confidence"].iloc[0]
    assert solid_bad["evidence_tier"] == "strong_evidence"
    assert solid_bad["performance_tier"] == "below_even"
    assert solid_bad["promotion_tier"] == "not_recommended"
    assert thin_good["evidence_tier"] == "low_evidence"
    assert thin_good["performance_tier"] == "above_core_baseline"
    assert thin_good["promotion_tier"] == "not_recommended"
    assert solid_good["evidence_tier"] == "strong_evidence"
    assert solid_good["performance_tier"] == "above_core_baseline"
    assert solid_good["promotion_tier"] == "watchlist"
    assert high_confidence["promotion_tier"] == "high_confidence_candidate"
    assert frame.iloc[0]["Deck"] == "High Confidence"
