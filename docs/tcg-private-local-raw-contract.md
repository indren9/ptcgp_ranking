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
- Existing G5/G6 STOP; no service deactivation, push, merge or scheduler/launcher changes.
