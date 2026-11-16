# MARS — CURRENT VIEW

**Status:** CURRENT
**Reviewed:** 2026-11-16 (documentary GitHub readback; not runtime recertification)

## Authority

- Andrea is the final decision authority.
- For changing technical facts, use the live GitHub repository.
- Effective run parameters come from the selected YAML profile.
- FROZEN decisions and artifacts remain frozen unless Andrea explicitly reopens them.
- A technical PASS is not an approval of a methodological or structural change; Andrea's explicit approval is required where governance requires it.
- Silence or absence of an answer is never approval.
- Static Project copies must never override a newer authoritative live source.

## Current technical baseline

- Repository: `indren9/ptcgp_ranking`
- Canonical branch: `main`
- Resolve the current `main` HEAD live when freshness matters.
- Canonical production acquisition: Limitless Tournament API.
- Legacy HTML is historical validation / rollback material only; no silent fallback.
- Pokémon TCG Pocket and Pokémon TCG Live Standard are supported production targets.

Observed during the 2026-10-05 governance review:
- latest merged technical work: D3-R4 historical positive-proof freeze
- Pocket published completed set: B4 — Ruler of the Skies
- Pocket observed current set: B4a — Team Rocket's Ambition

These observations are context, not permanent authority. Re-check live sources when a task depends on current state.

## Architecture review and provenance — documentary CURRENT (2026-11-16)

