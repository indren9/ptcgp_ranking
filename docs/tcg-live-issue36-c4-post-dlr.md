# Issue #36 Gate C.4 — post-Delta Reign branch-only catalog

Status: CANDIDATE IMPLEMENTATION; independent review and promotion pending.
Authorization: https://github.com/indren9/ptcgp_ranking/issues/36#issuecomment-6713318351
Baseline PR #37 HEAD: `3ece96dfe82279f4e780f1aba4031ba29e138211`.
Branch: `fix/issue36-b1-tcg-live-boundary-candidate`.
Only the candidate branch is in scope; not main or deployed production.

## New runtime catalog on the candidate branch

Canonical-path file: `data/reference/ptcg_live_windows.json`.
Version: `tcgl-expansion-boundaries-2026-11-07-issue36-c4-post-dlr-branch-v1`.

| Code | Included start (UTC) | Excluded end (UTC) | State |
| --- | --- | --- | --- |
| CRI | 2026-05-21 17:00 | 2026-07-16 17:00 | completed |
| PBL | 2026-07-16 17:00 | 2026-09-15 17:00 | completed |
| 30C | 2026-09-15 17:00 | 2026-11-05 17:00 | completed |
| DLR | 2026-11-05 17:00 | open | current |

At and after 2026-11-05T17:00:00Z, `latest_completed` selects **30C**, while
`auto` selects the released **DLR** window. The timestamps are first-party
TCG Live full-expansion availability times, not physical-release dates.
The membership policy remains half-open and uses tournament dates.

This new branch-only snapshot follows the post-DLR proposal already prepared
in `data/candidates/releases/tcg_live_windows_issue36_c2a_post_dlr_proposal.json`.
The non-runtime proposal envelope remains **unchanged** and un-loadable by
the standard release loader. The original pre-DLR candidate remains in Git
at baseline HEAD; the pre-DLR C.2A report documents its original gate.
The approved historical catalog and all FROZEN artifacts are untouched.

## Provenance and identity

The new catalog records official source URLs for CRI/PBL/30C/DLR,
the C.1 archive and post-DLR proposal hashes, the exact previous branch
Git blob `495aab2d4b92023738f4f0f9e0c5fa33bad053c4`,
the previous candidate HEAD, and the current C.4 authorization.

A changed `catalog_version` and changed catalog bytes invalidate prior
candidate receipt identities. The existing receipt includes the exact
completed window, YAML SHA-256, catalog SHA-256, and software revision.
Do not re-label historical receipts as newly acquired or producer-compatible.

## Limits and required gates

- This branch is **not deployed**. The published state still reflects the
  old CRI window, and a `noop` is not a freshness guarantee.
- The effective `config/tcg.yaml`, planner/job/runtime loader, scheduler,
  publication state/pointer, Pocket, Core/MARSConfig and methodology are unchanged.
- Neither `publish=False` nor a green CI certifies a fresh Tournament API
  acquisition. No LIVE acquisition, rebuild/import or publication is authorized.
- Local tests must cover exact UTC boundaries (including midnight and
  microseconds), chain and provenance, old-publication-state mismatch, and
  receipt identity changes, without network calls.
- Independent read-only review is required before technical closure.
- Merge, deployment, LIVE acquisition for 30C and publication each require
  a later, distinct authorization from Andrea.

## Verification status — implementer, not independent reviewer

Targeted offline Windows suite (Python 3.14 environment, pytest 9.1.1):
**91 passed, exit 0**. Covered C.2A/C.4 boundaries, synthetic pre-DLR
regression, latest_completed planner/job, candidate receipts, production
config and live-catalog integration tests with mocks/no new LIVE acquisition.
Full output: `C:\dev\Pokemon\c4-post-dlr-qa-20261107\targeted.log`.
Pytest's default Windows temporary directory was inaccessible (WinError 5),
so the passing run used an isolated `--basetemp` outside the worktree.
Initial failed/errored invocations do **not** count as passing runs.

GitHub PR CI, exact commit hash and independent review must be read back
separately; none is inferred from this local PASS.
