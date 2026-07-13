# PTCGP Ranking - MARS

End-to-end deck ranking pipeline for Limitless TCG data. It scrapes decklists
and matchup pages, builds directional win-rate matrices, filters sparse axes,
and ranks decks with **MARS**: a meta-adjusted, regularized score that combines
Bayesian smoothing, MAS/SE/LB, Bradley-Terry strength, and meta-vs-encounter
weighting.

The project now uses one Python pipeline as the source of truth. Notebooks are
kept as friendly run and preview interfaces.

## What Is Included

- Multi-game Limitless support for Pokemon TCG Pocket and Pokemon TCG.
- Automatic or configured set/format resolution.
- Polite scraping with cache, retry, delay, jitter, and timing diagnostics.
- Candidate-pool and NaN coverage diagnostics.
- MARS ranking output as CSV.
- Win-rate heatmap output.
- Styled Excel matchup report with summary, per-deck sheets, legend cover, and
  atomic write/retry handling for locked files.
- CLI, Python API, and notebook entry points.
- Unit tests for routing, scraping adapters, reporting, pipeline behavior, and
  core matrix contracts.

## Project Layout

```text
ptcgp_ranking/
|-- cli/
|   `-- deck_ranking.py
|-- config/
|   |-- alias_map.json
|   |-- config.yaml
|   |-- config_tcg.yaml
|   |-- config_tcg_wildcard.yaml
|   `-- loader.py
|-- core/
|   |-- consolidate.py
|   |-- matrices.py
|   |-- nan_diagnostics.py
|   |-- nan_filter.py
|   `-- normalize.py
|-- domain/
|   `-- expansions.py
|-- mars/
|   |-- auto_k_cv.py
|   |-- bt.py
|   |-- composite.py
|   |-- config.py
|   |-- coverage.py
|   |-- mas_lb.py
|   |-- meta.py
|   |-- pipeline.py
|   |-- posterior.py
|   |-- report.py
|   `-- validate_io.py
|-- notebooks/
|   |-- deck_ranking_run_all.ipynb
|   `-- deck_ranking_run_all_dev.ipynb
|-- pipelines/
|   `-- deck_ranking.py
|-- reporting/
|   |-- excel.py
|   |-- logs.py
|   |-- plots.py
|   `-- tables.py
|-- scraper/
|   `-- compatibility wrappers for older imports
|-- sources/
|   `-- limitless/
|       |-- browser.py
|       |-- client.py
|       |-- constants.py
|       `-- pages/
|           |-- decks.py
|           `-- sets.py
|-- storage/
|   |-- paths.py
|   |-- routing.py
|   `-- writers.py
|-- tests/
|-- utils/
|   `-- compatibility and display helpers
|-- MARS_explained.md
|-- project_tree.txt
|-- pytest.ini
`-- requirements.txt
```

## Quick Start

Create a virtual environment and install dependencies:

```bash
python -m venv .venv

# Windows
. .venv/Scripts/activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Run the default Pocket pipeline:

```bash
python -m cli.deck_ranking run --config config/config.yaml
```

Run Pokemon TCG:

```bash
python -m cli.deck_ranking run --config config/config_tcg.yaml --progress
```

Run the exploratory TCG wildcard profile:

```bash
python -m cli.deck_ranking run --config config/config_tcg_wildcard.yaml --progress
```

The CLI supports stage skips:

```bash
python -m cli.deck_ranking run --config config/config.yaml --skip-scrape --skip-report
```

Available skip flags are `--skip-scrape`, `--skip-core`, `--skip-mars`,
`--skip-heatmap`, and `--skip-report`.

## Notebook Workflow

Open `notebooks/deck_ranking_run_all.ipynb`, select the project virtual
environment as kernel, and choose a profile in the first cell:

```python
GAME_PROFILE = "pocket"        # Pokemon TCG Pocket
GAME_PROFILE = "tcg"           # Pokemon TCG
GAME_PROFILE = "tcg_wildcard"  # Pokemon TCG with full scrape + wildcard appendix
```

Run all cells. The notebook calls `run_deck_ranking(...)`, then previews the
resolved source, output paths, diagnostics, ranking, heatmap, and wildcard
appendix when available.

`notebooks/deck_ranking_run_all_dev.ipynb` is reserved for local development. It
sets `PTCGP_I_KNOW_FAST_SCRAPE_IS_FOR_DEV_ONLY=1` in the current session so
matchup scraping can skip delay while working from cache or test data.

