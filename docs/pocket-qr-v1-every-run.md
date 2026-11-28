# Pocket QR V1 — every-run operation (candidate)

Authority: issue #38, SELECTION R1 and EVERY RUN decisions. This document is a
candidate operating note, not an activation or merge approval.

## Contract

Every successful Pocket ranking-producing pipeline invocation calls the QR
stage, regardless of byte equality of the ranking. An enabled run extracts
the run's own hash-verified details/standings, selects the highest exact
participants/placing among valid native decklists (deterministic tournament,
player, slot tie-break), encodes and decodes each PNG, then installs the
bundle. Identical bundles can be retained only after the new validation.
No network request, HTML fallback, change to MARS, or QR for TCG/non-ranking
runs is introduced. An offline or manual ranking without its own verified
RAW yields ERROR, not reuse of a previous QR.

Run manifest diagnostics:
- PASS: all available Top 10 ranks have verified QR.
- GAP: some or all ranks lack eligible complete lists; dedicated manifest
  includes the explicit per-rank reasons and only verified PNGs.
- ERROR: optional QR generation or private publication failed; there is no
  valid QR pointer. Ranking generation remains independent.
- DISABLED: no QR generation; retire any previous active QR directory.

CURRENT means the current ranking and run manifest agree with the dedicated
QR manifest: status PASS/GAP, manifest hash, ranking SHA-256, acquisition run
ID, exact identity and PNG hashes. A directory or PNG's existence is not
evidence of CURRENT. Never consume an old QR on a new run. If a crash or
filesystem permission error leaves a directory visible without this binding,
it is non-current and must not be consumed. Recover using a new valid run.

The private publisher stages a QR subtransaction, with its dedicated manifest
last, then commits the ordinary artifacts with the enclosing run manifest
last. The latter is the authoritative commit marker. QR-only validation/copy
failure archives the old or partial directory, publishes the ranking and
writes a derived private run manifest with ERROR and source-manifest hash.
The local run manifest is not changed. A core publication failure still fails
the core transaction; do not restore a previous QR after a ranking may have
changed. Retry from a valid run. No claim of atomic multi-file rollback is
made. Use a single writer for a given Pocket scope. History is moved into
decklists/qr_history, never deleted or published as CURRENT.

The native card database is pinned to commit
75dbced2cf909dd300134dbb23a031e22f3e8fe7 of
flibustier/pokemon-tcg-pocket-database, with license and SHA-256 verification.
Update it separately when new cards appear; no runtime network refresh or
guessing of card IDs or energies. Private manifests can contain tournament
evidence and player IDs; do not include them in public publication.

## Activation / rollback (separate approval required)

1. Review exact PR head, entire CI, and current C1 evidence. Merge only after
   explicit authorization; do not run a live acquisition just to validate QR.
2. Align production checkout with approved commit, install requirements-qr.txt
   in the Pocket environment, verify pinned database and dependencies. Enable
   qr_decklists.enabled in each ordinary effective Pocket ranking config
   (scheduled/manual, including explicit variants); leave TCG unchanged.
3. Observe first authorized valid Pocket run: rank/RAW/QR manifest and PNG
   hashes, per-rank GAPs, private readback, absence of stale active files.
   User can confirm actual in-game import. CI does not certify game import.
4. Rollback: set enabled false, run next authorized ordinary Pocket ranking,
   check DISABLED and no valid active QR pointer in both local and private
   manifests; previous bundles preserved in history. If retirement fails,
   treat visible files as non-current, report and repair explicitly. Only then
   consider reverting code; never revert to code that silently leaves old QR
   marked current. No scheduler change, new workflow trigger or production
   activation in this PR.
5. Retroactive backfill is a separate TODO.
