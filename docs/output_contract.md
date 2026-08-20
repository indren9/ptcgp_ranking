# Saved Output Contract

This document defines the implemented persistence policy for pipeline
artifacts. The pipeline keeps rich intermediate DataFrames in memory while the
selected saving profile controls which files are written to disk.

## Complete Artifact Inventory

```text
decklists/raw/decklist_raw_latest.csv
decklists/top_meta/top_meta_decklist_latest.csv
matchups/raw/matchup_raw_latest.csv
matchups/scores/matchup_scores_latest.csv
matchups/scores/matchup_scores_<timestamp>.csv
matrices/winrate/winrate_matrix_latest.csv
matrices/winrate/winrate_matrix_<timestamp>.csv
matrices/match_counts/match_count_matrix_latest.csv
matrices/match_counts/match_count_matrix_<timestamp>.csv
diagnostics/nan_filter/nan_diagnostics_pre_filter_latest.csv
diagnostics/nan_filter/nan_diagnostics_pre_filter_<timestamp>.csv
diagnostics/nan_filter/nan_filter_simulation_latest.csv
diagnostics/nan_filter/nan_filter_simulation_<timestamp>.csv
diagnostics/wildcards/wildcard_candidates_latest.csv
diagnostics/wildcards/wildcard_candidates_<timestamp>.csv
rankings/mars/mars_ranking_latest.csv
rankings/mars/mars_ranking_<timestamp>.csv
matrices/heatmaps/wr_heatmap_latest.png
matrices/heatmaps/wr_heatmap_T<N>_<timestamp>.png
reports/mars/legend_latest.png
reports/mars/mars_matchup_report_latest.xlsx
reports/mars/mars_matchup_report_<timestamp>.xlsx
```

## Saving Profiles

### User Profile

Default target for normal notebook/CLI runs in the shipped configs.

Keep:
- `reports/mars/mars_matchup_report_latest.xlsx`
- `rankings/mars/mars_ranking_latest.csv`
- `matrices/heatmaps/wr_heatmap_latest.png`
- `diagnostics/wildcards/wildcard_candidates_latest.csv`, only when wildcard
  exploration is enabled or candidates exist
- `run/run_manifest_latest.json`

Do not save by default:
- timestamped CSV matrix/scores copies
- timestamped heatmap PNG files
- timestamped Excel reports
- standalone `legend_latest.png`
- full raw matchup table
- full win-rate and match-count matrices
- NaN diagnostic CSV if it is only used for notebook display
- NaN diagnostic sheets in the Excel report
- empty wildcard CSV files

### Reproducible Profile

For runs where we want to be able to rebuild reports without scraping again.

Keep everything in the user profile, plus:
- `decklists/raw/decklist_raw_latest.csv`
- `decklists/top_meta/top_meta_decklist_latest.csv`
- `matchups/raw/matchup_raw_latest.csv`
- config snapshot or run manifest

This profile supports `--skip-scrape` and rebuilding core/MARS/report stages
from saved data.
Timestamped CSV/PNG/XLSX copies remain disabled by default in this profile.

### Debug Profile

For development, regression tests, and investigating ranking issues.

Keep the current rich artifact set:
- score table
- WR matrix
- match-count matrix
- NaN diagnostics
- NaN dynamic simulation, when present
- wildcard diagnostics
- timestamped copies
- standalone `legend_latest.png`

This is the backward-compatible fallback when `saving.output_profile` is not
configured.

## Implementation Notes

- Keep `result.frames` rich in memory even when fewer files are saved.
- Use `saving.output_profile: user | reproducible | debug`.
- In notebooks, display saved artifacts through `saved_outputs_frame`, which
  labels each path as `user`, `reproducible`, `debug`, or `unknown`.
- Continue moving the artifact registry out of pipeline code if it grows:
  each output key should declare route, filename prefix, artifact tier,
  whether it needs `latest`, whether it needs timestamped copies, and whether it
  is required by skip/rebuild stages.
- Keep backward-compatible reading of existing `*_latest` files during the
  transition.
- Avoid deleting existing user output automatically; cleanup should be manual or
  handled by a dedicated script.
- Notebook output tables should show user-facing artifacts first and optionally
  hide debug artifacts.

## Contract Decisions

- NaN diagnostics stay notebook-only in the `user` profile.
- The Excel report remains a final user-facing report, not a technical
  diagnostics container.
- Full NaN diagnostic CSV files remain available in the `debug` profile.
