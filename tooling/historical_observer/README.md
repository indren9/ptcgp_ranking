# Historical observer: R3.3-A state persistence (candidate)

**Scope:** Windows observer evidence reliability only. This is **not** the
historical-rebuild runner and does not launch or authorize production workers.

StatePersistence.ps1 is the unchanged, SHA-256-verified R3.3-A candidate
prepared on 2026-10-19. Its retained baseline SHA-256 is
cc917300be281810a5c162608c1a9bb767504c5c80b31eb8bd23221a697ec28f.

It provides Write-R33StateJson to persist worker_launch.json and
worker_exit.json in an existing evidence directory. It stages data to a
unique file, flushes it, publishes without replacing a previous canonical
record, verifies the published bytes/JSON, and writes an explicit
OBSERVER_EVIDENCE_DEGRADED recovery record if canonical publication fails.
Failure to persist recovery does not count as worker/observer success.

## QA

Run on Windows PowerShell or PowerShell 7:

    pwsh -NoProfile -NonInteractive -File tooling/historical_observer/tests/test_state_persistence.ps1

The test creates only a new unique system temp directory, verifies 17
assertions including immutable-collision and failure-recovery behavior,
and never touches historical rebuild outputs or performs network access.
GitHub Actions runs this test on windows-latest; the existing Linux
pytest workflow remains unchanged.

## Explicitly excluded from this integration

- Invoke-WorkerObserved.ps1, NativeNoPipe.cs, TreeWatch.ps1, diagnostics,
  AuthorizedRun, Authorized30C, any production launcher or scheduler.
- No historical_rebuild command is invoked, no existing state/current.json
  is updated, and no writer locks are created.
- No changes to Acquisition/Core/MARS methodology, YAML, FROZEN, manifests
  or the public/latest pointer.
- No promotion of R3.3's full monitor to a production standard.

## Known limitations and next gate

Windows power loss, observer hard crash, simultaneous writers and recovery
failure are not solved by the test PASS. There is no guarantee of capturing
the native exit code if the process observer itself dies.

A separate code-security and concurrency review, hardened path/worktree
selection, live-process detection, integration tests with Windows Job Objects
and explicit approval are required before the full R3.3 monitor or its
productive execution path could be proposed for adoption.

This PR is the first non-productive component of a staged integration.
It is not authorization to run or publish any historical ranking.