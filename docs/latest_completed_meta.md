# Latest completed meta

The repository exposes at most one completed-meta snapshot. It is a living
example of the MARS pipeline, not a public historical archive.

## Current published state

At v1.0.1:

```text
current Pocket Standard set:
B4a — Team Rocket's Ambition

latest completed Pocket Standard meta:
B4 — Ruler of the Skies
```

The repository state is recorded in:

```text
.github/latest-completed-meta-state.json
```

The state advances only after a complete successful publication.

## Selection rule

The canonical Pocket Standard expansion catalog is ordered by the same natural
set-code ordering used by the catalog exporter.

The automatic rule is:

```text
current set   = last catalog entry
completed set = penultimate catalog entry
```

For example:

```text
catalog tail: [..., B4, B4a]
current:      B4a
completed:    B4
```

`scripts.latest_completed_meta` compares the derived completed set with
`.github/latest-completed-meta-state.json`.

A historical row inserted before the catalog tail does not cause a rollover.
The completed/current pair must also satisfy the canonical release-window
adjacency gate from `data/reference/pocket_releases.json`.

## Canonical acquisition boundary

Completed Pocket metas are produced from the Limitless Tournament API only.

The production path does not silently fall back to legacy HTML.

For the derived completed set, the release window is:

```text
release_datetime(completed)
<= tournament.date
< release_datetime(current)
```

LIVE acquisition freezes the selected tournament evidence and its manifest.
The exact frozen evidence is then restored and replayed OFFLINE with zero
network calls before Core + MARS runs.

## Private raw persistence

Canonical raw Tournament API evidence may contain player identifiers and is
therefore private.

Production uses the repository's vendor-neutral S3-compatible object-store
backend. The current deployment is backed by a private Cloudflare R2 bucket.

The workflow receives storage credentials only through GitHub repository
secrets. Raw evidence is never committed to Git and is not included in the
public Latest Completed Meta bundle.

The private store is the persistence layer between ephemeral GitHub-hosted
Actions runners. Canonical evidence is validated before the run manifest is
promoted.

## Public publication contract

A successful rollover may modify only these tracked publication paths:

```text
README.md
.github/latest-completed-meta-state.json
public/latest-meta/ranking.csv
public/latest-meta/heatmap.png
public/latest-meta/manifest.json
```

The public snapshot itself remains:

```text
public/latest-meta/
├── heatmap.png
├── ranking.csv
└── manifest.json
```

There are no public per-set archive folders. Previous published snapshots remain
available through normal Git history.

Only aggregate deck-archetype statistics are published. Raw player records,
usernames, pairings, credentials, cookies, private object-store references, and
local paths are not part of the public bundle.

## Producer and publisher boundary

The ranking producer remains separate from publication.

`scripts.latest_completed_meta_producer` receives the completed-set plan, the
reconstructed source run, and the canonical Tournament API acquisition
manifest. It rebuilds the MARS-facing public outputs and creates a temporary
bundle containing:

| File | Contract |
| --- | --- |
| `fragment.md` | Complete Markdown shown in the README block |
| `heatmap.png` | Non-empty heatmap referenced by the fragment |
| `ranking.csv` | Non-empty normalized ranking used for the table |
| `manifest.json` | Public provenance whose set matches the publication plan |

The producer stops if the source scope, release window, acquisition provenance,
deck labels, ranking reconciliation, or public privacy contract is invalid.

The public ranking preserves the native MARS columns:

```text
Rank, Deck, Score_%, MAS_%, LB_%, BT_%, SE_%, N_eff,
Opp_used, Opp_total, Coverage_%
```

Percent fields use four decimal places in the downloadable CSV and two decimal
places in the compact README Top 10.

The heatmap shows the top 10 decks in ranking order. Rows are the deck being
evaluated and columns are opponents. Blank cells represent mirror matchups or
missing observations.

## Automatic GitHub Actions rollover

The production workflow is:

```text
Update public expansion catalog
        ↓
successful workflow_run on main
        ↓
derive current + completed set
        ↓
derive exact release window
        ↓
restore useful prior private raw when available
        ↓
Tournament API LIVE acquisition
        ↓
validate + persist canonical raw privately
        ↓
restore current run into a fresh replay root
        ↓
exact OFFLINE replay
        ↓
Core + MARS
        ↓
produce bundle
        ↓
validate publication + privacy
        ↓
enforce exact tracked-file allowlist
        ↓
full regression
        ↓
abort if origin/main advanced
        ↓
one atomic publication commit to main
```

The workflow also supports manual `workflow_dispatch` modes:

```text
shadow
production
```

`shadow` is the safe validation mode. It exercises the rollover path without
modifying the real `public/latest-meta`.

Manual `production` is allowed only from `main`.

The workflow uses a single concurrency group with
`cancel-in-progress: false`, so a new rollover cannot cancel another run during
its publication sequence.

## Fail-safe behavior

The rollover is fail closed.

Any failure in acquisition, private persistence, OFFLINE replay, Core + MARS,
producer execution, bundle validation, privacy validation, regression, or the
stale-main guard means:

```text
NO PUBLICATION
NO STATE ADVANCE
NO README REPLACEMENT
NO public/latest-meta REPLACEMENT
```

The previously published Latest Completed Meta remains intact and the same
completed set can be retried safely.

A missing prior raw snapshot is a valid cold-start case and permits fresh LIVE
acquisition. An actual object-store authentication, availability, or I/O
failure is not treated as a cache miss and fails closed.

## Local planning and validation

Preview the automatic set-selection decision without publishing:

```bash
python -m scripts.latest_completed_meta plan   --catalog public/expansions_pocket_standard.csv   --output latest-meta-plan.json
```

Bundle publication logic can still be validated locally in dry-run mode:

```bash
python -m scripts.latest_completed_meta publish   --plan latest-meta-plan.json   --bundle build/latest-meta   --dry-run
```

Normal production publication is performed by the GitHub Actions rollover
workflow rather than by manually editing the public snapshot.

## Data and attribution

The snapshot credits [Limitless TCG](https://limitlesstcg.com/) and links the
official [developer guide](https://docs.limitlesstcg.com/developer) and
[terms of service](https://play.limitlesstcg.com/tos).

The project does not claim a license for Limitless data and does not imply
affiliation, endorsement, or sponsorship.
