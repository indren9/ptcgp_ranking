# Pokémon TCG Live production acquisition

## Status

This document records the frozen production contract for the Pokémon TCG
branch of PTCGP Ranking / MARS.

The canonical target is **Pokémon TCG Live Standard**, not physical Standard.

## Canonical acquisition source

- Provider: Limitless
- Source: Tournament API
- Game: `PTCG`
- Format: `STANDARD`
- Channel: online only
- Platform: `PTCGL`
- Public tournaments only
- Decklists required

Legacy HTML is not the canonical production source for this target.

## Eligibility policy

Production policy:

`ptcg_live_standard_v1`

A tournament is eligible only when all required conditions are explicitly
supported by the API record.

In particular:

- `isOnline = true`
- `platform = PTCGL`

If platform is required but absent, the record is classified as
`invalid_record` and excluded fail-closed.

The pipeline must not infer PTCGL merely from `isOnline = true`.

## Window policy

Production uses `latest_completed`.

TCG Live v1 boundaries are:

1. official full-expansion availability on Pokémon TCG Live;
2. official Pokémon TCG Live Standard rotation.

Early availability of individual cards is not a v1 boundary. It may become a
future diagnostic/refinement but does not split production windows in v1.

The dedicated catalog is:

`data/reference/ptcg_live_windows.json`

The catalog currently freezes the boundary required for the first production
window:

- Chaos Rising: `2026-05-21T00:00:00Z`
- Pitch Black: `2026-07-16T00:00:00Z`

Therefore, at the 2026-09-05 integration gate, the latest completed window is:

`[2026-05-21T00:00:00Z, 2026-07-16T00:00:00Z)`

with code `CRI`.

The JSON keeps `boundary_kind` metadata so later catalog updates can represent
both expansion-availability and Standard-rotation boundaries. Runtime reuses
the existing release-chain contract for backward compatibility.

## T4 real shadow validation

Frozen run:

`ptcgl-shadow-cri-20260905T150853Z`

Results:

- selected tournaments: 470
- participants: 31,286
- classified participants: 31,280
- unclassified participants: 6
- classification coverage: 99.9808%
- comparable matches: 67,028
- acquisition failures: 0

Selection exclusions:

- wrong format: 89
- wrong platform: 35
- decklists disabled: 20
- invalid record: 178

All 178 invalid records had missing platform.

- 172 were offline;
- 6 were online with platform absent.

Those 6 remain excluded fail-closed because the source does not prove they
were PTCGL events.

## T4 replay reproducibility

OFFLINE replay passed with zero network access.

The replay reproduced exactly:

- selected tournament IDs;
- classification totals;
- comparable-match total;
- public contract hashes.

Frozen contract hashes:

- `top_meta_decklist`:
  `e87d5975fdf321387264be1a0fe63341c143aa2051cbc3b227f5ccf82947691b`
- `matchup_raw`:
  `3778cb24e1ff19e0e10403b212b420847356ba5f3b8e369d8df47a5bc3b8a3bb`
- `dense_score`:
  `00553e14c9b69ef732d1967c599676ed0dc60ae559604bbd0321f84b391b3aa1`

T4 status:

`PASS / CLOSED / FROZEN`

## HTTP operational policy

Tournament API production uses:

- HTTP cache TTL: 0
- minimum request interval: 4.0 seconds
- discovery page size: 50
- discovery maximum pages: 100
- raw stable-snapshot reuse: enabled under the existing RF1 rules

The 4-second interval is an operational pacing value validated during T4. It
is not claimed to be an official Limitless quota.

Transport connection errors and timeouts use the existing bounded retry /
exponential-backoff behavior.

## Core and MARS

The acquisition layer changes only the source and scope selection.

Downstream Core contracts remain unchanged:

- `score_latest.csv`
- `filtered_wr_latest.csv`
- `n_dir_latest.csv`

MARS remains source-agnostic.

Frozen MARS lower-bound penalty:

`Z_PENALTY = 1.96`

No TCG-specific MARS retuning is introduced by this integration.

## First Core + MARS production validation

The first full production-path validation used the frozen T4 raw dataset via
OFFLINE replay:

`ptcgl-shadow-cri-20260905T150853Z`

The run exercised:

`Tournament API replay -> production bridge -> Core -> NaN filter -> contracts -> MARS`

Network access was explicitly forbidden during the replay.

Observed acquisition result:

- acquisition source: `tournament_api`
- execution mode: `offline`
- network calls: `0`
- T4 contract hashes: identical to the frozen LIVE run

Core result:

- retained axis: 27 decks
- score rows: 702
- expected cardinality: `27 * 26 = 702`
- WR matrix: `27 x 27`
- n_dir matrix: `27 x 27`
- WR diagonal: NaN
- n_dir diagonal: NaN
- WR directional symmetry maximum error: `0.0`
- no mirror rows: PASS
- `N = W + L + T`: PASS
- unique directional pairs: PASS
- `n_dir = W + L`: PASS
- W/L/T directional symmetry: PASS

`WR_dir` is contractually rounded to two decimal places. Validation against
the unrounded mathematical value can therefore differ by at most 0.005
percentage points. The observed maximum difference was
`0.005000000000002558`; validation against the two-decimal contractual value
had maximum error `0.0`.

MARS result:

- ranking rows: 27
- `K_used = 28.91366458960192`
- all 27 ranked decks used all 26 opponents
- coverage: 100% for every ranked deck

Top three by `Score_%` in this validation run:

1. `dragapult-ex` — 92.640398
2. `crustle-dri` — 92.425948
3. `dragapult-blaziken` — 90.564790

Validation status:

`PASS`

## Current LIVE operational validation

A subsequent production LIVE run was executed with the canonical TCG Live
configuration.

Run:

`limitless-api-live-20260905T213244704558Z`

Result:

- acquisition source: `tournament_api`
- execution mode: `live`
- selected tournaments: 469
- classified participants: 31,256
- unclassified participants: 6
- comparable matches: 66,975
- acquisition failures: 0
- Core retained axis: 27 decks
- score rows: 702 = `27 * 26`
- MARS ranking rows: 27
- minimum coverage: 100%
- `K_used = 28.91366458960192`

The ranking remained materially stable relative to the frozen T4 validation.

### Mutable LIVE source rule

Limitless Tournament API data may change retroactively.

A LIVE run is therefore authoritative for the data exposed by Limitless at
that acquisition timestamp. A later LIVE run is not required to reproduce the
contract hashes of an earlier LIVE run.

Exact reproducibility of a historical run is guaranteed by its frozen raw
snapshots, manifest and OFFLINE replay.

During this validation, T4 selected 470 tournaments and T5 selected 469.
One previously selected tournament disappeared from the fresh Limitless
discovery:

`6a0f640adfbdf089cbbdea7f`

The removed tournament accounted exactly for:

- 24 fewer participants
- 24 fewer classified participants
- 55 fewer pairings
- 53 fewer comparable matches

The fresh discovery catalogs also showed broader retroactive mutation:
6 tournament IDs disappeared and 5 appeared between T4 and T5, including
records with historical dates.

This behavior is treated as upstream source mutability, not as a production
pipeline failure.

### Runtime note

The observed wall-clock duration of approximately 11.5 hours is not treated
as a production runtime benchmark. The run spanned an overnight system
suspension interval. Raw fetch timestamps and rate-limit observations show
that the process resumed and completed successfully after wake.

Validation status:

`PASS`

## Automatic latest-completed operation

TCG Live uses a dedicated automation channel. The existing Pocket
latest-completed automation remains unchanged.

The TCG Live controller uses:

- catalog: `data/reference/ptcg_live_windows.json`
- state: `.github/tcg-live-latest-completed-meta-state.json`
- future public target: `public/tcg-live/latest-meta/`

The controller resolves `latest_completed` from the official TCG Live
boundary catalog.

Decision contract:

- if the resolved completed window matches the published state: `noop`
- if the resolved completed window changes: `publish_required`

The initial state is frozen to CRI
`[2026-05-21, 2026-07-16)`, because this window has already passed the
LIVE, OFFLINE, Core and MARS production validation gates.

The scheduled GitHub Actions publication is intentionally introduced only
after the complete TCG Live LIVE -> raw persistence -> OFFLINE replay ->
Core -> MARS -> publication path has been validated. This prevents a
partially implemented automation from becoming active.

## Update rule

When a new official TCG Live expansion or Standard rotation becomes a
production boundary:

1. verify the official TCG Live date;
2. append/update the dedicated boundary catalog;
3. preserve the half-open chain;
4. run tests;
5. validate the new latest-completed scope;
6. only then allow production rollover.

Do not derive a TCG Live date from a physical release-date offset.
