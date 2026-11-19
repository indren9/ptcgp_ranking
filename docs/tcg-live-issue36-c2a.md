# Issue #36 Gate C.2A â€” branch-only canonical window correction

Authorization: https://github.com/indren9/ptcgp_ranking/issues/36#issuecomment-6561406638

Starting PR #37 HEAD: `fc15b26d6c2253987ecd1941c93977c6ea8bf74a`.
This change is limited to the candidate branch; it is not deployment or gate closure.

## Canonical path on the candidate branch

`data/reference/ptcg_live_windows.json` uses version
`tcgl-expansion-boundaries-2026-11-01-issue36-c2a-pre-dlr-v1`.

| Code | Included start UTC | Excluded end UTC | Snapshot flag |
| --- | --- | --- | --- |
| CRI | 2026-05-21 17:00 | 2026-07-16 17:00 | completed |
| PBL | 2026-07-16 17:00 | 2026-09-15 17:00 | completed |
| 30C | 2026-09-15 17:00 | open | current |

The old catalog used midnight for CRI/PBL and left PBL current/open.
The new canonical-path catalog is a new identity, not a reinterpretation of old
runs, state or FROZEN evidence. At 2026-11-01 the planner selects PBL.
At 2026-09-06 it still selects CRI, but with corrected boundaries and version;
the preserved old CRI publication state therefore requires a new candidate.

The runtime loader, release-chain invariants, tournament-date selector, policy
IDs, YAML, Core I/O and MARS methodology are unchanged. Boundary membership
continues to use the tournament date and a half-open interval; pairings are
not independently truncated at the release hour.

## Official provenance and C.1

Full-expansion availability (not physical release, ladder or trial dates):

- CRI: https://community.pokemon.com/en-us/discussion/24085/mega-evolution-chaos-rising-expansion-release
- PBL: https://community.pokemon.com/en-us/discussion/24856/mega-evolution-pitch-black-expansion-release
- 30C: https://community.pokemon.com/en-us/discussion/26004/pokemon-tcg-30th-celebration-expansion-release
- DLR announcement: https://community.pokemon.com/en-us/discussion/26743/mega-evolution-delta-reign-expansion-release

C.1 archive SHA-256:
`c06d855104a4348821cb9e37e391288d1c29f12ee690431201b23d2575d71099`.
All 31 entries match its `DELIVERY_SHA256.json`.
The canonical file records the prior production blob, baseline revision,
official URLs, supporting FROZEN blob and C.1 proposal hash.
The historical approved catalog and B1 candidate retain their original bytes.

## Delta Reign separation

The canonical snapshot contains DLR only as planned provenance metadata.
Its `releases` array contains CRI, PBL and 30C, with 30C OPEN/current.

`data/candidates/releases/tcg_live_windows_issue36_c2a_post_dlr_proposal.json`
is a **non-runtime envelope** retaining the C.1 post-DLR proposal under
`proposed_catalog`. It deliberately has no top-level `releases`.
The unmodified loader and planner reject this file at every time, before
release selection. This is the safeguard; a declarative not-before field
alone is not treated as enforcement. No automatic activation code is added.

Tests explicitly extract the proposal into a temporary fixture only.
They verify that, with that synthetic catalog, 30C becomes latest-completed
at 2026-11-05 17:00 UTC (not midnight), while DLR is the last/current row.
They do not certify that loading the extracted payload before that instant is
safe: the existing loader would display DLR current prematurely.

Future operational replacement requires a separate authorization, actual UTC
time check at or after 2026-11-05 17:00 UTC, review and deployment gate.
The pre-DLR snapshot remains 30C current/PBL latest-completed even after that
instant. It does **not** enforce expiry or freshness; if not replaced it is
stale. The review timestamp in provenance is informational, not an automatic
rollover.

## Offline verification and remaining gates

`tests/test_tcg_live_issue36_c2a.py` exercises canonical chain/provenance,
midnight and +/-1 microsecond boundaries, real selector membership, preserved
stale state, exact identity and catalog-byte invalidation, no automatic DLR,
and runtime rejection of the post-DLR envelope. Sockets are blocked in these
tests. Existing B3 and job tests cover config/revision invalidation, corrupt
receipts, locks, recovery and unpublished-candidate reuse with no backend/LIVE.

