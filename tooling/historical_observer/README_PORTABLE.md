# R3.3 portable Windows observer — synthetic and read-only candidate

**Scope:** instrumented subprocess observation, evidence persistence, and
read-only historical run preview. This is NOT a production launcher or
scheduler. No mode can launch a historical rebuild.

This review branch is based on the unmerged R3.3-A persistence PR #33:
StatePersistence.ps1 is inherited unchanged from that base PR, SHA-256
cc917300be281810a5c162608c1a9bb767504c5c80b31eb8bd23221a697ec28f.

## Portability and lifecycle

- Invoke-WorkerObserved.ps1 accepts mandatory absolute RepoRoot and PythonExe
  paths. There are no hardcoded worktree or virtualenv locations.
- InspectRun passes these to RunScopeGuard.ps1 and checks reviewed window IDs,
  input/YAML SHA-256, clean Git main / pinned HEAD, checkpoint, writer lock.
  A good preview still returns PREVIEW_ONLY_NOT_AUTHORIZED.
- r2_capture.ps1 takes RepoRoot to locate the writer lock.
- Synthetic mode executes only tests/fixtures/synthetic_worker.py with a
  constrained SyntheticCase. Caller-supplied WorkerArguments are rejected.
  AuthorizedRun and Authorized30C are not supported.
- Synthetic evidence must remain outside RepoRoot. All existing components
  of repository, evidence and preview-input paths are rejected if they are
  Windows reparse points (junction/symlink/mount); the evidence destination
  is checked again after creation. This fail-closed rule may reject a worktree
  or environment reached through a legitimate junction.
- NativeNoPipe.cs observes the entire owned Windows Job Object, including
  descendants, and captures worker exit codes and evidence of process exit.
- Preexisting state JSON generates explicit degraded-state recovery; it
  never silently overwrites prior worker launch or exit evidence.

## Windows-only regression tests

- test_windows_lifecycle.ps1: 21 assertions — normal exit, nonzero exit,
  child process joined/drained, no orphan, state collision recovery,
  forbidden productive mode/argument injection, no worktree writes.
- test_preview_portability.ps1: 11 assertions — temporary Git repository
  with spaces in path, read-only inspection, SHA/head/window/intent rejection,
  input confinement and no output writes.
- test_controlled_termination.ps1: WCT result, nonempty minidump and file readback — deliberate synthetic
  termination, empty Job Object, diagnostic event and no canonical writes.

All tests are offline and create uniquely named system temporary directories.
For local runs, set MARS_TEST_PYTHON to a known Python executable or ensure
python.exe is discoverable in PATH. GitHub Actions runs under Windows
PowerShell 5.1 and PowerShell 7 with Python 3.14.

## Exclusions and limitations

- No production mode, scheduler, authorized historical launch or publishing.
- No change to MARS/Core science, YAML, FROZEN, historical outputs or pointers.
- A hard observer crash can still lose the native worker exit status;
  this candidate does not implement independent OS exit capture.
- Sudden power loss, genuinely concurrent writers, and filesystem races
  (including reparse-point swaps between checks) are not proven safe.
- Preview is NOT the Runner full semantic/artifact validation.
- Git status commands can still block in an unhealthy Git environment.
- Synthetic capture tests do not prove WCT/minidump behavior under a real
  long-running productive historical worker.

**Gate:** review CI and code, then obtain explicit Andrea approval before
any merges. PR #34 must not turn either this component or PR #33 into an
automatically adopted productive monitor.