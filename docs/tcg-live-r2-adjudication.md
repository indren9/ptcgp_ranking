# Gate 1.11-D2-R2 — first-party adjudication and targeted Limitless fallback

**R3 update:** this document and the R2 JSON preserve the historical R2 outcome
below. Chat Madre/user subsequently approved PAF/SSP/PRE at their published
17:00 UTC through `GATE_1_11_D2_R3`; PRE uses the independently confirmed
2025-01-16 date. These are **HUMAN_ADJUDICATED**, not machine-verified or
Limitless-derived. The current complete, still-unreviewed catalog and 22-window
preview are described in the [current boundary review](tcg-live-historical-boundary-review.md).
R2's evidence, observations and replay remain unchanged; the R3 approvals live
in a separate register. No fallback is used to select the R3 times.

**NOT_READY.** All three expansion starts still have two plausible clock hypotheses.
No canonical time is proposed, no candidate is reviewed, and no historical MARS
rebuild or Tournament API acquisition was executed. The fallback stops at its
feasibility audit, before tournament discovery.

Branch: `feat/tcg-historical-boundary-catalog`; baseline HEAD
`6b53e689141f63808664f05c6abca86651a6bdf5`; baseline main
`742cb24eb3f888d92f214b9fc5363b9d39f72da5`.
[PR #23](https://github.com/indren9/ptcgp_ranking/pull/23) remains draft and unmerged.

## First-party research

The three original full-expansion announcements and two independent letters were
captured again. C1 verifies the official forum authorship of all five, and their
normalized first-post contents match the R1 evidence. No C1 rule or prior source
state was changed. Explicit UTC is not given precedence over a conflicting local
clock.

| Expansion | First-party result | Candidate A | Candidate B | Adjudication status |
|---|---|---|---|---|
| PAF | January PDT remains invalid; independent exact hour not established | 2024-01-25T17:00:00Z | 2024-01-25T18:00:00Z | CANONICAL_PENDING |
| SSP | 10:00 PT conflicts with explicit 17:00 UTC | 2024-11-07T17:00:00Z | 2024-11-07T18:00:00Z | OFFICIAL_CONFLICT |
| PRE | Date independently confirmed as 2025-01-16; original year and clock contradictions remain | 2025-01-16T17:00:00Z | 2025-01-16T18:00:00Z | OFFICIAL_CONFLICT |

For **PAF**, the [release announcement](https://community.pokemon.com/en-us/discussion/8969/scarlet-violet-paldean-fates-release)
supplies explicit 17:00 UTC and seasonally invalid January PDT. The 18:00
hypothesis is conditional on the author intending Pacific civil time, which is
PST on that date. It is not an accepted correction. The newly located
[Pokémon.com UK now-playable article](https://www.pokemon.com/uk/news/scarlet-violet-paldean-fates-now-playable-in-pokemon-tcg-live)
has indexed date-only context, not a resolving release clock.

For **SSP**, the [release announcement](https://community.pokemon.com/en-us/discussion/13953/scarlet-violet-surging-sparks-release)
contains the two competing clocks. The independent
[November 5 letter](https://community.pokemon.com/en-us/discussion/13239/letter-to-the-community-november-5-2024)
confirms November 7 without an hour. The
[UK now-playable article](https://www.pokemon.com/uk/news/scarlet-violet-surging-sparks-now-playable-in-pokemon-tcg-live)
does not resolve the hour in its indexed content.

For **PRE**, the [December 3 letter](https://community.pokemon.com/en-us/discussion/13604/letter-to-the-community-december-3-2024)
independently supplies January 16, 2025. The
[release announcement](https://community.pokemon.com/en-us/discussion/15414/scarlet-violet-prismatic-evolutions-release)
still literally says 2024 and retains the clock conflict. R2 explicitly constructs
two hypotheses using that independently confirmed date and the two full-expansion
clock fields. It does not edit the original evidence. The 18:00 hypothesis comes
from converting local 10:00 Pacific in winter; it does **not** come from the
[Prismatic Parade event clock](https://www.pokemon.com/uk/news/eevees-prismatic-parade-event-in-pokemon-trading-card-game-live).

Direct captures of all three UK editorial pages returned HTTP 200 Incapsula
challenge bodies. Their raw digests describe those challenge bodies, not articles.
Search-indexed text is retained only as discovery/context, never as machine
authority. No challenge bypass was attempted. This bounded research found no
independent first-party statement selecting one clock; it does not prove that
no such source exists elsewhere. Earlier patch/maintenance findings remain in
the [R1 review](tcg-live-historical-boundary-review.md).

## Fallback feasibility and stopping decision

The [Tournament API documentation](https://docs.limitlesstcg.com/developer/tournaments)
defines `date` as the organizer's **scheduled** start. It is not an actual start,
registration deadline, deck submission, creation, or card-use timestamp. The
documented standings and pairings expose no historical deck-version timestamp.
The list endpoint documents game/format/organizer and pagination filters, without
a date interval filter. R2 neither invents date filters nor paginates history.

The [organizer settings](https://docs.limitlesstcg.com/organizer/reference)
allow a tournament to start later than its configured time, and permit late
registration/deck submission in the first two Swiss rounds. The documented lock
on changes by already-playing participants is relevant but does not prove that
a current deck existed by the scheduled start.

The [player guide](https://docs.limitlesstcg.com/player/decklists) allows resubmission
while submission is open and says prior submissions are discarded. It does not
establish that organizers can edit archived decklists. R2 therefore records the
post-event immutability/admin-edit guarantee as **NOT_ESTABLISHED**, rather than
claiming proven retroactive editing. There is no verified historical revision
trail binding today's API deck contents to the candidate hour.

The [round schedule](https://docs.limitlesstcg.com/organizer/schedule) is also
editable, including during a round. It cannot replace the missing immutable
historical time evidence.

**Actual fallback: INCONCLUSIVE at preflight for PAF, SSP and PRE.** The scheduled
timestamp alone already fails the required inference. If unsafe retroactive edits
are positively established for future evidence, the engine returns **UNSAFE** and
stops. Unknown semantics also fail closed, without asserting an edit occurred.

| Expansion | Fallback needed? | Planned discriminating interval, UTC | API queries | Tournaments / decks inspected | Earliest positive | Empirical bound | Eliminated / remaining |
|---|---|---|---:|---:|---|---|---|
| PAF | Yes; preflight blocked | [2024-01-25 17:00, 18:00) | 0 | 0 / 0 | None acquired | None | 0 / both |
| SSP | Yes; preflight blocked | [2024-11-07 17:00, 18:00) | 0 | 0 / 0 | None acquired | None | 0 / both |
| PRE | Yes; preflight blocked | [2025-01-16 17:00, 18:00) | 0 | 0 / 0 | None acquired | None | 0 / both |

These are planned intervals, not claims of completed searches. No margin was
needed and no search was broadened. Zero acquisitions provide no negative
evidence about card availability.

## Implementation, card identity and resume

`historical_boundaries/targeted_fallback.py` is an isolated research engine.
Only PAF/SSP/PRE are eligible, only after unresolved first-party research. A
coherent first-party start skips fallback entirely. There is deliberately **no
enabled live Tournament API adapter**, because its documented data cannot satisfy
the temporal contract. Passing an assumed timestamp would not repair this.

The bounded-provider tests use explicitly synthetic proofs. A future real adapter
would need independently audited evidence of valid PTCGL play by time T, tied to
the exact tournament/player/deck digest and immutable history. The ordinary API
`date` field cannot serve as that proof.

Cards must come from actual `pokemon`/`trainer`/`energy` deck entries: positive
count, exact target `set`, numeric card `number`, and nonempty name. Observations
retain the set-number ID and card URL, tournament ID and scheduled timestamp
with its semantics, player identity, separately proven presence time, deck/raw
SHA256, and temporal proof references. Archetype names, titles, metadata, and
same-name prints in another set are insufficient. No actual card observation was
acquired in this gate.

Checkpoint location: `.cache/gate111-d2-r2/fallback/<set>/<context-digest>/`.
The context includes the plan, audit and inspection policy. Checkpoints retain
fetched raw hashes, completed tournaments, individual inspected decks, positive
observations and the earliest positive in the inspected evidence. Raw and
checkpoint integrity is validated on resume. Completed valid evidence is not
downloaded or inspected again. A crash after a positive per-deck checkpoint still
honors the stop immediately on resume. Corrupt evidence fails closed.

The engine stops at the first validated observation that discriminates candidates;
the retained earliest observation is the earliest **within inspected evidence**,
not a claim of globally earliest play. A bound eliminates only candidates strictly
later than T. No observation, an observation at 18:00, or one later than both
candidates cannot select 18:00. If all candidates are contradicted, no arbitrary
replacement timestamp is invented. Progress is deterministic display-only output.

Only a surviving first-party candidate can be proposed, separately from the
canonical catalog, with `HUMAN_REVIEW_CANDIDATE`, `LIMITLESS_CORROBORATED` and
`reviewed=false`. This differs from `MACHINE_VERIFIED_SOURCE` and from explicit
`HUMAN_ADJUDICATED` approval, neither of which Limitless supplies.

## Artifacts, validation and remaining blockers

- Separate unreviewed metadata: `data/candidates/releases/tcg_live_r2_adjudication.json`.
- Offline replay: `python -m historical_boundaries.r2`; `--write` regenerates only
  that fixed R2 metadata artifact. Both use the frozen ledger and make no network
  request. Captures and checkpoints remain in ignored research storage.
- Original candidate, evidence ledger and window preview are unchanged. Twenty
  expansion starts remain frozen; the three gaps remain null and no complete
  expansion-window preview is emitted. All review flags remain false.
- All five named protected files and the original candidate/ledger/preview are
  unchanged. R2 performs no production catalog promotion, OneDrive publication,
  historical NEXT/ALL/WINDOW execution, dependency change, scheduler/launcher/
  routing change or merge.
- The extended before/after audit has 29 of 33 hashes identical. Four existing
  Pocket output files changed independently: the pre-existing Windows task
  `PTCGP Ranking - Pocket Daily` ran on September 19, 2026 at 09:58:33 Europe/Rome.
  Its launcher log records B4a outputs at 10:08:56, OneDrive publication at
  10:08:57 and exit 0 at 10:09:12. This was not invoked by R2 or its tests. Those
  current outputs were preserved; they are not falsely reported as unchanged.
- Focused tests cover the requested A–P cases, partial resume, interruption after
  a positive checkpoint, tamper detection, raw tournament/player binding,
  target-set identity, strict interval bounds and offline artifact replay.
- Python 3.14.7 validation: final R2 tests **50 passed**; combined R2/D2/R1,
  observer, release-candidate and historical-runner regression **319 passed**
  (before adding the two final artifact replay/tamper tests, both included in
  the final focused and full runs); full pytest **833 passed**, zero failures,
  six pre-existing plotting deprecation warnings. `pip check`: **PASS**.
  Both offline artifact checks and `git diff --check` pass. Local logs and
  before/after audit records are in `.cache/gate111-d2-r2/`.

Remaining blockers: independent first-party evidence resolving each release clock,
or a separately proven immutable historical-use source that makes the bounded
fallback feasible; then explicit human approval of any unique proposed starts.
**Verdict: NOT_READY.**
