# Changelog

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
