# Run-scoped deck display labels

The acquisition resolver in `acquisition/deck_labels.py` implements
`latest_tournament_utc_lexicographic_v1`. Canonical `deck_id` remains the
computational identity for aggregation, mirror exclusion, Core axes and MARS.

Normalized participant observations retain their original `deck_name` values.
The resolver consumes a separate tournament frame (`tournament_id`, `date`);
`PARTICIPANT_COLUMNS` is unchanged. Existing stripped-text normalization applies.

## Resolution

For each ID, within the supplied run observations:

1. No non-empty name: use the ID.
2. One non-empty name: use that name, even alongside unnamed observations.
3. Multiple names: select the name observed at the latest tournament instant.
4. Tied latest instants: select the minimum Python string by Unicode code points,
   case-sensitive and independent of locale, without casefolding or Unicode
   normalization.

Timezone-aware dates are converted to UTC. Missing, invalid, naive or conflicting
tournament dates for named observations of a drifting ID fail closed. Those
observations could change which label is latest, so they cannot be discarded.
Unnamed observations do not compete. Dates are not required for a fallback or a
single observed name. Equivalent duplicate UTC dates are unambiguous.

The date is a deterministic evidence-selection criterion, **not a source rename
date**. The resolver never reads a current catalog, external source or another
historical window.

## Integration and evidence

Live and offline acquisition pass their normalized tournament frame to the
shared resolver through aggregation. Historical normalization retains the same
frame from `normalize_snapshot` instead of discarding it. Source frames are not
rewritten; only downstream display labels are resolved.

Existing acquisition manifest/diagnostics `deck_identity_diagnostics` gains a
`label_resolution` entry. Historical NORMALIZATION results carry the same entry.
It contains the policy/comparator, sorted fallback IDs and sorted drift evidence:
observed labels, counts, UTC extrema, tournament IDs, latest tournament IDs,
unnamed count, selection and tie-break details. No player identities or new
global registry are introduced.

The production bridge validates consistency and emits one mapping row per ID;
distinct IDs may share a display label. Publication rejects conflicting mappings
instead of using the last row. It retains its existing collision-safe presentation.

## Invalidation

The resolver file is explicitly fingerprinted at NORMALIZATION. The integration
also changes that stage's adapter and acquisition implementation hashes.
No new compatibility rule is provided.

Comparison with baseline `cc049ee92efb5c83608db7c5558cae54e5a2762b` under the
same runtime/configuration changes direct semantics only at NORMALIZATION.
Discovery, Tournament IDs Frozen and RAW retain their semantics. Existing
dependency fingerprints invalidate downstream stages; the seven prior DONE
windows are not artificially kept current under the new implementation.
Their original generations and checksummed states remain historical evidence.

This implementation does not authorize a historical rerun, promotion,
publication or replacement of any generation. Synthetic tests call only
fixture-backed paths in temporary directories.

## Validation coverage

- Zero/one/multiple labels, unnamed observations, UTC equivalence and lexical ties.
- Missing/invalid/ambiguous required dates and deterministic row/tournament order.
- Unchanged directional W/L/T/N, matchup cardinality and exclusions.
- Distinct canonical IDs with a shared label, technical matrices and NaN diagonals.
- One-row-per-ID bridge mapping and rejection of downstream conflicts.
- Fixture-only live/replay/historical consistency and run-scope isolation.
- Stage-specific fingerprint coverage and existing regression suites.

## Implementation verification (2026-10-06)

The read-only historical status check on the implementation branch verified
2,166/2,166 stored RAW artifacts across the eight started windows. SVI, PAL, OBF,
MEW, PAR, PAF and TEF report STALE starting at NORMALIZATION; TWM remains BLOCKED
at NORMALIZATION with 356/356 valid RAW artifacts. The eight persisted
`state/current.json` files were byte-identical before and after inspection.
The other fourteen windows were not started. No historical execution or
publication was performed.

Terminal evidence records one preliminary focused validation before the final
authoritative runs. After an earlier pytest attempt using the default temporary
directory failed with a Windows `PermissionError` on `pytest-of-visen`, a
repository-local `--basetemp` was used without changing system permissions. That
preliminary focused suite passed 117 tests; it is retained here only as provenance
and is not the final validation result.

The final authoritative focused suite passed 139 tests in 68.42 s with exit code 0.
The final full suite passed 1,080 tests with 6 warnings in 265.75 s with exit code 0.
The warnings were `PendingDeprecationWarning` instances from the reporting plot
stack and do not change the deck-label resolver validation result.
