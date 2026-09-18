# TCG resumable historical rebuild — Gate 1.11-D1

This runner is execution infrastructure. It does not build a historical release
catalog, promote candidates, modify canonical release files, schedule jobs, or
publish rankings. The real historical rebuild is a separate, interactive user run
after review. Gate development runs only synthetic/offline fixtures.

## Later user commands

From the integrated TCG repository, using Python **3.14.7**:

```powershell
.\.venv\Scripts\python.exe -m historical_rebuild --boundaries reviewed-boundaries.json status
.\.venv\Scripts\python.exe -m historical_rebuild --boundaries reviewed-boundaries.json next
.\.venv\Scripts\python.exe -m historical_rebuild --boundaries reviewed-boundaries.json all
.\.venv\Scripts\python.exe -m historical_rebuild --boundaries reviewed-boundaries.json window WINDOW_ID
```

`--config` optionally selects a TCG configuration; the default is `config/tcg.yaml`.
The configured production paths and publication settings are **never invoked**.
The CLI deliberately has no output-root or publication override: its workspace is
`outputs/TCG/Rebuild/`. Keep the reviewed boundary input outside that directory.

* **status** checks checkpoints, effective fingerprints and artifact SHA-256s.
  It performs zero network calls, stage computation or writes, including on an
  empty workspace. It reports overall/window state, RAW counts, Core/MARS state,
  canonical bounds, active stage, last successful checkpoint and error. It may
  take time to read/hash a large collection. It is a point-in-time view; another
  active writer can advance after the read.
* **next** skips independently verified DONE windows, resumes the first incomplete
  window chronologically and stops after that window completes or fails.
* **all** does the same sequentially until all supplied windows complete or the
  first failure occurs. Retryable failures also stop this invocation; rerun to retry.
* **window ID** resumes only the selected window. A still-current DONE window is
  skipped byte-for-byte, including its checkpoint. Unknown IDs fail.

Exit code 0 means the requested operation completed; 2 means invalid input or a
blocked/failed execution. An interrupted process may return its native interrupt
exit code. The CLI never silently skips a failed historical window.

## Reviewed boundary input

Schema version 1 is an ordered list of official starts, with an explicit review
attestation. There is no independent end field. This deliberately does not accept
the legacy release catalog's duplicated end values or unreviewed observer output.

The following is a **schema illustration with synthetic dates and a placeholder
URL, not official evidence and not an input to a real rebuild**:

```json
{
  "schema_version": 1,
  "reviewed": true,
  "boundaries": [
    {
      "event_id": "SYNTHETIC_A",
      "name": "Synthetic expansion A",
      "kind": "expansion",
      "start_utc": "2020-01-01T17:00:00Z",
      "reasons": ["expansion"],
      "evidence": {
        "url": "https://www.pokemon.com/REPLACE-WITH-REVIEWED-OFFICIAL-SOURCE",
        "reviewed": true,
        "exact_time": true
      }
    },
    {
      "event_id": "SYNTHETIC_B",
      "name": "Synthetic expansion B",
      "kind": "expansion",
      "start_utc": "2020-02-01T17:00:00Z",
      "reasons": ["expansion"],
      "evidence": {
        "url": "https://www.pokemon.com/REPLACE-WITH-REVIEWED-OFFICIAL-SOURCE",
        "reviewed": true,
        "exact_time": true
      }
    }
  ]
}
```

This produces A = `[A.start, B.start)` and B = `[B.start, OPEN)`.
`end_utc` is null for OPEN. UTC timestamps must contain an explicit time; date-only,
missing, unordered, ambiguous/unreviewed evidence, duplicate identities and
independent end fields fail closed. An incomplete input bundle is rejected as
NOT READY, so a missing intermediate start can never accidentally lengthen a window.
IDs are portable single path components, case-insensitively unique.

Evidence must attest reviewed exact time and link to an HTTPS first-party Pokémon
domain (`pokemon.com` or `pokemon-support.com`, including subdomains). This is a
reviewed-input contract: the runner does **not** fetch the official article or
certify that an attestation is truthful. The separately reviewed catalog must
follow Gate C0/C1's official-source methodology; never mark inferred times reviewed.

`kind: "rotation"` preserves an explicitly supplied official Standard rotation
event. A distinct timestamp makes a distinct window. Coincident expansion/rotation
events form one boundary with all identities/reasons preserved, including in the
previous window's end-boundary provenance. Expansion identity is preferred as the
window ID at coincidence; ordering is otherwise deterministic. No rotation is
discovered, fabricated or silently dropped.

OPEN can be displayed without a cutoff. Executing it additionally requires a
top-level `observation_cutoff_utc` strictly after its start. This is an explicit,
frozen **acquisition observation horizon**, not a canonical expansion end. It
makes the effective data scope reproducible and prevents using the wall clock as
a hidden input. Changing it invalidates that window; its canonical `end_utc`
remains null. A later supplied official start automatically derives the closed end.
The runner freezes source evidence once; it does not claim source results are final
or keep OPEN tournaments live-refreshed. Review of data maturity belongs to the
later historical input/run gate.

