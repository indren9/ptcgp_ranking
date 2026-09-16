# Non-authoritative release candidates (Gate 1.11 Phase B)

Candidate != canonical. Approval != activation. Date != exact timestamp.
Limitless discovery != release-time authority.

`release_candidates` is an isolated, offline operator/developer library and
review CLI. Production CLI, notebook Run All, Scheduled Tasks, refresh, selection,
MARS/Core and publication do not import it. Normal users need no new flags and
are not expected to edit candidate files. No crawler, scheduled workflow,
promotion function, activation or canonical writer is included in this phase.

## Contracts

`model.Candidate` holds game/platform/format, release code/name, logical event
key/semantic ID, kind/reasons, lifecycle state/revision, date/local datetime/zone/
UTC instant, parent version/digest, conflict/supersession/missing-evidence fields
and a reviewed identity mapping. `evidence` is an ordered nonempty tuple: the
first entry is the selected primary source, remaining entries corroborate or
expose conflicts. Each `Evidence` carries event reference, tier, URL, authority,
category, retrieval timestamp, short retained excerpt and its UTF-8 SHA-256,
extracted date/time/zone, local offset and conversion rationale. This is an
excerpt digest, not a claim to have archived the entire article.

Strict JSON arrays/booleans and unknown-field rejection apply. Supported contexts
are POCKET/POCKET/STANDARD and PTCG/PTCGL/STANDARD. Source policy:

- A: Pokémon documented API, including official documentation URL.
- B: Pokémon announcement or named official staff announcement.
- C: Limitless operational metadata; identity/discovery/cross-check only.
- D/E: independent reporting/community corroboration, never future exact-time authority.

Official host allowlisting is necessary but not proof. Eligible A/B evidence also
requires explicit `authority_verified: true` and a review note. That flag records
an external evidence review; it is not an automated authenticity determination.
Candidate validation cannot prevent a dishonest author from fabricating an
attestation. Reviewers must inspect retained context/source identity and confirm
the event binding. Tier A fixtures are synthetic, not claims a real release API exists.

`DISCOVERED`, `DATE_CONFIRMED`, `TIMESTAMP_CONFIRMED`, `CANONICAL_CANDIDATE`,
`CANONICAL_APPROVED`, `CONFLICT`, `QUARANTINED`, `SUPERSEDED` are representable.
ACTIVE/COMPLETED are deliberately unsupported. Date-only proposals leave exact
fields null; no default hour is invented. Exact evidence needs a local ISO
datetime **with offset**, matching extracted date/time and UTC, IANA zone or
explicit numeric timezone. Abbreviations such as PST/PT are not silently parsed.
Repeated DST hours require the offset to disambiguate; nonexistent times fail.
IANA zones need the system/existing environment timezone database; if unavailable,
validation fails rather than guessing (no new dependency is installed).

## Identity, revisions and parent safety

The semantic event ID is `game:platform:format:event_key`, using a reviewed stable
key such as `release:b5` or `rotation:2027`. It does not contain a date or candidate
hash. A delayed/corrected event retains that identity. Rotation under the same
expansion uses a distinct key. Coincident expansion and rotation are represented
as one `combined` event with both sorted reasons, never duplicate boundaries.
Keys are curated mappings, not automatically guessed from aliases or timestamps.

Revision is a deterministic SHA-256 over all candidate content except lifecycle
state, revision itself and approval reference. Evidence, proposed time, mapping,
parent identity or supersession changes create a new revision. State changes do
not rewrite historical evidence. `prepare_candidate(payload)` seals a proposal
but refuses CANONICAL_APPROVED; externally reviewed approval can be represented
by `Candidate.from_dict` with an approval reference. No review status causes any
activation or publication.

Validate a complete review bundle including superseded ancestors. A correction
references the preceding revision, which must be explicitly SUPERSEDED; missing,
cross-event, duplicate-live or cyclic relationships fail. Equal-time events must
be combined explicitly by a reviewer; the validator does not guess/coalesce them.

Mature candidates bind to the exact current canonical version and SHA-256 bytes.
Pass fresh `ParentSnapshot.from_bytes(...)` snapshots to validation. Stale input
fails, without rewriting its parent. An explicit re-review/new revision is needed
after any parent change. Candidates can be future-dated without closing today's
canonical window. Structural promotability is **advisory**, never a promotion API.

## Review reports and storage

The reusable `validate_candidates` returns structured results. `report_json` and
`report_markdown` are pure deterministic renderers. For an already sealed JSON
candidate array (see executable fixtures in `tests/test_release_candidates.py`):

```powershell
.\.venv\Scripts\python.exe -m release_candidates candidate-input.json --format markdown
.\.venv\Scripts\python.exe -m release_candidates candidate-input.json --save-review
```

The optional save can only create content-addressed `*.candidate.json` and
`*.candidate.md` files in `data/candidates/releases/reviews/`; no output-path
override exists. Existing identical artifacts are idempotent; different bytes
at the same path are refused. Symlink/junction redirection is rejected. Reports
include errors, missing evidence, source conflicts, stale status and advisory
promotability. Non-promotable early discovery is a valid report; validation
errors or authoritative conflict produce CLI exit 1. No network is used.
An authoritative disagreement reports CONFLICT (or preserves QUARANTINED /
SUPERSEDED), retaining the submitted state separately; it never changes input files.

## Frozen historical policy

Existing lower-tier Pocket rows remain grandfathered in their current canonical
version; they are not precedent for future times and are never rewritten here.
TCG v1 remains frozen historical methodology. Synthetic CRI/PBL tests represent
00:00 UTC v1 observations and separate 17:00 UTC correction candidates, retaining
semantic identity while changing revision. These are candidate-only fixtures,
not a v2 deployment, impact study, acquisition or republication.

Future evidence collection automation, reviewed canonical updates, delivery,
activation, migration/republication identity and production impact study require
later authorization. This phase does not alter the existing canonical loader,
60-minute refresh policy, TCG code-based NOOP behavior or public catalog workflow.
