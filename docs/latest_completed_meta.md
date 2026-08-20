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

The future producer must create a temporary bundle containing:

| File | Contract |
| --- | --- |
| `fragment.md` | Complete Markdown shown in the README block |
| `heatmap.png` | Non-empty heatmap referenced by the fragment |
| `ranking.csv` | Non-empty normalized ranking used for the table |
| `manifest.json` | Metadata whose `set.code` matches the publication plan |

The manifest may later gain metrics such as match count, coverage, collection
window, MARS parameters, and source hashes. Those are presentation/data-policy
decisions and are not required by the publication state machine.

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

Validate a prepared bundle without publishing it:

```bash
python -m scripts.latest_completed_meta publish \
  --plan latest-meta-plan.json \
  --bundle build/latest-meta \
  --dry-run
```

Removing `--dry-run` replaces the single public bundle, updates the README
block, and records the new state only after the bundle has passed validation.

## Automation boundary

The initial workflow is deliberately manual and read-only. It proves catalog
selection and exposes the plan in the Actions summary. Once ranking fields and
heatmap presentation are approved, a producer job can be inserted between
`plan` and `publish`, and the workflow can inherit the daily catalog schedule.

The final automated sequence will be:

```text
refresh catalog -> plan -> produce bundle -> validate -> publish -> commit
```

If production or validation fails, the state file is not advanced, so the same
completed set can be retried safely.
