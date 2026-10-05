# MARS — CURRENT VIEW

**Status:** CURRENT
**Reviewed:** 2026-10-05

## Authority

- Andrea is the final decision authority.
- For changing technical facts, use the live GitHub repository.
- Effective run parameters come from the selected YAML profile.
- FROZEN decisions and artifacts remain frozen unless Andrea explicitly reopens them.
- Static Project copies must never override a newer authoritative live source.

## Current technical baseline

- Repository: `indren9/ptcgp_ranking`
- Canonical branch: `main`
- Resolve the current `main` HEAD live when freshness matters.
- Canonical production acquisition: Limitless Tournament API.
- Legacy HTML is historical validation / rollback material only; no silent fallback.
- Pokémon TCG Pocket and Pokémon TCG Live Standard are supported production targets.

Observed during the 2026-10-05 governance review:
- `main` HEAD: `9715e46d8cbbdcdbaa50aa6d8e5196fee62ba9e0`
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

Current workstream: MARS Governance VNEXT.

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

Do not reconstruct the whole project history by default.

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
