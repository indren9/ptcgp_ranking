# MARS — CHAT MADRE SUCCESSION PROTOCOL

**Status:** OPERATIVE
**Version:** v1
**Effective:** 2026-10-06

## Purpose

This protocol governs succession of the MARS Chat Madre without depending on conversation memory.
Its goal is to transfer only the minimum current operational context while preserving persistent authority,
history, FROZEN decisions, reproducibility, provenance, and the Acquisition/Core ↔ MARS boundary.

It does not create a new source of truth, decision register, issue register, artifact register, or methodology layer.

## Authority

During succession, authority remains domain-specific:

- current project state and scope → `docs/governance_current.md`;
- effective run parameters → selected YAML profile;
- code, tests and current technical documentation → live GitHub repository;
- task-specific FROZEN decisions/artifacts → their persistent references;
- run/rebuild state → existing state pointer / manifest / artifact / hash chain;
- issues → GitHub Issues where applicable;
- history → Git commits, PRs, releases, old chats, handoffs and preserved generations.

Andrea remains the final decision authority.
A technical PASS is not an approval of a methodological or structural change.
Silence or absence of an answer is never approval.

## Succession trigger

Use this protocol when the current Chat Madre is being replaced, intentionally compacted, or can no longer
reliably carry the current operational context.

Succession is not required for every ordinary new operational chat.

## Phase 1 — Freeze new work

Before handoff, the outgoing Chat Madre must stop opening new workstreams.
It may only close or persist the minimum context needed for safe succession.

Do not reopen FROZEN items, change methodology, mutate canonical acquisition policy, or rewrite history as part of succession.

## Phase 2 — Persist the minimum current context

Persist only what is not already recoverable from authoritative sources.
Prefer pointers over narrative duplication.

The succession packet should contain, when applicable:

- current objective and active workstream;
- exact branch/worktree or run identifier;
- unresolved blocker or pending Andrea decision;
- task-specific FROZEN references that materially constrain the next action;
- current process handle/PID only if a legitimate long-running local job is still active;
- exact next action that was authorized but not yet completed.

Do not copy the whole conversation history into the packet.

## Phase 3 — Successor bootstrap

The successor Chat Madre performs a read-only bootstrap first:

1. read the Project Instructions;
2. read `docs/governance_current.md` live from GitHub;
3. read this succession protocol;
4. inspect only the live GitHub/config/state files required by the active task;
5. read only the specific FROZEN decisions/artifacts that alter that task;
6. inspect the succession packet, if one exists;
7. compare any handoff claim against the current live authority before acting.

Old chats and historical documents are supporting history only.
A stale handoff must never override newer live state.

## Phase 4 — Freshness and readiness gate

Before new work, classify succession readiness as:

- `PASS` — current authority is consistent and the next action is unambiguous;
- `PASS WITH LIMITATIONS` — work may continue, but named gaps or stale references remain;
- `STOP` — there is an unresolved authority conflict, missing required artifact, or unsafe ambiguity.

When status is `STOP`, ask Andrea rather than resolving the conflict by assumption.

## Mandatory setup recommendation for every new chat

Whenever the Chat Madre opens or assigns a new chat, the dispatch must include:

- **preferred setup**;
- **reason**;
- **fallback Chat setup**.

Current recommendation rule:

- use **Work** when the task materially benefits from autonomous multi-step execution and Work is available;
- otherwise use **GPT-5.6 Sol — High** for governance, audit, methodology, complex refactor, difficult debugging, or cross-source verification;
- use **GPT-5.6 Sol — Medium** for bounded operational tasks with clear inputs, outputs, and acceptance criteria;
- do not default to the maximum reasoning level when a lower level is sufficient;
- if product model/mode availability has changed, verify what is actually available before recommending a setup.

A dispatch is incomplete if it omits the setup recommendation.

## Desktop Commander discipline

Desktop Commander is for genuinely local work: repository/worktree operations, terminal execution,
tests/builds, notebooks, local files, environments, schedulers, or desktop applications.

When Desktop Commander is used, follow `DESKTOP_COMMANDER_PLAYBOOK_v02` and this minimum discipline:

1. `inspect → plan → act → verify`;
2. identify the exact repository/worktree and branch before writes;
3. prefer small reversible changes;
4. for long processes: start once, retain the PID/process handle, and monitor that same process;
5. use `read_process_output` / existing-session interaction instead of starting new shells to poll;
6. do not use repeated `start_process + Start-Sleep` as a polling mechanism;
7. after timeout/disconnection, verify whether the existing job is still active before relaunching it;
8. verify final exit state, logs and produced artifacts; a printed `PASS` alone is not sufficient;
9. avoid destructive Git operations and history rewriting unless Andrea explicitly authorizes them.

Desktop Commander discipline applies to the Chat Madre and to every operational chat it dispatches.

## Minimum operational handoff format

When useful, the outgoing or operational chat should return:

`RESULT / EVIDENCE / CHANGES / BLOCKERS / NEXT`

For a Chat Madre succession, also include:

- `READINESS`: PASS / PASS WITH LIMITATIONS / STOP;
- `CURRENT POINTERS`: only the persistent sources needed to resume;
- `ACTIVE LOCAL STATE`: branch/worktree/PID only when still relevant;
- `PENDING ANDREA DECISION`: explicit, if any;
- `RECOMMENDED SETUP`: preferred setup + reason + fallback.

## STOP conditions

Stop before:

- changing MARS methodology or `MARSConfig` implicitly;
- changing canonical Core I/O contracts;
- reopening/replacing FROZEN decisions or artifacts;
- changing canonical acquisition policy;
- deleting history or canonical artifacts;
- assuming a resolution to a real authority conflict;
- treating a stale Project/Drive/OneDrive copy as newer than live GitHub/config/state authority.

Succession is complete only after the successor has passed the read-only readiness gate.
