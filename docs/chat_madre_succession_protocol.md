# MARS — CHAT MADRE SUCCESSION PROTOCOL

**Status:** PROPOSED — awaiting Andrea review (v1 remains operative on `main` until approved merge)
**Version:** v2 — proposed 2026-11-18
**V1 archival copy:** [chat_madre_succession_protocol_v1.md](chat_madre_succession_protocol_v1.md)
**Global protocol:** [Agent Operating Guide §1-bis](https://app.notion.com/p/4070b32ce54781f1aa75fe1ebf66a68c)

## Purpose

This document adds MARS-specific technical checks to the Agent Operating Guide §1-bis, which is the sole global protocol for nomination, bootstrap, succession, delegation and common authorizations. It is not an alternative global succession procedure.
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

Andrea remains the final decision authority. Only Andrea may nominate the Chat Madre CURRENT, with an identifiable original decision and effective date; a title, handoff, prior chat or self-declaration does not nominate or grant permissions.
Project Instructions govern static safety, Agent OS §1-bis governs the common procedure, and GitHub LIVE with effective YAML and the state/manifest/artifact/hash chain govern MARS technical facts.
If a procedural v1 clause conflicts with §1-bis, apply the approved global contract while preserving these compatible MARS technical checks.
A predecessor remains HISTORY after the nomination, with no retroactive transfer of authority.
A technical PASS is not an approval of a methodological or structural change.
Silence or absence of an answer is never approval.

## Succession trigger

A change of Chat Madre CURRENT occurs only after Andrea authorizes and names the successor. The original nomination source, conversation identity and effective date must be verified before attributing the role.

A compacted or ordinary operational chat is not automatically a successor. Loss of context alone does not grant a new chat the CURRENT role. Succession is not required for every ordinary new operational chat.

## Phase 1 — Freeze new work

After Andrea's succession decision, the outgoing Chat Madre, if available, stops opening new substantial workstreams. It may close active work safely or provide a short optional handoff, without making new substantive decisions.

Do not reopen FROZEN items, change methodology, mutate canonical acquisition policy, or rewrite history as part of succession.

## Phase 2 — Persist the minimum current context

An ordinary succession handoff is **optional**, not a precondition and not an emergency fallback if absent. Reconstruct CURRENT from the original LIVE sources. If useful and possible, persist one short, versioned handoff in an already governed destination. Prefer pointers over narrative duplication.

If created, the handoff may contain:
- current objective and active workstream;
- exact branch/worktree or run identifier;
- unresolved blocker or pending Andrea decision;
- task-specific FROZEN references that materially constrain the next action;
- current process handle/PID only if a legitimate long-running local job is still active;
- exact next action authorized but not yet completed.

Never copy whole conversations, registries, private data or secrets. A handoff is **HISTORY/DERIVED EVIDENCE**: it does not establish CURRENT, modify permissions or override GitHub LIVE.

## Phase 3 — Successor bootstrap

The successor's **entire first turn is rigorously READ-ONLY**. No Notion/GitHub or local writes, tests, CI, workflow, pointer, FROZEN or production changes. Perform only the following bounded checks:

1. Verify Andrea's explicit nomination, original source, effective date and the identity of the designated conversation. If not verifiable: STOP on actions requiring CURRENT authority.
2. Read the Project Instructions actually applicable, then the [Agent Operating Guide §1-bis](https://app.notion.com/p/4070b32ce54781f1aa75fe1ebf66a68c).
3. Read `docs/governance_current.md` on GitHub `main`, and this MARS specialist protocol. Check branch/commit LIVE; note stale CURRENT views as gaps rather than treating history as technical authority.
4. Inspect only the live GitHub, effective YAML, state/pointer → manifest → artifact → hash chain and task-specific FROZEN evidence actually required for the active mandate.
5. Consult an optional handoff only as history; verify any claim against original LIVE sources. A missing handoff alone is **not** STOP.
6. Distinguish VERIFIED, APPROVED, PROPOSED, HISTORY, UNKNOWN/STALE and GAP/PENDING_SYNC, and identify the exact resume point.

No task restarts or promotion at the end of this first turn.

## Phase 4 — Freshness and readiness gate

Report one explicit gate based on the READ-ONLY evidence:

- `PASS` — nomination and indispensable LIVE authority verified; no blocking conflicts.
- `PASS WITH LIMITATIONS` — safe bootstrap complete but specifically identified gaps or stale references remain; explain what cannot yet be done.
- `STOP` — required nomination, indispensable source, authorization or safe interpretation cannot be established.

**After EVERY gate, including PASS, the successor must STOP for Andrea's review and explicit next mandate.** PASS is readiness, not self-authorization. No automatic continuation of suspended work.

Selective fail-closed rule: absence of an optional handoff does not block independent recovery; if Notion is unavailable, declare it and proceed only with harmless READ-ONLY checks supported by other sufficient sources. Never substitute a snapshot for CURRENT or resolve authority conflict by assumption.

## Mandatory setup recommendation for every new chat

Whenever the Chat Madre opens or assigns a new operational chat, the dispatch must specify the **preferred setup**, **reason**, and **fallback Chat setup**. Chat is the default for bounded tasks; recommend Work only when multi-step execution materially helps and is actually available.

Verify the models, modes and reasoning levels available in the current product interface before recommending any named option. Select the least intensive sufficient setup for the task; do not hard-code historical model labels or assume maximum reasoning is always needed.

The dispatch must also state scope, sources, permissions and exclusions, intended evidence, output, SESSION CLOSE and STOP. No new authority follows from a setup recommendation.

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

If an optional Chat Madre succession handoff is prepared, also include:

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

A successor must STOP for Andrea after the first READ-ONLY gate even on PASS. Readiness alone does not complete the authorized operational handover or restart suspended tasks; await Andrea's explicit review/mandate.
