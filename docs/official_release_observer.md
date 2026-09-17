# Official TCG Live release observer (Gate 1.11-C1)

This developer-only observer discovers first-party release announcements and
produces **non-authoritative machine drafts**. It has no canonical writer,
promotion, activation, scheduler, launcher, refresh or publication integration.
An accepted observer timestamp is evidence verified under the machine policy,
not a canonical timestamp. Canonical status always remains `CANONICAL_PENDING`.

## Explicit entry points

```powershell
# Bounded public GET discovery. No network runs on import.
.\.venv\Scripts\python.exe -m official_release_observer --network

# Persist immutable evidence and fetch receipts in the fixed ignored store.
.\.venv\Scripts\python.exe -m official_release_observer --network --save

# Replay a local development capture. The URL identifies the claimed source;
# the caller is responsible for acquisition provenance of offline captures.
.\.venv\Scripts\python.exe -m official_release_observer --html capture.html --url https://community.pokemon.com/en-us/discussion/26004/pokemon-tcg-30th-celebration-expansion-release
```

`--article-url` can supply known US Pokémon.com editorial URLs for corroboration.
No search-engine indexing is used. Default bounds are 160 source pages, 240 HTTP
requests including robots/retries, concurrency one, and at least two seconds
between requests. `--max-sources` is capped at 200, HTTP budget at 500. These are
local operational limits, not a Pokémon quota. There is no recurring job.

## Authority and source profile

Policy `pokemon-live-na-admin-1` requires HTTPS, exact host
`community.pokemon.com`, canonical discussion ID consistency, the exact English
TCG Live News & Announcements category, one first-post container, matching original
author ID/profile/name, and server-rendered `Role_Administrator`, `Rank-Admin` and
`Administrator` markers in its header. Unknown/new role schemas fail closed as
`AUTHORITY_UNVERIFIED`. Account names are not an allowlist: another administrator
can satisfy the same profile. Quotes, replies, global banners and body claims of
admin status cannot supply authority or release evidence.

US Pokémon.com editorial articles have a separate bounded DOM profile; both
`/us/pokemon-news/<slug>` and the observed canonical alias `/us/news/<slug>` are
recognized. Only the reviewed explicit expansion-availability sentence is parsed.
Other localizations/templates stay pending. Product-info cards and quoted/hidden
content are excluded. Offline captures are not publisher signatures: machine
provenance records the applied policy and adapter, not cryptographic authorship.

## Discovery and network failures

1. Inspect the category landing for its advertised RSS and discussion links.
2. Consume RSS discussion URLs (not its pubDate as a release date).
3. Read the sitemap index advertised in robots and reconcile all advertised
   category shards, including historical discussions missing from the RSS.
4. Fetch canonical discussion pages, including previously stored sources for edit
   detection. Deduplicate by discussion ID rather than title, date or feed order.

The current robots exclusions for category pagination and search are respected:
neither route is used. Every redirect is checked against the original approved
host and robots rules. HTTP cache max-age, Age/Date, ETag/Last-Modified, no-cache,
no-store, 304 and Retry-After are honored. Cache and robots state are per explicit
run; there is no full-page on-disk cache. 429/5xx/timeouts have bounded retries;
long Retry-After defers the run, not a shortened retry. Challenges, invalid UTF-8,
oversized bodies, forbidden redirects and unavailable robots fail closed.

Missing RSS entries can be recovered from sitemap reconciliation. Failed surfaces,
truncated source sets or failed page fetches mark coverage incomplete and suppress
every accepted exact timestamp in that run's report. Raising explicit bounds may
be necessary as history grows; the observer never treats truncation as complete.
An empty/malformed discovery surface is not a successful absence claim.

Optional `/api/v2/discussions/<id>` JSON is supported by the parsing API as metadata
only. It must agree with the HTML source ID, category, original author, canonical
URL, body and publication timestamp. Schema/body mismatch gives `API_SCHEMA_DRIFT`.
It never replaces HTML authority. The CLI deliberately does not require JSON.
All such observations remain Tier B: no Pokémon-documented typed release calendar
or Tier A release API was established in C0. `dateInserted`/`dateUpdated` describe
the post; RSS pubDate and sitemap lastmod are never release timestamps.

## Event and time contract

The forum parser requires an explicit named expansion availability proposition,
a same-expansion all-game-modes playability statement, and one temporal block in
the first post. The title alone does not authorize anything. Maintenance, patches,
ladder, Trainer Trials, Build & Battle, Battle-Pass-only, physical release,
prerelease, redemption-only, individual-card access and rotation-only evidence
produce no accepted timestamp. Unknown prose or correction/delay qualifiers give
`EVENT_AMBIGUOUS`. Mixed expansion/rotation is a single combined event, as in
Perfect Order. This deliberately conservative grammar can require review for
valid new announcement wording.