## Execution and persisted state

Visible stages are:

```
WINDOW_READY → DISCOVERY → TOURNAMENT_IDS_FROZEN → RAW_ACQUISITION
 → NORMALIZATION → CORE → MARS → VALIDATION → REPORT → DONE
```

Discovery and eligibility call the existing Tournament API helpers and selector.
Discovery freezes candidate IDs; selection freezes sorted eligible IDs and their
details evidence. Incomplete pagination, invalid eligibility evidence, empty
selection or API errors stop the window. The raw checkpoint fetches standings
and pairings for each missing tournament, with the frozen details. The existing
production request pacing/retry client is used without a shared production cache.
An interruption during discovery/selection can repeat that stage; the per-tournament
resume guarantee applies to RAW acquisition.

Normalization reuses existing snapshot normalization, aggregation and Part-1
contracts/bridge. Canonical deck IDs remain the computation axis, as in production;
legacy display-name aliases are intentionally bypassed. Core calls the existing
canonical dense Core helper. MARS calls `mars.pipeline.run_mars` with the configured
`MARSConfig`, including the existing deterministic AUTO_K seed. No math is copied.
Validation checks production matrix contracts, complete ranking axes, finite
ranking values and ordering. Report uses the production workbook writer and checks
its summary sheet. Only XLSX container/property timestamps are fixed to make bytes
reproducible; ranking/report calculations are unchanged.

Each window owns:

```
outputs/TCG/Rebuild/<window-id>/
  state/current.json                 # checksummed current-state pointer
  state/<stage>/<fingerprint>-<attempt>/result.json
  raw/<content-fingerprint>-<attempt>/result.json
  normalized/<fingerprint>-<attempt>/result.json
  core/<fingerprint>-<attempt>/result.json
  mars/<fingerprint>-<attempt>/       # result.json + ranking.csv
  report/<fingerprint>-<attempt>/     # result.json + validated XLSX
```

The checksum envelope contains `state` and `sha256`. State schema 1 retains window
identity/definition, start/derived end, event/reason/evidence provenance, definition
fingerprint, overall status/current stage, ID-manifest digest, tournament IDs and
counts, per-stage status/input/output hashes/effective semantics/start/completion/error, raw references,
acquisition metadata, Python provenance and last successful checkpoint. Stage
references contain a complete relative file inventory with SHA-256 for every file.
`result.json` is a deterministic machine-readable contract; analytical DataFrames
use CSV payloads with round-trip float parsing. No pickle loading is used.

Raw records distinguish PENDING, VALID, FAILED_RETRYABLE, FAILED_BLOCKED and STALE.
Every successful tournament is structurally validated and independently checkpointed
before the next acquisition. File existence alone never authorizes reuse. Missing,
changed or corrupt RAW affects only that tournament's acquisition. Removed IDs stay
in audit storage but do not contribute to current counts or downstream data.

Only the current state whose **entire dependency chain and file digests** recheck
successfully is DONE. Generation files alone are work in progress; there is no
automatic public/latest pointer outside this rebuild workspace.

## Fingerprints and invalidation

Inputs use sorted canonical JSON/SHA-256. Each stage binds effective local semantics
and required upstream input/output fingerprints. Production fingerprints record
relevant module hashes, transitive function/constant AST hashes within the shared
pipeline module, effective configuration, Python/relevant package versions and
report font/compression dependencies. A report
function edit in that shared module cannot invalidate Core. The repository commit
SHA, unrelated config paths, launcher files, publication settings and legacy alias
file are not blanket invalidators. Semantic contract version must be bumped when
changing the orchestration contract itself.

| Change | First affected stage | Reuse |
|---|---|---|
| Official start/end, boundary identity/reasons/evidence or OPEN horizon | WINDOW_READY / DISCOVERY | Unchanged validated raw items can still be reused after selection |
| Eligibility/discovery semantics | DISCOVERY | Common unchanged eligible raw items |
| Frozen IDs/details change | RAW / NORMALIZATION | Common IDs with identical acquisition inputs |
| Single corrupt/missing raw tournament | RAW, then NORMALIZATION onward | All unrelated valid tournaments |
| Normalization implementation / effective alias policy | NORMALIZATION | RAW |
| Core config/top-meta/filter/implementation | CORE | RAW and normalization |
| MARSConfig/seed/relevant implementation | MARS | RAW, normalization and Core |
| Report implementation/config | REPORT | Everything through validation |

Canonical-ID alias policy is fingerprinted at normalization. The production legacy
alias file has no effective role in this pipeline, so editing that irrelevant file
does not trigger acquisition or recomputation. No new alias mechanism is introduced.
The tournament-manifest digest covers both IDs and frozen details; changing an
individual tournament's acquisition semantics/evidence reacquires that item.

