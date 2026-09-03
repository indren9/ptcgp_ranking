# Changelog

## [1.0.1] - 2026-09-03

### Added

- Published `B4 — Ruler of the Skies` as the Latest Completed Pocket Meta from
  the canonical Limitless Tournament API release window
  `[2026-07-30T01:00:00Z, 2026-08-27T01:00:00Z)`.
- Added automatic completed-meta rollover orchestration from expansion-catalog
  refresh through Tournament API acquisition, immutable raw persistence, exact
  OFFLINE replay, Core + MARS, bundle validation, and atomic publication.
- Added vendor-neutral private S3-compatible persistence for canonical raw
  tournament evidence.
- Added a deterministic end-to-end rollover shadow that exercises detection,
  replay, MARS, bundle generation, validation, and staged publication without
  touching the real public snapshot.
- Configured the production private object store with Cloudflare R2 and verified
  GitHub Actions access through a temporary PUT/HEAD/GET/DELETE preflight.

### Changed

- Latest Completed Pocket Meta state is now:
  current `B4a — Team Rocket's Ambition`, completed
  `B4 — Ruler of the Skies`.
- Completed-meta production now derives current and completed sets dynamically
  from the canonical expansion catalog rather than hard-coding set codes.
- The rollover workflow is triggered after a successful expansion-catalog
  workflow on `main`, with manual shadow and production modes also available.
- Production publication is serialized and protected by an exact public-file
  allowlist and a stale-`main` guard.

### Safety

- Acquisition, object-store persistence, replay, MARS, producer, bundle
  validation, regression, or stale-head failure causes no public replacement.
- Canonical raw Tournament API evidence remains private and is never committed
  or included in the public Latest Completed Meta bundle.
- Legacy HTML remains historical validation/rollback material only and is never
  an automatic production fallback.

### Validation

- Canonical B4 acquisition: 105 tournaments, 9,828 participants, 28,352
  pairings, 24,483 comparable matches, and zero surviving selection failures.
- Exact B4 OFFLINE replay: PASS.
- Published B4 MARS ranking: 41 decks.
- D2B full regression before merge: 383 tests passed.
- Post-merge GitHub Actions regression: PASS.
- Manual GitHub Actions rollover shadow: PASS.
- GitHub-hosted runner to production Cloudflare R2:
  PUT/HEAD/GET/DELETE preflight PASS.

## [1.0.0] - 2026-08-28

### Added

- Canonical Limitless Tournament API acquisition for Pokemon TCG Pocket.
- Versioned Pocket release catalog and release-window scoping.
- Immutable, content-addressed raw tournament evidence with exact frozen refs
  and hash validation.
- Deterministic zero-network OFFLINE replay from a frozen LIVE manifest.
- Acquisition and replay progress reporting.
- Canonical dense directional acquisition contracts plus coverage and
  missing-matchup diagnostics.
- Canonical game/format/set output routing.

### Changed

- The Tournament API is now the canonical/default Pocket acquisition source.
- `legacy_html` is retained only as an explicit rollback and historical,
  non-authoritative diagnostic path, with no silent fallback or numerical-
  parity requirement.
- LIVE acquisition always performs fresh `/tournaments` discovery. NEW and
  RECENT (`<72h`) tournaments are fetched fresh; STABLE (`>=72h`) tournaments
  may reuse validated immutable raw.
- The 72-hour operational stability horizon is explicitly separate from both
  an official tournament-ended signal and the HTTP response cache TTL.
- Validated canonical Tournament API dense input uses the canonical dense fast
  path instead of reapplying legacy consolidation.
- Canonical technical deck identity is `deck_id`; `deck_name` remains display
  metadata.

### Fixed

- Resolved the frozen P1 and P4 release findings around dense API contract
  preservation and LIVE freshness semantics.
- Separated HTTP cache freshness from tournament raw-reuse policy.
- Removed the stale standings/pairings reuse assumption and added conservative
  fresh handling for invalid or missing discovery dates.
- Corrected nullable placing, live pairing, and rematch occurrence handling.
- Removed the pandas concat `FutureWarning` in acquisition normalization.

### Performance

Historical B3b OFFLINE benchmark on the frozen benchmark environment:

```text
717.443 s
→ 63.633 s
≈ 11.27x faster
```

This is historical benchmark evidence, not a general runtime guarantee.

### Validation

B13 production evidence:

```text
date: 2026-08-25
set: B4 — Ruler of the Skies
format: Standard

tournaments discovered: 1000
selected: 99

participants: 9546
pairings: 27545

canonical deck identities: 701
dense directional rows: 490700 = 701 * 700

filtered MARS axis: 40
filtered directional score rows: 1560 = 40 * 39

ranking rows: 40
coverage rows: 40
missing pairs: 80

HTTP 429: 3
retries: 3
surviving acquisition failures: 0

P1: PASS
P4: PASS
```

B4 is release-window validation evidence. The published Latest Completed
Pocket Meta remains B3b — Everyday Wonders.
