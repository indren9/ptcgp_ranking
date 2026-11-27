# WP4 G4 — private TCG RAW persistence contract (owner APPROVED 2026-11-27)

Authority: Andrea, WBS decision "WP4 — decisione backend RAW locale", 2026-11-27.
Scope: dedicated local TCG P2 checkout only; no change to Pocket, public job, S3 APIs, FROZEN or scientific methodology.
Source: https://app.notion.com/p/4230b32ce547811983cafed6a7286d13

- Explicit --publish-private chooses a persistent local object store, not a fallback from S3.
  Primary: sibling mars-raw-tcg outside the checkout/cache; on PC-Andrea C:/dev/Pokemon/mars-raw-tcg.
- OneDrive personal TCG/RAW_Backup holds a private ZIP for each run ID; no ranking files or raw data in Git.
- Reuse canonical object keys, manifests, SHA-256 and original restore validation.
  Exclusive writes; existing bytes must match; incomplete or conflicting objects/archives fail closed.
- Back up verified canonical index and every referenced object before restore; restore directly from the independently readable archive.
  No overwrite or silent repair; retain incomplete files for explicit recovery. Local filesystem verification is not proof of cloud synchronization.
- A successful G4 requires independent cloud readback/metadata comparison for the backup, exact restore, unchanged historical source revision,
  and verified private set publication under the existing global mutex. Record a separate receipt linking historical source inventory, current commit and bundle.
- Public/nonprivate paths retain S3; injected backend factories remain explicit test/integration adapters.
- At the G4 gate, G5/G6 were STOP; no service deactivation, push, merge or scheduler/launcher changes were authorized by G4.

## WP4 closure — 2026-11-27

Andrea subsequently authorized G5/G6 and accepted WP4/P2 CLOSED. Source: the [P2 WBS](https://app.notion.com/p/4230b32ce547811983cafed6a7286d13), sections WP4 SESSION CLOSE and P2 ACCEPTED/CLOSED.

- Deployed commit `4800dadcdf17a823229f6804f88cf9a2d017060d`, parent `1e6cf865c640f9e03b69c4fd24e5f0626623b8ab`, main baseline `d39c7eca97c8e0eb85fda770085f77fb4a13674b`. The review branch preserves those commits; a documentation-only follow-up is not a new deployment.
- G4: 16/16 targeted infrastructure tests passed; 2,147 canonical RAW files (79,145,344 bytes) persisted and restored from the backup with SHA-256 checks, zero new acquisitions. Backup 9,980,251 bytes, local SHA-256 `fcc895c4aa0088fb4d408531dacf089a969f3e73b3f46011410922ee25311703`, cloud size/QuickXor matched. No independent cloud-download SHA-256 claim.
- Original acquisition run `limitless-api-live-20261127T084539727051Z`, acquisition manifest SHA-256 `349f900730181206a211aeb5f386901cb14eed3b0af0d99692ecdf8b7f81e565`. Historical source revision/snapshot preserved; the bootstrap did not recalculate or relabel it as a new run.
- G5: dedicated TCG pointer and private flags only; common mutex, sleep protection, Pocket and both scheduler definitions preserved.
- G6: one ordinary TCG task execution at 15:16 CET, verified private 30C NOOP, 0 network calls, 0 publications, exit 0. Pre/post SHA-256 equality: 4,296 RAW objects/metadata files, 3 ranking artifacts, 1 ZIP, 269 Pocket files. Both tasks Ready after the run.
- G3/G4 limitations remain: future real rollover not exercised; cloud synchronization not atomic; external consumers and power-loss recovery not fully qualified. Local archive verification alone does not establish future cloud sync. S3 compatibility and services remain unchanged.

Evidence root on the approved operational PC: `ptcgp-p2-wp4-g1-g3-20261127` (outside Git; no private RAW, archive, ranking or runtime credentials committed).

| Evidence | SHA-256 |
| --- | --- |
| G4_MANIFEST.json | f1ce70497706f33a2d6747f0e21ea26fc23d7b80ea0e56752ad58a6f7db56fd8 |
| G4_RAW_RECEIPT.json | ebf988320bd743bc3cec9fb0d968c1228d3fa339cff368dc2d357f100fada094 |
| G4_PUBLICATION_RECEIPT.json | af68251022334bb8351318c468efa5b9ea15ce4d5c739970953f46bebe9533fa |
| G5_MANIFEST.json | 408cc3defc5775d39d790e16917709e3b3a911d5518dde9c6cf48a7d4a242c52 |
| G6_MANIFEST.json | b21ef0629ae0a69a4c4a311e5697ed2a34473ecf25db479fbe9b711418b4eb9e |

No remote integration or S3 service deactivation is authorized by this record.
