# Automatic catalog freshness

Live runs automatically ensure catalog freshness. The CLI and
[`notebooks/live_ranking.ipynb`](../notebooks/live_ranking.ipynb) call the shared
`run_deck_ranking` pipeline. Notebook users select a game and choose **Run All**;
there is no catalog-refresh switch. The selected production profile controls
acquisition, analysis, and local outputs. OneDrive publication remains part of
the production CLI/job wrapper, not the notebook.

## Runtime contract

The shared live catalog preflight reuses a validated catalog younger than
60 minutes. An absent or older catalog triggers a fetch. The fetched rows must
pass validation before a temporary file can atomically replace the cache.
Game, format, and relevant rotation scope identify the cache.

If refresh fails and a valid previous catalog exists, that cache remains
unchanged and the run continues with `CATALOG_REFRESH = FALLBACK_LKG` at WARNING.
The diagnostic includes the game/scope, cache age, last successful refresh time,
and error class. Without a valid previous catalog the run reports
`CATALOG_REFRESH = FAIL_CLOSED` and stops. Ordinary INFO diagnostics identify
`FRESH_CACHE` and `REFRESHED` without dumping catalog contents.

The live freshness limit is centrally owned. The retained legacy runtime
expansion resolver also calls this central contract, without a second Selenium
or output-directory AUTO fallback. Low-level adaptive TTL helpers remain only
for compatibility; their settings cannot extend the automatic runtime limit.
Ancillary all-format batch discovery retains its existing behavior and is not
part of the canonical ranking runtime. Tournament-response caching and frozen raw-evidence reuse
remain separate policies. OFFLINE, replay, frozen inputs, and no-acquisition
rebuilds do not acquire a dependency on today's catalog or refresh the network.

Pocket AUTO first establishes fresh-enough runtime expansion metadata. Its
selected code must also have an approved entry in the versioned Pocket release
catalog before acquisition can use the corresponding tournament window. A newly
discovered code without a canonical release window fails closed with a clear
diagnostic; discovery never invents release timestamps or modifies methodology.
Explicit CODE selection remains explicit.

TCG Live's production path uses `data/reference/ptcg_live_windows.json` and its
`latest_completed` policy. It does not consume generic Limitless expansion
metadata, so this feature adds no generic catalog request to that path and never
automatically changes canonical TCG windows. The independent public catalog
workflow and `public/expansions_pocket_standard.csv` retain their existing contract.

## Audit of the pre-change main

The audit used main `374db3e23e3be7ee1c21493d361c375eb9295299`.

| Question | Observed behavior before this change |
| --- | --- |
| A. Pocket resolution | The default Tournament API path read `data/reference/pocket_releases.json` through `_api_catalog_context` / `resolve_release`. Generic runtime discovery existed in the legacy HTML path. |
| B. Cache reads/writes | `sources/limitless/pages/sets.py` read and directly wrote expansion/format JSON caches. The canonical release catalogs were versioned JSON inputs rather than those caches. |
| C. TTL | Generic discovery used an adaptive nominal seven-day TTL, shorter end-of-month and burst intervals, and jitter; AUTO could explicitly force another refresh. |
| D. `force_refresh` | The scraping setting affected retained HTML acquisition. It did not establish freshness of the Tournament API release catalog. |
| E. Pocket CLI | The CLI entered the shared production pipeline on every run, but the default Tournament API dispatch did not call generic expansion discovery. |
| F. Notebook | Main tracked no notebooks after the legacy-notebook quarantine. Historical local notebooks called a shared pipeline from their own checkout; those files were not edited. |
| G. TCG generic metadata | `scripts/tcg_live_latest_completed_job.py` planned from the canonical TCG release-window catalog and did not consume generic set discovery. |
| H. TCG context | Release/window selection used `ptcg_live_windows.json`; tournament evidence still came from the Tournament API. |
| I. Cache identity | The config-aware expansion path included game and format. Low-level defaults and legacy envelopes did not provide complete explicit game/format/rotation identity. |
| J. Network failure | Generic discovery returned previous rows, often with DEBUG-only diagnostics; absent data could fall through to Selenium or output-directory inference. It did not provide the new validated LKG/fail-closed contract. |

The new notebook is a small supported entry point in the feature checkout.
Quarantined filenames, historical user notebooks, scheduler definitions,
launchers, and production routing remain outside this change.

## Gate 1.10 qualification

The canonical Python 3.14.7 environment passed 515 tests (baseline: 434),
with zero failures; `pip check` passed. Existing plot deprecation warnings remain.

The isolated Pocket shadow fetched the real Limitless catalog (20 entries,
current B4a), then used deterministic Tournament API fixtures through the real
acquisition, bridge, Core, MARS, heatmap, and Excel paths. The notebook's code
cells ran the same pipeline in that isolated root and reused the fresh cache:
one HTTP catalog request across `REFRESHED` and `FRESH_CACHE` runs. Both produced
three ranking rows. This qualifies live catalog integration, not a full live
tournament-evidence acquisition.

The actual TCG job was exercised with an isolated work root and publication
disabled: the already-published CRI window returned NOOP with zero network calls.
No Windows Scheduled Task was run and no canonical OneDrive artifacts were written.
