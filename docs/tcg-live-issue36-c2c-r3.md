# MARS / Issue #36 — C.2C-R3 isolated historical_import startup

Authority: [Andrea, 2026-11-07, C2C-R3 APPROVED WRITE LIMITED](https://github.com/indren9/ptcgp_ranking/issues/36#issuecomment-6709737575).
Scope: only C2C-R1-01 (MEDIUM); C2C-R1-02 (LOW) remains closed.
Initial candidate revision: `f30d728a2aabf07556b1bc6f0953a7a1a9b8d8ab`.

## Supported CLI (trusted bootstrap)

The old `python -m scripts.tcg_live_historical_import --request ...` is **UNSAFE and unsupported**:
Python initializes `scripts/__init__.py` *before* the importer's integrity checks.
A late rejection does not prevent a sentinel inside an untracked `__init__.py` from running.

**Use the following trusted, in-memory stdlib bootstrap**. The code supplied to
`-c` is the trust anchor; copy it from this reviewed contract, **not** from an
unverified repository-local script. On Windows PowerShell:

```powershell
$repo = 'C:\dev\Pokemon\ptcgp-f08r22-20261101'  # replace with candidate worktree
$request = 'C:\absolute\path\to\request.json'    # replace with pinned request
$bootstrap = @'
import json
from pathlib import Path
import runpy
import subprocess
import sys

if sys.flags.isolated != 1:
    raise SystemExit("isolated Python -I required")
repo, request = (Path(s).absolute() for s in sys.argv[1:3])
launcher = repo / "tcg_live_historical_import_safe.py"
if (not repo.is_dir() or repo.is_symlink()
        or getattr(repo, "is_junction", lambda: False)()
        or not launcher.is_file() or launcher.is_symlink()):
    raise SystemExit("unsafe launcher/repository path")
revision = json.loads(request.read_bytes())["software_revision"]
head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
if revision != head:
    raise SystemExit("requested revision is not HEAD")
blob = subprocess.check_output(["git", "-C", str(repo), "show",
                                revision + ":tcg_live_historical_import_safe.py"])
if launcher.read_bytes().replace(b"\r\n", b"\n") != blob:
    raise SystemExit("launcher not pinned to Git blob")
sys._mars_historical_import_bootstrap_verified = True
sys.argv = [str(launcher), "--request", str(request)]
runpy.run_path(str(launcher), run_name="__main__")
'@
python -I -c $bootstrap $repo $request
```

The snippet is also embedded verbatim as `BOOTSTRAP` in
`tests/test_tcg_live_historical_import.py`, where it is executed by real
Python subprocesses. The launcher refuses direct script execution if the
isolated bootstrap marker is absent. The importer refuses direct CLI invocation.
These late guards prevent accidental misuse; **they are not** substitutes
for the pre-execution pin.

## Security ordering and runtime scope

1. `python -I -c` starts with no repository path on Python's import search path.
   Only Python's interpreter, standard library, installed third-party runtime
   and the explicitly supplied trusted inline bootstrap execute before the pin.
2. The bootstrap reads the request's declared full Git revision, verifies local
   HEAD, and compares the launcher's checkout bytes with the raw committed Git
   blob, allowing **only** literal CRLF→LF normalization.
3. Only after this does the launcher run. It verifies the Git top-level,
   requested HEAD, index identity, all tracked runtime file bytes, and all
   untracked runtime paths before adding the worktree to `sys.path`.
4. The pinned scope includes `scripts` **as an entire package** (including
   any tracked or untracked `__init__.py`), the historical importer, acquisition,
   historical_rebuild, core, mars, pipelines, sources, storage, config,
   data/reference, domain, reporting, utils, and the launcher.
   An unexpected runtime file is rejected even when Git would ignore it.
5. Existing generated `__pycache__/*.pyc` are permitted only because the
   launcher selects a fresh, outside-repository `sys.pycache_prefix` and
   disables bytecode writes **before** importing any repository module.
   Non-cache, untracked Python/extension/hook files are not allowed.
6. For every tracked file, calculate Git's raw object SHA-1 on actual checkout
   bytes (with the restricted CRLF exception); do **not** use `git hash-object
   --path` or depend on configurable clean/smudge filters. Reject any effective
   `filter` or `working-tree-encoding` attributes for runtime paths, including
   attributes coming from `.git/info/attributes`.
7. A repository-local import guard checks every later Python module resolution
   against the pinned file inventory. An unrelated, untracked root-level file
   such as `yaml.py` cannot shadow an installed dependency and execute.
   Unexpected local package imports outside the verified dependency scope fail
   closed before their source is loaded.
8. Only then is the `scripts.tcg_live_historical_import` module loaded. The
   importer repeats the complete pin on initial verification, before manifest
   sealing, and at the final pre-rename gate. The import manifest continues
   to record the importer's actual raw-byte `implementation_sha256` and adds
   `startup_launcher_sha256`. Previously verified manifests are untouched.

## Limits and non-goals

The trust model excludes a compromised interpreter, standard library,
installed third-party packages, Git executable/object store, malicious operator
changes to the trusted inline bootstrap, and TOCTOU attacker mutations after
the last check. This is not an OS sandbox or atomic seal. Direct `python -m`
can still execute `scripts/__init__.py` before its *late* rejection: therefore
only the **isolated -c bootstrap path** is claimed safe.

No LIVE acquisition, API calls, daily bridge, publish, deployment, Pocket,
productive YAML, FROZEN source, Core/MARS method, state/pointer or historical
rebuild transformations are involved. The four independent production blockers
remain unchanged (missing original source URLs; four invalid_record PBL;
unproven freshness/completeness; absent daily/publication bridge).

## Evidence and gate

The R3 tests run the old `python -m` in an isolated synthetic repository with
an innocuous sentinel and demonstrate its execution. The supported -I bootstrap
must reject the same untracked `scripts/__init__.py` **without executing the
sentinel**. Additional negatives cover modified tracked dependencies, untracked
dependencies, anomalous Git attributes, altered HEAD, and modified launcher;
positive synthetic CRLF execution must complete the real offline import with
its manifest/QA. Original historical CRI/PBL sources are never used by tests.

R3 is a technical implementation only; independent C.2C-R3-R1 review by the
Chat Madre 05 is mandatory before closure. No merge or production authorization.
