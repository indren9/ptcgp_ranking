# TCG Live final reviewed boundaries — Gate 1.11-D2-R4

The final catalog approval is **HUMAN_APPROVED / FROZEN**. Chat Madre/user
explicitly approved: “Approvo il catalogo storico completo TCG Live.”
The final review gate is `GATE_1_11_D2_FINAL_CATALOG_REVIEW` and the approval
policy is `tcg-live-frozen-catalog-1`.

The independent final review approved all 23 expansion starts: 20
`MACHINE_VERIFIED`, 3 `HUMAN_ADJUDICATED`, and 0 unresolved. The reviewed
runner input is
`data/approved/releases/tcg_live_historical_boundaries_reviewed.json`.
It has `schema_version=1`, `window_unit=expansion`, `reviewed=true`, and
`evidence.reviewed=true` on every boundary.

## Frozen provenance

The separate attestation is
`data/approved/releases/tcg_live_historical_boundary_approval.json`.
Its complete SHA-256 is pinned in `historical_boundaries/reviewed.py`:
`7553fe1b1f09990c9294d9eeee6b5d738fadc5f4c5ad81a9f4278425e7528268`.
The same approval metadata is embedded under `review` in the reviewed artifact.
Changing a source and updating the attestation's source digest cannot transfer
this approval to new content: the attestation itself must match the pinned hash.

Source revision: `a7de0ca47ed42ed14a6f00557bbb3ff3c0c95e31`.
All hashes below cover **exact UTF-8 file bytes, with no normalization**.
The approved R3 files use LF. A checkout that changes even line endings fails
closed; restore the approved source bytes rather than changing the pinned hashes.
The approved directory fixes JSON line endings to LF for deterministic checkout.

| Research input in `data/candidates/releases/` | SHA-256 |
| --- | --- |
| `tcg_live_historical_boundaries_candidate.json` | `57df7f1a25fee938569f9c1792ebd344913bfcaf2e4ec1d394c31cf17906bb7f` |
| `tcg_live_human_adjudications.json` | `e519d2f08ac8a885b5adfcb99f646929504a41b22800e3010b6418b284e3da70` |
| `tcg_live_historical_boundary_evidence.json` | `390c8cd91b14a7ad2a908578ad7eb15d3668fbc618356c422339e2207c802d00` |

The candidate remains `COMPLETE_UNREVIEWED` with `reviewed=false` and every
boundary's `evidence.reviewed=false`. The human register remains
`catalog_reviewed=false`, with all three individual approvals true. Research
history, ledger evidence, machine conflicts, R2 artifacts, R3 preview and the
[R3 review](tcg-live-historical-boundary-review.md) remain unchanged predecessor
records. R4 does not claim that the human decisions became machine verification.

The exact human starts remain PAF `2024-01-25T17:00:00Z`, SSP
`2024-11-07T17:00:00Z`, and PRE `2025-01-16T17:00:00Z`. Their decision IDs and
original machine states are retained in the reviewed boundaries and refer to
the unchanged register. Historical timing patterns and Limitless are unused for
these decisions. No timestamp is researched, recalculated or reinterpreted by
the promotion generator. It copies the bound R3 sequence and changes only the
review flags and final catalog status, adding approval and generation metadata.

## Offline generation and direct D1 validation

Use the repository virtual environment, Python **3.14.7**:

```powershell
& .\.venv\Scripts\python.exe -m historical_boundaries.reviewed --write
& .\.venv\Scripts\python.exe -m historical_boundaries.reviewed
```

Without `--write`, the command checks the persisted artifact byte-for-byte
against deterministic generation, then passes that persisted JSON directly to
`historical_rebuild.model.load_windows`. No temporary review flag changes are
made during D1 validation. `--write` creates only the fixed reviewed path when
absent. An identical existing artifact is left untouched; differing bytes are
rejected, even with `--write`. Missing final approval or changed input bytes fail
before writing. There is no custom output path or rebuild operation.

Expected result: `D1_COMPATIBILITY=PASS`, 23 expansion identities, 22 distinct
release instants and 22 logical windows. Windows are contiguous with no gaps or
zero-duration intervals. Both BLK and WHT remain at `2025-07-17T17:00:00Z` in one
logical window. The last window is `[2026-09-15T17:00:00Z, OPEN)` for 30C.
Rotations are absent from runner boundaries; their audit metadata remains in
the research ledger. There is no canonical `observation_cutoff_utc`.

The generator makes no network requests, invokes no runner or production
backend, and writes no production catalogs. It does not execute `next`, `all`
or `window`, and does not publish outputs. Real historical rebuild execution
and its observation cutoff are a later, separately authorized operation.

Focused tests in `tests/test_reviewed_historical_boundaries.py` cover frozen
provenance, all 23 timestamp mutations, missing approvals, source digest and
register changes, persistence, direct D1 loading, grouping, immutability and
byte-identical regeneration. Network access, research compilation, targeted
fallback and rebuild execution are blocked by test guards.
