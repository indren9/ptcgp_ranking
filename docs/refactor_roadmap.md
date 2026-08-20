# Refactor Roadmap

This document records the current architecture and the remaining cleanup work
before publishing a clean commit series.

## Goals

- Keep the deck ranking pipeline reproducible from CLI, Python, and notebook.
- Keep notebooks as thin run and preview interfaces.
- Keep acquisition, transformation, analysis, storage, and reporting separated.
- Preserve compatibility wrappers until their removal is intentional and tested.
- Keep `pytest -q` green after every change.
- Keep public documentation and user-facing messages in English.

## Current Architecture

```text
config/                 Run profiles and config loading
sources/limitless/      Limitless HTTP/browser adapters and page parsers
domain/                 Domain models such as Expansion
core/                   DataFrame transformations, matrices, NaN filtering
mars/                   MARS ranking algorithm and Excel report assembly
storage/                Output paths, routing, latest-file lookup, writers
reporting/              Tables, plots, logs, Excel styling helpers
pipelines/              End-to-end deck ranking orchestration
cli/                    Command line entry point
notebooks/              Human-facing run and preview interfaces
scraper/, utils/        Compatibility wrappers for older imports
tests/                  Contract and regression tests
```

## Completed Work

- Added scoped output routing:
  `outputs/<GAME>/<FORMAT>/<CODE>__<NAME>/...`.
- Added support for absolute or relative external output roots.
- Moved storage responsibilities into `storage/`.
- Moved Limitless scraping and parsing into `sources.limitless`.
- Added `domain.expansions`.
- Added `pipelines.deck_ranking.run_deck_ranking`.
- Added CLI support through `python -m cli.deck_ranking run`.
- Reduced notebooks to wrapper/preview interfaces.
- Added TCG configs and verified TCG runs across set changes.
- Added wildcard diagnostics for TCG full-scrape exploration.
- Added reporting tables for share distribution, coverage, wildcard review, and
  evidence-core simulations.
- Added a notebook scope summary that separates full decklist/fetch rows from
  the effective MARS core rows, especially for TCG wildcard runs.
- Stabilized output naming around unprefixed `*_latest` files inside scoped
  folders.
- Added local Pytest base temp through `.pytest_tmp`.
- Added first-pass saved output profiles:
  `user`, `reproducible`, and `debug`.
- Added profile-aware heatmap/report saving and compact run manifests.
- Added notebook-facing run overview and scrape timing summary tables.
- Added notebook-facing wildcard summary and prioritized wildcard review tables.
- Made notebook profile selection explicit with profile descriptions before the
  run cell executes.
- Added notebook-facing saved artifact classification by output tier.

## Normal Run Profiles

- Pocket: `config/pocket.yaml`
- Pocket wildcard exploration: `config/pocket_wildcard.yaml`
- Pokemon TCG: `config/tcg.yaml`
- Pokemon TCG wildcard exploration: `config/tcg_wildcard.yaml`

The wildcard profiles scrape the full decklist and report excluded decks with
enough evidence against the core. They do not change the main MARS ranking.

## Validated Decisions

- Keep the main ranking conservative:
  candidate pool at 80% cumulative share plus iterative NaN filtering.
- Keep wildcard analysis as a separate diagnostic appendix for now, not as an
  automatic promotion mechanism into the main MARS ranking.
- Treat Pocket and Pokemon TCG as different data regimes:
  Pocket is more volatile and NaN-sensitive, while TCG currently has complete
  top-meta coverage and needs more attention to meta relevance than missing
  data.
- Keep TCG standard defaults stable while monitoring future set changes.
- Use `docs/run_observations.md` for short-term validation of live runs,
  especially Pocket `B3b - Everyday Wonders`.
- Treat `PBL - Pitch Black` as the current Pokemon TCG monitored set;
  `CRI - Chaos Rising` is now historical baseline data.
- Pocket wildcard exploration is available as an explicit notebook profile;
  the normal Pocket run remains the default workflow.
- NaN diagnostics stay notebook-only in the `user` output profile; the Excel
  report remains focused on final ranking/matchup results.

## Remaining Pre-Commit Checklist

1. Run `pytest -q`.
2. Search for remaining Italian public text:
   `rg -n "[àèéìòù]" README.md CONTRIBUTING.md MARS_explained.md docs config -i`.
3. Review `git status --short`.
4. Confirm deleted root notebooks were replaced by notebooks under
   `notebooks/`.
5. Regenerate `project_tree.txt` when the file layout changes.
6. Keep generated output, cache, and log files out of the commit.

## Backlog

- Move the repository/workspace out of OneDrive into a local `C:\...` path, then
  keep heavy `outputs/`, cache, and run artifacts on OneDrive through configured
  external paths. This should reduce sync noise and keep the code checkout
  lighter while preserving cloud-backed results.
- Monitor a few more Pocket `B3b - Everyday Wonders` runs and confirm whether
  core size, top 5, and NaN coverage stabilize.
- Monitor `PBL - Pitch Black` with the standard TCG profile and compare its
  early-set behavior against the historical `CRI - Chaos Rising` baseline
  before changing TCG defaults.
- Revisit wildcard thresholds only after collecting more real runs; for now the
  wildcard table is diagnostic output, not ranking logic.
- Review the saved output contract from scratch:
  most generated files are likely redundant, and the project should keep only
  artifacts that are useful for review, reproducibility, debugging, or user
  consumption. See `docs/output_contract.md`.
- Consider moving the output artifact registry out of `pipelines.deck_ranking`
  if profile/routing rules grow further.
- Continue improving notebook diagnostic presentation:
  coverage tables should remain easy to read without inspecting raw CSV files.
- Continue reducing legacy wrappers in `scraper/` and `utils/` once notebook and
  user compatibility risks are low.
- Add focused docstrings around non-obvious areas:
  output routing, set/format resolution, wildcard diagnostics, iterative NaN
  filtering, and MARS report generation.
- Consider a stricter TCG evidence-core rule only after comparing multiple real
  runs over time.
- Add future Limitless pages, such as tournaments or metagame pages, through new
  modules in `sources.limitless.pages` and dedicated pipelines.

## Commit Strategy

Suggested commit grouping:

1. Storage and routing.
2. Limitless sources and compatibility wrappers.
3. Pipeline and CLI.
4. Reporting, notebooks, and Excel output.
5. TCG support and wildcard diagnostics.
6. Documentation, tests, and final cleanup.
