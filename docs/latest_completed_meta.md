# Latest completed meta

The repository exposes at most one completed-meta snapshot. It is a living
example of the MARS pipeline, not a public historical archive.

## Selection rule

The Pocket Standard expansion catalog is sorted with the same natural set-code
ordering used by the catalog exporter. The last entry is the current set and
the penultimate entry is the latest completed meta:

```text
catalog: [..., B3b, B4]
current: B4
completed: B3b
```

`scripts.latest_completed_meta plan` compares that completed set with
`.github/latest-completed-meta-state.json`. It returns `publish` only when the
public snapshot is missing, a new current set has appeared, or an operator has
requested a manual override. Historical rows inserted before the end of the
catalog do not cause a republication.

## Public contract

Only the current snapshot is kept under `public/latest-meta/`:

```text
public/latest-meta/
├── heatmap.png
├── ranking.csv
└── manifest.json
```

Publishing overwrites these three paths and the content between the invisible
`latest-completed-meta` markers in `README.md`. There are no per-set folders and
no public archive navigation. Previous versions remain part of normal Git
history, as with any committed repository file.

## Producer/publisher boundary

The ranking producer is intentionally separate from the publisher. This lets
the presentation rules evolve without changing set detection or repository
state.

`scripts.latest_completed_meta_producer` rebuilds MARS from a saved aggregate
run, reconciles the result against that run's ranking, and creates a temporary
bundle containing:

| File | Contract |
| --- | --- |
| `fragment.md` | Complete Markdown shown in the README block |
| `heatmap.png` | Non-empty heatmap referenced by the fragment |
| `ranking.csv` | Non-empty normalized ranking used for the table |
| `manifest.json` | Metadata whose `set.code` matches the publication plan |

The producer stops if the set/format scope does not match the plan or if the
regenerated deck order or numeric ranking differs from the source run. The
heatmap is then generated from the same recomputed core as `ranking.csv`.

The public ranking preserves the native MARS columns:

```text
Rank, Deck, Score_%, MAS_%, LB_%, BT_%, SE_%, N_eff,
Opp_used, Opp_total, Coverage_%
```

Percent fields use four decimal places in the downloadable CSV and two decimal
places in the compact README Top 10. The README table shows Score, MAS, LB, BT,
and coverage; the manifest also records core size, decisive match volume,
coverage min/median/max, effective MARS parameters, input hashes, config hash,
and the code revision used for reproduction.

The heatmap shows the top 10 decks in ranking order. Rows are the deck being
evaluated and columns are opponents. A diverging palette is centered at 50%,
the visible scale is clipped at 20–80% to keep competitive differences legible,
and annotations retain the actual values when a cell falls outside that range.
Blank cells are mirror matchups or missing observations.

## Commands

Preview the automatic selection without changing tracked files:

```bash
python -m scripts.latest_completed_meta plan \
  --catalog public/expansions_pocket_standard.csv \
  --output latest-meta-plan.json
```

Select a specific completed set for initial publication or recovery:

```bash
python -m scripts.latest_completed_meta plan \
  --force-completed-set B3b \
  --output latest-meta-plan.json
```

Build a bundle from an existing aggregate run without making network requests:

```bash
python -m scripts.latest_completed_meta_producer \
  --plan latest-meta-plan.json \
  --source-run outputs/POCKET/standard/B3b__Everyday_Wonders \
  --config config/pocket.yaml \
  --bundle build/latest-meta \
  --acquired-on 2026-07-15
```

The acquisition date must be taken from the verified run record. If the saved
inputs do not encode an exact tournament date window, the manifest says so
instead of inferring one.

Validate the prepared bundle without publishing it:

```bash
python -m scripts.latest_completed_meta publish \
  --plan latest-meta-plan.json \
  --bundle build/latest-meta \
  --dry-run
```

Removing `--dry-run` replaces the single public bundle, updates the README
block, and records the new state only after the bundle has passed validation.

## Data and attribution

Only aggregate deck-archetype statistics are published. Raw player records,
usernames, decklists, pairings, cookies, credentials, and local paths are not
part of the bundle.

The snapshot credits [Limitless TCG](https://limitlesstcg.com/) and links the
official [developer guide](https://docs.limitlesstcg.com/developer) and
[terms of service](https://play.limitlesstcg.com/tos). The project does not
claim a license for Limitless data and does not imply affiliation, endorsement,
or sponsorship.

## Automation boundary

The workflow remains deliberately manual and read-only. It proves catalog
selection and exposes the plan in the Actions summary. The producer and the
static snapshot can be reviewed locally, but no producer or publishing job is
connected to GitHub Actions until the ranking fields, summary metrics, and
heatmap presentation are explicitly approved.

The final automated sequence will be:

```text
refresh catalog -> plan -> produce bundle -> validate -> publish -> commit
```

If production or validation fails, the state file is not advanced, so the same
completed set can be retried safely.
