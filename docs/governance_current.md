# MARS — CURRENT VIEW

**Status:** CURRENT
**Reviewed:** 2026-10-06

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