## Python API

```python
from pipelines.deck_ranking import run_deck_ranking

result = run_deck_ranking(
    config_path="config/config.yaml",
    run_scrape=True,
    run_mars=True,
    run_heatmap=True,
    run_report=True,
    show_progress=True,
)
```

`result.outputs` contains written file paths, `result.frames` contains the main
DataFrames, and `result.diagnostics` contains source scope, NaN diagnostics,
scrape timing, MARS diagnostics, and wildcard summaries.

## Configuration

The default Pocket config is `config/config.yaml`.

```yaml
source:
  provider: limitless
  game: POCKET
  format:
    mode: auto
    code: ""

logging:
  level: INFO

scraping:
  request_delay_sec: 5.0
  request_delay_jitter_frac: 0.25

paths:
  output_dir: outputs

top_meta:
  threshold_pct: 80.0
  ensure_at_least: 1

analysis:
  candidate_pool:
    share_pct: 80.0
  wildcard_pass:
    enabled: false
    min_coverage_vs_core_pct: 60.0
    min_n_vs_core: 50

nan_filter:
  mode: fixed
  max_nan_ratio: 0.15
  min_nan_allowed: 1
```

Use `config/config_tcg.yaml` for a normal Pokemon TCG run and
`config/config_tcg_wildcard.yaml` for an exploratory run that scrapes the full
decklist and reports wildcard candidates without changing the main MARS ranking.

## Output Layout

Outputs are scoped by game, format, and set:

```text
outputs/<GAME>/<FORMAT>/<CODE>__<NAME>/
|-- decklists/
|   |-- raw/
|   `-- top_meta/
|-- matchups/
|   |-- raw/
|   `-- scores/
|-- matrices/
|   |-- heatmaps/
|   |-- match_counts/
|   `-- winrate/
|-- diagnostics/
|   |-- nan_filter/
|   `-- wildcards/
|-- rankings/
|   `-- mars/
`-- reports/
    `-- mars/
```

Important latest files:

- `matchups/scores/matchup_scores_latest.csv`
- `matrices/winrate/winrate_matrix_latest.csv`
- `matrices/match_counts/match_count_matrix_latest.csv`
- `matrices/heatmaps/wr_heatmap_latest.png`
- `rankings/mars/mars_ranking_latest.csv`
- `reports/mars/mars_matchup_report_latest.xlsx`

`paths.output_dir` can be relative to the repository root or an absolute path
outside the repository, which is useful for heavy outputs on OneDrive or another
drive.

## MARS In Short

MARS ranks each deck by blending two signals:

- **LB**: a conservative meta-adjusted score, computed from smoothed matchup
  win probabilities as `MAS - z * SE`.
- **BT%**: a regularized Bradley-Terry strength estimate over the matchup graph.

The final score standardizes both signals and blends them:

```text
z_comp = alpha * z(LB) + (1 - alpha) * z(BT)
Score_% = 100 * Phi(z_comp / sqrt(2))
```

See `MARS_explained.md` for the full method notes.

## Wildcard Diagnostics

The normal ranking uses a candidate pool based on cumulative meta share
(`analysis.candidate_pool.share_pct`, default `80.0`). The wildcard profile can
scrape the full decklist, keep the main core comparable, and report excluded
decks that have enough evidence against the core.

For Pokemon TCG, the notebook separates:

- `evidence_tier`: coverage and volume against the core.
- `performance_tier`: weighted win rate against the core.
- `promotion_tier`: readable categories such as `high_confidence_candidate`,
  `watchlist`, and `not_recommended`.

These categories are diagnostics only. They do not automatically promote decks
into the MARS ranking.

## Development

Run the test suite:

```bash
pytest -q
```

`pytest.ini` uses `.pytest_tmp` as local base temp so tests do not depend on the
user-level Windows temp directory.

Regenerate the project tree:

```bash
python utils/make_project_tree.py
```

Before committing:

```bash
git status --short
pytest -q
rg -n "[àèéìòù]" README.md CONTRIBUTING.md MARS_explained.md docs config -i
```

## License

MIT. See `LICENSE`.

Copyright (c) 2025 Andrea Visentin.

Suggested citation:

> Visentin, A. (2025). PTCGP Ranking - MARS. MIT License.
> https://github.com/indren9/ptcgp_ranking

## Author

Andrea Visentin - GitHub: https://github.com/indren9