Stale and interrupted generations remain for audit/debugging. New attempts always
write a separate generation, even if their input fingerprint is identical. Old
valid output is never overwritten. Raw records retain superseded references.
There is no automatic garbage collector, broad reset or silent checkpoint repair.

## Interruption and recovery

Example: first run of window X stops at RAW **356/469**. On a normal rerun the
runner hashes the previous evidence, reuses **356** valid tournaments, acquires the
remaining **113**, then continues normalization/Core/MARS/validation/report to DONE.
The tests also prove that a MARS-only configuration change retains the exact Core
generation and performs zero RAW requests.

Checkpoint/result writes use temporary files, flush/fsync and atomic replace.
Adapter-created generation files are fsynced before publishing their pointer.
A crash before the pointer update leaves an unreferenced generation, never a
partially accepted one. Normal exceptions persist stage/raw errors. Keyboard
interrupts retain the most recent completed checkpoint; a RUNNING stage is treated
as stale on inspection. Abrupt process/PC termination can leave the writer lock.

One exclusive `outputs/TCG/Rebuild/writer.lock` protects the entire run (including
window selection). It records hostname, PID, creation time and an ownership token.
Active, stale, malformed and unknown locks all fail closed. The runner releases
only its own byte-identical lock on a normal exit or handled interruption.

For an abrupt termination, read the lock and verify on the recorded host that the
writer is no longer running. Account for PID reuse; if ownership is uncertain,
stop. Once absence is established, manually **archive** that lock outside the active
lock path, then rerun the same command. No timeout automatically deletes a lock.
STATUS remains available while a lock exists. A corrupt/unsupported checkpoint,
or a missing checkpoint in a workspace that already contains evidence,
must be preserved and restored from a verified backup/reviewed recovery; deleting
state to make a failure disappear is not supported. Filesystem durability still
depends on the underlying filesystem/hardware honoring flush/atomic replace.

Failures distinguish retryable acquisition, blocked data/methodology, validation,
stale/corrupt checkpoint, and artifact integrity. Any such failure prevents DONE
and stops NEXT/ALL. Previously validated windows remain intact.

Interactive output uses the installed `tqdm` writer with progress bars and explicit
overall/current/raw counts. Redirected output uses plain checkpoint lines without
terminal control characters. Rendering never controls stage execution.

## Verification

`tests/test_historical_rebuild.py` covers Gate D1 A–X, including production adapters
with a mocked HTTP client, real Core/MARS/report execution, cross-workspace artifact
determinism, atomic-write failure, interrupted resume and fail-closed locks/state.
All fixtures are synthetic; none are the historical TCG catalog.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_historical_rebuild.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_release_candidates.py tests/test_official_release_observer.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

No production `next`, `all` or `window` command is part of implementation validation.
Canonical catalog review, historical acquisition, publication/replacement and merge
remain separate later actions.

### Gate D1 verification record

Validated against `origin/main` **20f616d6d39b4a2c6d811ae014617022c304a30d**,
on branch `feat/tcg-resumable-historical-rebuild`, under **Python 3.14.7**.

| Check | Result |
|---|---|
| Focused rebuild-runner tests | 46 passed |
| Release candidate / official observer regression | 168 passed |
| Full pytest | 726 passed, 0 failed; 6 existing plotting deprecation warnings |
| pip check | No broken requirements found |
| Protected-file comparisons | 21/21 SHA-256 checks identical (20 unique files) |
| New production/public output files | 0 |
| Requirements/new dependencies | Unchanged / none |
| Real historical rebuild | **NO** |

The five explicitly protected files had these identical before/after SHA-256s:

| File | SHA-256 before = after |
|---|---|
| `data/reference/ptcg_live_windows.json` | `57DF1A7BD3B4CA0FE67FFC0B45054EAA64842FE1C1B403E13CA880A347ABA048` |
| `data/reference/pocket_releases.json` | `3ECE52AA2C90B953F4DC9FFF7F9994F857D89A27C79FFDAA7E6BC5BB9226597E` |
| `.github/tcg-live-latest-completed-meta-state.json` | `3DDFEBA94330DAC4D81C2014755C351DC8121DA18955751FBC5152436101C3AB` |
| `requirements.txt` | `73FA463C6E73524B07BEC32C601DB3D87745DCD6F1B6E2642CFD29EED57F0400` |
| `.github/workflows/update-expansion-catalog.yml` | `72BFDCB25EC758E4335D61F179038D8E6593903F03574F8D3AE57D621486361D` |

All existing files under `outputs/`, `public/` and `.github/workflows/` were also
compared. No scheduler, launcher/routing, OneDrive publication or historical Pocket
worktree file was modified. Git operations necessarily use the integrated
worktree's shared administrative Git metadata; no Pocket worktree content was used
as an output target.
