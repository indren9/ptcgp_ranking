# R3.5 — Synthetic supervision and recovery candidate

**Status: NON-PRODUCTIVE, NOT MERGED.** This development component
cannot execute or authorize historical_rebuild. There is no scheduler,
publication, arbitrary command invocation, automatic retry or manual lock reset.

## Contract and operation

Invoke-R35SyntheticSupervisor.ps1 exposes only:
- StartSynthetic: invoke the existing R3.3 synthetic-only observer with an
  approved fixed synthetic worker fixture and constrained case. RepoRoot and
  PythonExe are explicit absolute paths; EvidenceDir is isolated, outside
  RepoRoot, and existing Windows reparse components are rejected.
- RecoverReadOnly: reconcile saved intent and observer evidence without
  launching, attaching to, terminating or retrying any worker; no writes.

StartSynthetic writes and fsyncs an immutable SHA-256 envelope
(r35_intent.json) **before** requesting the R3.3 worker start. CreateNew
is the exclusive claim for that evidence directory. Even after success,
the same directory is not reused; failure cannot trigger an automatic
relaunch. The intent identifies a planned attempt; it does not prove
that a worker actually started.

Read-only results always carry:
  can_execute=false; may_retry_automatically=false;
  production_success=false; canonical_artifacts_validated=false.

Possible outcomes:
- SYNTHETIC_SUCCESS_VERIFIED: matched launch/exit identity, exit code 0,
  drained Job Object, no degraded observer state.
- SYNTHETIC_FAILED or SYNTHETIC_TERMINATED: test-only terminal outcomes.
- WORKER_ACTIVE: live PID, creation UTC and executable match persisted
  identity. No process attachment or ownership takeover occurs.
- RECOVERY_REQUIRED: absent intent, absent exit/worker, PID reuse or
  ambiguous state. No restart until independently reconciled.
- EVIDENCE_DEGRADED: corrupt intent envelope, missing launch for exit,
  inconsistent worker evidence or observer error.

## Boundaries and unresolved problems

- The SHA-256 intent envelope is an internal consistency check, not a
  signature or external trusted provenance anchor. The R3.3 launch/exit
  files lack independent immutable hash anchoring.
- Directory claim is per-directory only, NOT the canonical writer.lock,
  and cannot guarantee global cross-worktree mutual exclusion.
- The worker still starts before the R3.3 worker_launch.json write;
  R3.5 pre-start intent closes only the intent gap, not native exit-code
  preservation after observer crash or OS power loss.
- PID-based recovery is conservative but cannot prove process ancestry
  after a crash. Windows reparse swaps/TOCTOU, remote storage durability,
  process identity spoofing, ACLs, secret-bearing minidumps, retention,
  and concurrent writers need dedicated review.
- A successful synthetic worker does NOT validate historical_rebuild
  state/current.json, generations, manifests, hashes, Core/MARS, or
  production authorization. The Runner's writer.lock and validation
  remain authoritative and unchanged.

## QA and next gate

Separate GitHub Actions job executes
tooling/historical_observer/tests/test_r35_supervisor.ps1
under Windows PowerShell 5.1 and PowerShell 7, using isolated finite
synthetic workers. It checks pre-launch durable intent, collision and
retry denial, nonzero exit, read-only recovery, lost evidence, corrupt
records, identity mismatch and absence of canonical output changes.

PR integration, adoption, productive execution, scheduler or a real
historical worker all require separate explicit approval from Andrea.
An R3.5 technical PASS is *not* production readiness.
