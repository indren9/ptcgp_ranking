# MARS — CURRENT VIEW

**Status:** CURRENT
**Documentation reviewed:** 2026-11-18 — Agent OS 2.0 procedural alignment only.
**Technical observations:** historical observations from 2026-10-05 remain preserved, not refreshed.
**Technical certification:** NOT PERFORMED by this document review; obtain current technical evidence from GitHub LIVE, effective YAML and the state/manifest/artifact/hash chain.

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

## P2 — local TCG operational alignment (2026-11-27)

**Owner decision:** WP4 accepted, P2 CLOSED in the [P2 WBS](https://app.notion.com/p/4230b32ce547811983cafed6a7286d13).
This entry records the approved local operation, not a merge or a new production certification. It is prepared on an isolated review branch; GitHub main remains authoritative until the separately approved integration.

- Local TCG deployment: `4800dadcdf17a823229f6804f88cf9a2d017060d` (parent WP3/G2 `1e6cf865c640f9e03b69c4fd24e5f0626623b8ab`), based on main `d39c7eca97c8e0eb85fda770085f77fb4a13674b`. Both source commits are retained intact.
- TCG daily 10:15 local, dedicated checkout, common launcher and global mutex, explicit `--publish-private`; Pocket daily 09:15 and its original checkout unchanged. Public GitHub TCG demonstration is independent and not auto-refreshed.
- Local private TCG ranking is per completed expansion; verified manifests determine latest-completed. Persistent local canonical RAW with private OneDrive archive backup replaces mandatory S3 **only on this local private route**. See [TCG production contract addendum](ptcg_live_production.md#p2-local-private-operation--2026-11-27) and [RAW contract](tcg-private-local-raw-contract.md).
- At the WP4 gate: 30C latest-completed, DLR CURRENT; the installed C.4 catalog blob retains its original candidate provenance. That provenance is historical; owner approval of local deployment is recorded separately. No FROZEN artifact was rewritten.
- G4 bootstrap published the approved 30C bundle privately without new acquisition. G6 ordinary scheduled-task exercise passed: 30C NOOP, zero network, zero publications, exit 0, mutex released. G6 did not exercise a future real rollover. See the RAW contract for evidence hashes and limitations.

[Issue #38](https://github.com/indren9/ptcgp_ranking/issues/38) architecture standby remains in force; [Issue #36](https://github.com/indren9/ptcgp_ranking/issues/36) remains open and [PR #37](https://github.com/indren9/ptcgp_ranking/pull/37) remains held. This alignment does not import the broader PR or authorize its merge, future tests, service deactivation, new roadmap work or methodology changes.

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

Use the [Agent Operating Guide §1-bis](https://app.notion.com/p/4070b32ce54781f1aa75fe1ebf66a68c) for global bootstrap, delegation, succession and common authorizations. The [MARS specialist succession protocol v2](chat_madre_succession_protocol.md) is OPERATIVE / APPROVED (Andrea, 2026-11-18) and adds compatible technical checks only.

For an ordinary operational chat, recover only the necessary context:

1. Applicable Project Instructions.
2. Agent Operating Guide §1-bis when pertinent.
3. This CURRENT VIEW and task-relevant GitHub LIVE sources.
4. Effective YAML, state/pointer → manifest → artifact → hash and task-specific decisions/FROZEN only when needed.
5. Task inputs, authorization boundaries and expected output.

**New Chat Madre successor — protected first turn:** verify Andrea's explicit nomination and original source. Keep the entire first turn READ-ONLY, apply the specialist v2 protocol, report `PASS / PASS WITH LIMITATIONS / STOP`, then **STOP for Andrea even after PASS**. No automatic restart or promotion. An optional handoff is HISTORY, not an authorization, prerequisite or nomination; its absence alone is not STOP.

Do not reconstruct the entire project history by default, or treat a Notion/static snapshot as technical CURRENT.

## New-chat delegation and setup discipline

Every Chat Madre dispatch must state mandate, task-specific LIVE sources, scope and exclusions, permissions, expected evidence/output, `SESSION CLOSE`, `STOP`, a recommended setup, its reason and a fallback Chat setup.

- **Chat is the default** for bounded tasks. Recommend Work only if multi-step execution materially helps and Work is available; always give a Chat fallback.
- Verify current model, mode and reasoning options in the product interface before naming a specific one. Do not hard-code obsolete model labels.
- Choose the least intensive reasoning level sufficient for the task, not the maximum by default.
- A setup suggestion or QA PASS does not grant authority. Protected changes and substantial decisions remain subject to Andrea's approval.

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
