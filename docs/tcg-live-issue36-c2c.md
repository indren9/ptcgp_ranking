# Issue #36 â€” Gate C.2C historical_import

Authority: [Andrea's C.2C mandate](https://github.com/indren9/ptcgp_ranking/issues/36#issuecomment-6561899282).
Candidate baseline: `71791accb0928a771c32f791748393829626c7f1`, PR #37.

## Entry point and boundaries

`python -m scripts.tcg_live_historical_import --request <absolute-request.json>`

This is an explicit standalone import, deliberately not an acquisition execution
mode of the daily. It imports already computed results without running the
historical runner, acquisition, Core, MARS, report or publication. The daily,
receipt loader, bundle producer, scheduler and production configuration are
unchanged. In particular `publish=True` on the daily retains its existing
behavior; this command has no `publish` parameter or LIVE fallback.

Version 1 supports CRI and PBL only. A request is a JSON object with exactly:

- `schema_version: 1`, `source_kind: "historical_import"`;
- absolute `source_root`, `source_state_sha256` (bytes of `state/current.json`);
- `historical_scope` and `target_scope`: full explicit dictionaries containing
  `policy_id`, `game`, `format`, `set_code`, `set_name`, `start_datetime`,
  `end_datetime`, `catalog_version`;
- `config_sha256`, `catalog_sha256`: exact bytes of the checked-out
  `config/tcg.yaml` and `data/reference/ptcg_live_windows.json`;
- `software_revision`: exact checked-out Git HEAD.

The caller must inspect and pin these values. No request generator silently
approves new identities. The original full scope must match the frozen selection
manifest; the full target scope must match the runtime catalog. Game, format,
policy, code and UTC half-open endpoints must agree OLDâ†’NEW. A name/catalog
translation is explicitly represented by the two pinned identities and never
rewrites the source. CRI does not become latest-completed.

## Verification and manifests

Before creating a candidate, the importer verifies the historical state wrapper,
schema, DONE/VALID stages, window fingerprint, immutable generation inventories,
stage output fingerprints, frozen tournament-manifest digest/order, full raw ID
coverage and raw payload/eligibility. All referenced input paths and exact hashes,
including the historical state and original generations, are retained. Input
symlinks and junctions fail closed.

NORMALIZATION, CORE, MARS and VALIDATION semantics must match exactly, including
the recorded config, implementation and runtime/package versions. A mismatch
blocks this import-only adapter; it does not trigger recomputation. A historical
Git revision absent from the source is explicitly null with its reason. The
original state, acquisition metadata, stage semantics and timestamps remain in
the lineage; the import does not assert a new acquisition date or freshness.

The import manifest records `source_kind=historical_import`, the pinned request,
actual software revision and verified raw-byte importer SHA-256. The executed
importer must match the Git blob at the pinned revision after Git's documented
checkout filters (allowing clean LF/CRLF conversion); tracked runtime paths (including
the importer) must be clean, with no untracked runtime dependencies. Git
verification errors fail closed; software checks repeat before sealing.
The manifest also records the full effective config, original and
target catalog identities, stage actions `import_result`, input inventory,
tournament IDs, selection caveats, independent QA and output hashes. Its ID is
content-derived; no wall-clock timestamp is used to impersonate acquisition.
The run manifest binds the import manifest by exact SHA-256 and repeats output
hashes, QA and blockers. Neither is a Tournament API manifest or a daily receipt.

Only `<worktree-parent>/historical-import-candidates/historical-import-<digest>`
can be written; the command accepts no public/state/cloud destination. The
scratch tree must not overlap repo or source, and the request must be outside it. An exclusive
writer lock covers the output transaction. All CSVs are reread with their
original explicit packed axes/types and compared before sealing. Original
inputs, request, config, catalog, implementation and HEAD are checked again
before sealing. A last pre-rename gate repeats the importer/Git checks and
compares every pending CSV with its recorded SHA-256, failing closed on a
concurrent modification. This is a pre-rename check, not an OS-level guarantee
against an adversary able to replace bytes after the check. Existing completed
candidates are never overwritten.

An interrupted attempt leaves a `.pending` tree and cannot be mistaken for a
completed candidate. Retry fails closed. Inspect the process and preserve/archive
that pending tree explicitly before retry; stale/unknown locks require the same
manual inspection. There is no automatic deletion or lock theft.

## QA and known limitations

QA checks axes, NaN diagonals, unique directed pairs/no mirrors, integer
nonnegative W/L/T/N, N=W+L+T, opposite directions, n_dir=W+L, WR from flat and
finite ordered ranking with the same axis. Label identities must be present,
unique and nonblank. Stored VALIDATION must bind the imported Core/MARS payloads.

The proven historical top-meta has exactly Rank/Deck/Count/Share_% and no source
URLs. Version 1 accepts that explicit historical contract and emits
`top_meta_historical.csv`; it rejects an unexpected URL-bearing table rather than
inventing or accepting unverified provenance. It marks producer compatibility
false. The current producer is not relaxed and no public bundle is built.

All selection classifications, excluded IDs, invalid evidence and evidence
coverage are reconciled. PBL's four unclassifiable identities remain visible:
`6a282e7a47f3797bf6baff56`, `6a56d5c26355385f44dd4e9a`,
`6a5bf8dc52c24ac2da645f63`, `6a9452cbabb9482237503eac`.
Metadata evidence coverage is not tournament-universe completeness. The manifest
always says freshness and universe completeness are uncertified, and any invalid
records require a later explicit production decision.

## Subsequent gate proposal (not implemented)

An independent reviewer should check pins and OLDâ†’NEW lineage, corruption and
interruption behavior, output isolation, source immutability, no-network tests,
CSV QA and CI. A separate publication gate must decide acceptance of the original
snapshot and PBL caveats, establish verified URL provenance or authorize a new
producer contract, validate a new candidate/publication bridge and destinations,
and authorize actual promotion. No receipt fabrication or bypass is proposed.

Technical tests and candidate QA do not authorize merge, deployment, LIVE,
publication, Pocket changes, FROZEN edits or methodology changes.

## Validation on 2026-11-01

- Native Windows/Python 3.14.7: 170 passed, 1 skipped, exit 0, 162.93 s.
  Command: `python -m pytest -q tests/test_tcg_live_historical_import.py tests/test_tcg_live_latest_completed_job.py tests/test_tcg_live_issue36_b3.py tests/test_tcg_live_issue36_c2a.py tests/test_historical_rebuild.py`.
  The skip is the Windows symlink privilege check; Linux CI must execute it.
- Real CRI/PBL imports to isolated scratch passed independent CSV round-trip QA:
  CRI 27 axes/702 directed rows/27 ranks, PBL 25 axes/600 rows/25 ranks.
  All 1,059 referenced original files remain hash-identical (481 CRI, 578 PBL),
  and all six candidate CSV hashes per window match the C.2B exports.
- 82 protected tracked files (config, catalogs, workflows, public, historical
  backend, Acquisition/Core/MARS and storage) are hash-identical.
- Development history: the initial 4 failing positive/recovery/lock tests exposed
  an overly broad request-directory overlap guard, corrected before the final
  passing run. An intermediate invocation exited 4 for a mistyped test filename
  and executed no tests. No failures were suppressed.
- Evidence directory on Andrea's PC: `C:\dev\Pokemon\c2c-qa-20261101`.
  The real-source harness blocks socket connections, backend acquisition/network
  and stage execution; only synthetic fixtures run actual MARS.
- CI and final committed-revision manifest hashes are reported separately after
  push; this document does not pre-claim CI success or production readiness.
