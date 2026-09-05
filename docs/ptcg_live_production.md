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