PT resolves to America/Los_Angeles even without a UTC parenthetical, using the
installed `tzdata` package **loaded explicitly** and its recorded version. PDT/PST
and explicit Pacific offsets must agree seasonally. No unversioned OS fallback
is used. Ambiguous abbreviations/folds remain `TIMEZONE_AMBIGUOUS`; nonexistent
local times are rejected. Explicit UTC or an offset can select a unique fold.
The parser checks optional weekday, local calendar date, AM/PM, DST, UTC clock,
explicit UTC date and day rollover. Local/UTC disagreement is `OFFICIAL_CONFLICT`.
Date-only stays `DATE_CONFIRMED`, exact UTC null. There is no default hour.

## Reconciliation, identity and revisions

Evidence is sealed with SHA-256 and checked before reconciliation/bridging/storage.
Accepted exact timestamp implies policy PASS, the supported correct Live release
event, explicit exact time, valid zone conversion, integrity and no unresolved
conflict in the supplied official source scope. Two distinct official exact sources
can corroborate; duplicate observations do not. Compatible date-only evidence is
allowed, incompatible official dates/times fail closed. Non-official evidence has
no time-authority path. This is not a universal discovery/uptime guarantee.

Timestamp verification does not require a release code. A normalized official
expansion name creates a provisional, date-independent discovery identity; it is
not a reviewed canonical mapping. Renames/aliases or multi-expansion semantics
require review. `IDENTITY_UNMAPPED` does not invalidate the extracted timestamp.

Storage is fixed at `data/candidates/releases/observer/` (ignored). No output path
argument can redirect it into `data/reference/`. Symlinks/junctions, integrity
violations and concurrent writers are refused. Each observation preserves URL and
redirects, source ID, title/category, original author/role, available publication
and edit times, raw edit label, retrieval time, minimal excerpt and its digest,
normalized first-post text digest, metadata fingerprint, expansion identity,
raw local/zone/explicit UTC and calculated UTC, and versioned machine provenance.
Unknown edit timezone stays unknown; optional matching JSON can supply it.

Normalization decodes HTML entities, uses Unicode NFC and collapses whitespace
inside each selected span; retained spans are joined by LF. Metadata fingerprint
excludes counters/retrieval time. Observation IDs include the stable complete
record except retrieval time. The full page is not retained in generated evidence.

Identical observations append only a fetch receipt; immutable evidence is reused.
Content/metadata changes create `SOURCE_CHANGED` revisions. Changes to timestamp,
authority, date or event semantics quarantine the previous observer candidate,
record its revision and hold the affected discovery identities. Holds survive
subsequent unchanged runs. Explicit corrections never silently rewrite history.
There is intentionally no automatic hold-clear or canonical promotion operation.
Receipts retain the predecessor, so previous evidence is still reviewable.

## Phase B bridge and human review

`release_candidates.observer_bridge.machine_drafts` exposes a versioned sidecar
with `MACHINE_VERIFIED`, `verification_method=MACHINE_POLICY`, policy/parser/adapter/
tzdb versions and evidence references. Existing `Candidate` and `Evidence` formats,
hashes, human flags and validation semantics are unchanged.

`phase_b_draft` can reuse an explicitly supplied reviewed mapping to make a
`DISCOVERED` Phase B proposal; its human `authority_verified` and
`identity_verified` remain false. The mapping reference and machine observation ID
are recorded. No function emits `CANONICAL_APPROVED`, writes reference files or
marks a candidate structurally promotable. Reconciliation and persisted quarantine
must be respected by consumers; a single source's calculated UTC is not acceptance.

Manual review applies to new authority schemas, unsupported semantics, ambiguous
timezones, unmapped identities, official conflicts/corrections, quarantine, storage
integrity errors and persistent discovery failures. Date-only observations and
transient failures can remain pending automatically. Human attestation and later
canonical acceptance remain distinct from normal automatic evidence collection.

## Validation and fixture provenance

`tests/fixtures/official_release_observer/official_excerpts.json` contains eight
minimal C0 release excerpts (30th Celebration, Pitch Black, Chaos Rising, Perfect
Order, Ascended Heroes, Mega Evolution, Phantasmal Flames, Scarlet & Violet 2023)
and five negative official posts. DOM wrappers are explicitly reconstructed from
observed metadata, not represented as complete historical captures. No full
copyrighted page bodies are committed. C0 raw captures were also replayed locally
against the adapter, including the actual Pokémon.com Perfect Order article.

Tests cover authority spoofing/schema changes, category/host/canonical mismatch,
banners, quotes, replies, hidden content, missing/ambiguous dates, PT without UTC,
PDT/PST, numeric Pacific offsets, DST gap/fold, UTC conflicts/rollover, API drift,
challenge pages, robots, caching/validators, retry/budget failure, RSS gaps/sitemap
reconciliation, immutable revisions/quarantine and separate Phase B human flags.
Protected canonical/runtime/dependency bytes are checked around offline tests.