- **Roadmap / architecture:** new ordinary development and expansion remain in **STANDBY** under Andrea's [Issue #38 decision](https://github.com/indren9/ptcgp_ranking/issues/38). Critical fixes require separate authorization. The old roadmap must not be rewritten; its definitive live source remains subject to reconciliation.
- **AR-01:** fail-closed MARS I/O validator bugfix **COMPLETED**, [PR #39 MERGED](https://github.com/indren9/ptcgp_ranking/pull/39) at `df30cd788d6217050f6ba3a59e7d367efa69ef5b`. [Post-merge Tests #180](https://github.com/indren9/ptcgp_ranking/actions/runs/43456283649): **SUCCESS, 1110 passed, 6 warnings**. [Merge close](https://github.com/indren9/ptcgp_ranking/issues/38#issuecomment-6858060305). This does **not** certify production provenance.
- **PROVENANCE-01:** C1/C2/C3 design and the five concrete contracts **PV-D01 through PV-D05 are APPROVED — CONTRACT ONLY**, **NOT IMPLEMENTED**. The authoritative record is [Issue #38](https://github.com/indren9/ptcgp_ranking/issues/38): [PV-D01 ratification](https://github.com/indren9/ptcgp_ranking/issues/38#issuecomment-6920036079), [PV-D02/D03/D04/D05 ratification](https://github.com/indren9/ptcgp_ranking/issues/38#issuecomment-6920944934); [decision history](https://github.com/indren9/ptcgp_ranking/issues/38#issuecomment-6919849478) remains unchanged.
- **Approved direction only:** deterministic scientific identities, separately identified operational attempts/publications; minimal verifiable evidence; coherent local promotion; fail-closed verified consumers; no assumed shared cloud commit. Implementation details and tests need a **new, explicit WRITE mandate**. Do not infer publication or methodological authorization.
- **PV-D01 prerequisite gate (2026-11-16):** specialist static descriptor inventory and canonical specification **DONE WITH LIMITATIONS**; independent synthetic checks report **14/14 positive and 6/6 negative families PASS**, byte/SHA-256 verified, with full *example* fixtures. **PV-02A WRITE READINESS = STOP**: nested descriptor/payload schema is not yet normative, AST scientific dependency closure and science/I/O boundary not qualified, actual numerical-build identities UNKNOWN. **Next:** narrowly scoped READ-ONLY schema/software-inventory close by original specialist, followed by independent review of changed parts. No coding, pipeline tests or production authorized. See [Notion MARS coordination record](https://app.notion.com/p/3f90b32ce54781c9969ad795f8b18808) for dated documentary status; contract authority remains [Issue #38](https://github.com/indren9/ptcgp_ranking/issues/38).
- **Separate / on hold:** `RT-01` canonical filename reconciliation and `AR-02` are separate; [PR #37](https://github.com/indren9/ptcgp_ranking/pull/37) remains **OPEN / DRAFT / UNMERGED**, `C.6` **HOLD**. [Issue #38](https://github.com/indren9/ptcgp_ranking/issues/38) remains OPEN. Preserve FROZEN, `MARSConfig`, Acquisition/Core separation and canonical CSV contracts.
- **Gates:** `IMPLEMENTATION = NOT AUTHORIZED`; `TESTS/WORKFLOWS = NOT AUTHORIZED` for the next mandate; `PRODUCTION = STOP` for unverified provenance. This is a **documentary snapshot**, not runtime, external-consumer or cloud-readiness certification.

Re-check the live repository, YAML and issue decisions for subsequent work; this section is a compact index, not a duplicate technical register.

## MARS / Core

Do not redefine methodology in this file.

Read current behavior from:
- `config/pocket.yaml`
- `config/tcg.yaml`
- `MARS_explained.md`
- `core/`
- `mars/`
- tests and production manifests

Methodology changes must be explicit, isolated from bugfix/refactor/acquisition/routing work, and approved separately.

## Governance workstream

MARS Governance VNEXT is **OPERATIVE**.
Cutover completed: 2026-10-05.

Purpose:
- reduce bootstrap overhead;
- remove stale duplication from the ChatGPT Project layer;
- preserve authority, provenance, reproducibility, traceability and FROZEN protection.

This governance workstream does not authorize changes to MARS science, canonical I/O contracts, acquisition policy, storage semantics or FROZEN artifacts.

## Bootstrap for a new chat

Normally read only:

1. Project Instructions.
2. This CURRENT VIEW.
3. Live GitHub sources required by the task.
4. Only the task-specific decisions/FROZEN items that alter its baseline.
5. Task inputs and required output.

If the new chat is replacing the Chat Madre, also read `docs/chat_madre_succession_protocol.md`
and complete its read-only readiness gate before opening new work.

Do not reconstruct the whole project history by default.

## New-chat setup discipline

Every Chat Madre dispatch must include a recommended setup, the reason, and a fallback Chat setup.

- Prefer Work when autonomous multi-step execution materially helps and Work is available.
- Otherwise prefer GPT-5.6 Sol — High for governance, audit, methodology, complex refactor, difficult debugging, or cross-source verification.
- Prefer GPT-5.6 Sol — Medium for bounded operational tasks with clear inputs and acceptance criteria.
- Do not default to the maximum reasoning level when a lower level is sufficient.
- If available model/mode options change, verify the current product availability before recommending a setup.

## Desktop Commander discipline

Use Desktop Commander only for genuinely local work and follow `DESKTOP_COMMANDER_PLAYBOOK_v02`.

For long processes: start once, retain the PID/process handle, monitor the same process,
do not create repeated `start_process + Start-Sleep` polling shells, verify an existing job
after timeout/disconnection before relaunching it, and verify final logs/artifacts rather than trusting a printed PASS alone.

The full Chat Madre succession rules live in `docs/chat_madre_succession_protocol.md`.

## History

History remains in:
- Git commits, PRs, tags and releases;
- old chats and handoffs;
- historical rebuild generations;
- frozen raw evidence;
- manifests and hashes;
- historical documents.

Current-state updates do not rewrite history.

## Retrieval

Prefer deterministic pointers:

- normal run → run manifest → outputs;
- Pocket public snapshot → publication state → public manifest;
- TCG Live public snapshot → dedicated publication state → public manifest;
- historical rebuild → `state/current.json` → generation → artifact inventory/hash;
- code/config/docs → GitHub live.

Do not create a new global artifact register unless existing mechanisms become insufficient.

## STOP conditions

Stop and ask Andrea before:
- changing MARS methodology;
- changing canonical I/O contracts;
- reopening or replacing FROZEN decisions/artifacts;
- changing canonical acquisition policy;
- deleting history or canonical artifacts;
- resolving a real authority conflict by assumption.

If a static Project source conflicts with GitHub live, treat the static copy as stale and report the conflict.
