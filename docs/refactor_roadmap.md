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
- Added TCG configs and verified a TCG run on `CRI - Chaos Rising`.
- Added wildcard diagnostics for TCG full-scrape exploration.
- Added reporting tables for share distribution, coverage, wildcard review, and
  evidence-core simulations.
- Added a notebook scope summary that separates full decklist/fetch rows from
  the effective MARS core rows, especially for TCG wildcard runs.
- Stabilized output naming around unprefixed `*_latest` files inside scoped
  folders.
- Added local Pytest base temp through `.pytest_tmp`.

## Normal Run Profiles

- Pocket: `config/config.yaml`
- Pokemon TCG: `config/config_tcg.yaml`
- Pokemon TCG wildcard exploration: `config/config_tcg_wildcard.yaml`

The wildcard profile scrapes the full decklist and reports excluded decks with
enough evidence against the core. It does not change the main MARS ranking.

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