Historical rebuild CRI/PBL remain valid only in their own verified scope.
No historic generation is converted into a production receipt here. C.2B must
establish compatible scope/configuration, provenance, manifests/hashes, new
lineage and final artifact QA offline before considering productive reuse.
The current `publish=True` job bypasses unpublished receipts; this change
does not provide or claim publication without reacquisition.

No merge, LIVE acquisition, real historical rebuild, public write, deployment,
scheduler/launcher change, public pointer edit, Pocket change, FROZEN mutation,
production YAML or methodology change is included. Tests alone do not close C.2:
independent targeted review and Andrea's later gates remain necessary.

## Exact-byte identities and local QA evidence

- Old canonical catalog from starting Git commit (LF):
  `6fe7c9697452cf4a61daf728746d6d7b2b3971d125467f580fd5421fe3734a2e`.
- New canonical catalog (LF):
  `569a52ed53976eda756086b77f5e5af98ada9862926b9a1a30a6267ea9caf094`.
- Inactive post-DLR envelope (LF):
  `3c1aace2427fff5dd4ae25610172736070a9331c2b1b4f3617dc56994e895891`.
- Supporting historical FROZEN catalog, unchanged exact bytes:
  `1f2176f85a9cab0d33d14c5c7520944b4e5ee0e6355ac2e7e0edcaf548d78215`.

Receipt identities use actual local bytes. LF/CRLF differences in checkout
catalogs or YAML invalidate byte identity; never normalize an old input just
to obtain a receipt hit. The B1 candidate is preserved, including checkout
line endings, rather than rewritten to match a different byte digest.

QA uses Python 3.14.7 in the existing ranking environment and separate
`C:\dev\Pokemon\c2a-qa-20261101` temporary output roots.
The initial targeted run passed 87 tests (exit 0). After extending the DLR
selector edge case, the compatibility run passed 174 tests (exit 0), covering
the C.2A/B3 planner/receipt/job cases, release selection, Acquisition,
Core matrices and pipeline contracts. `git diff --check` returned exit 0.

A separate readback of three synthetic TCG output CSVs verified a 2x2 common
axis, NaN diagonals, two unique opposite directional pairs, `N=W+L+T`,
`n_dir=W+L` and `WR(A,B)+WR(B,A)=100`. SHA-256:

- matchup scores: `788004c4a58cc294247204291be0e6e5694c4a2578c6ce50903557db6f7cfd5a`
- WR matrix: `d6c831eba5217f1859dadaab7934337cebe54adb55fd60901c2d34d433b35582`
- n_dir matrix: `efe58c3f462955339aa53a261f007e56e11c922b03415b4ad56998eac8e9d8c5`

This is a mocked pipeline fixture, not a real corrected-window rebuild or
scientific recertification. Raw outputs and the machine-readable verification
are retained in the local QA directory. All 53 tracked protected
config/FROZEN/public/state/Core/MARS/Pocket files checked against their starting
exact-byte hashes remained unchanged.

### Full native Windows suite: limitations, not PASS

The complete suite finished with **1562 passed, 184 skipped, 50 failed,
6 warnings**, exit **1** (352.22 seconds). The 50 failures are confined to:

- 31 cases in unchanged `tests/test_f08r18_pocket_config.py`: the existing
  snapshot helper accesses `os.stat_result.st_rdev`, unavailable on this
  Windows interpreter, before the assertions can complete.
- 19 cases in unchanged `tests/test_reviewed_historical_boundaries.py`:
  the unchanged historical candidate input is checked out with CRLF bytes
  and fails its original frozen LF SHA-256 binding. Some negative tests
  consequently encounter that earlier checksum failure instead of the
  intended later rejection.

No failure occurred in C.2A, B3 or the corrected TCG integration tests.
These are concrete causes in unchanged files, not a successful rerun of the
entire pre-change baseline. No Pocket portability fix, newline normalization
of historical inputs, FROZEN rewrite or test suppression is included.
Native symlink-dependent skips also limit coverage; full Windows certification
and an independent gate assessment are not claimed.

Logs: `targeted.log`, `compatibility.log`, `full.log` and
`artifact_verification.json` under the local QA directory above.
The existing Linux PR CI is a separate check; it does not promote this branch.
