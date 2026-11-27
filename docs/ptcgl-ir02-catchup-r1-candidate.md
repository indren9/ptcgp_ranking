# MARS P2 — IR-02 · Catch-up catalog candidate R1 (26/11/2026)

**Status:** LOCAL CANDIDATE / NOT CERTIFIED / NOT DEPLOYED.
**Authority:** Andrea GO to prepare a minimal, versioned correction based on PR #37. This does **not** authorize tests/CI, PR #37 merge, main modifications, production acquisition, publication, or changing FROZEN artifacts.
**Git baseline:** `d39c7eca97c8e0eb85fda770085f77fb4a13674b`.
**Source:** `PR #37` head `303439b34d3bdf4a242cb8883ea013351b8880fc`, DRAFT/UNMERGED, Issue #36 (OPEN), Issue #38 architecture standby.

## Minimal change

1. `data/reference/ptcg_live_windows.json`: exact candidate bytes copied from PR #37 with original Git blob `1e3ccbe003355a5a778fcf41582ec435cfc33768`. The file is **modified only in this detached local candidate worktree**, not in main. Its catalog_version and candidate_provenance explicitly identify Issue #36 Gate C.4 branch-only provenance.
2. `scripts/tcg_live_latest_completed.py`: the idempotence decision now compares the complete completed window identity (`code/name/start/end/catalog_version`), not the code alone. The old state from GitHub must **not** be silently treated as matching corrected timestamps.
3. Existing relevant TCG tests updated for 17:00 UTC and a synthetic corrected state on NOOP, while maintaining Pocket historical fixture differences. New `tests/test_tcg_ir02_catalog_catchup_r1.py` proposes chronology, exact-boundary and stale-state checks. **Tests authored, not run.**

Expected sequence (UTC, half-open windows):
- CRI: [2026-05-21T17:00Z, 2026-07-16T17:00Z)
- PBL: [2026-07-16T17:00Z, 2026-09-15T17:00Z)
- 30C: [2026-09-15T17:00Z, 2026-11-05T17:00Z)
- DLR: [2026-11-05T17:00Z, open)

At 26/11/2026 DLR is current, 30C latest completed. This follows the PR #37 reviewed *branch candidate*, **not a production-readiness declaration**.

## Scope and remaining blockers

- **No FROZEN rewrite:** GitHub historical catalog, prior public state, raw, receipts, manifest, hashes, pointers, historical MARS ranking, workflows, YAML and methodology remain untouched; old 00:00 UTC artifacts must be classified and migrated only in new, separately authorized generations.
- PR #37 is much larger (42 changed files): we are *not* cherry-picking its broad publisher/storage/CI changes.
- This candidate addresses **IR-02** and the necessary minimal planner identity check. It **does not** close other independent review findings IR-03–IR-10 (CURRENT stale-plan revalidation, trust/binding, private publisher locking/atomic consumer, etc.).
- Effects on old 17-hour slices and history must be audited before promotion; no forced stale-state publication.
- Candidate should be independently reviewed; targeted offline test authorization, CI, merge, LIVE and publication require their own GO.

**SESSION CLOSE:** Isolated candidate patch only. No test execution or external side effects. WP4/production STOP.
