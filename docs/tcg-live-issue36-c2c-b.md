# C.2C-B — isolated historical_import startup

Authority: Andrea, architecture B approved 2026-11-07; bounded implementation,
local tests, candidate-branch commit/push and CI explicitly approved in this session.
Baseline: `619ee3cb5a618ad057a0ba1804ab1115fab75541`.
Decision: https://github.com/indren9/ptcgp_ranking/issues/36#issuecomment-6711083756

## Supported entrypoint and single Git policy

This supersedes the R3 startup command, which does not neutralize fsmonitor.
Copy the following reviewed trust anchor from this document obtained through a
trusted channel, into an external string/file; never execute or extract it from
an unverified local checkout in operational use. Supply it as `python -I -c`
with absolute repository and request paths. On PowerShell, use a literal
here-string `$bootstrap = @'` (own line), the code below, then `'@` (own line),
and `python -I -c $bootstrap $repo $request`. On POSIX, assign the literal code
with `bootstrap=$(cat <<'PY' ... PY)` then use
`python -I -c "$bootstrap" "$repo" "$request"`.

The literal below is byte-for-byte checked against `BOOTSTRAP` in the launcher.
It defines the single Git policy and passes the exact callable to the verified
launcher and importer via the isolated interpreter. Library verification uses
that same literal's factory; there is no second Git policy implementation.

```python
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

# One policy for bootstrap, launcher and importer. No repository command runs
# until the trusted executable reports a supported version outside the checkout.
def make_git(repo):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    # Explicit absolute PATH lookup avoids Windows' implicit current-directory
    # executable search (a checkout-local git.exe is not a trust anchor).
    executable = None
    names = ("git.exe", "git.cmd", "git.bat", "git.com") if os.name == "nt" else ("git",)
    for entry in env.get("PATH", "").split(os.pathsep):
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        for name in names:
            candidate = directory / name
            if (candidate.is_file() and os.access(candidate, os.X_OK)
                    and not candidate.resolve().is_relative_to(Path(repo).resolve())):
                executable = str(candidate)
                break
        if executable:
            break
    if not executable:
        raise ValueError("historical_import startup: Git executable missing")
    version = subprocess.run([executable, "--version"], cwd=tempfile.gettempdir(),
                             env=env, capture_output=True)
    match = re.fullmatch(rb"git version (\d+)\.(\d+)\.(\d+)(?:[.\-][A-Za-z0-9.\-]+)?\s*",
                         version.stdout)
    if version.returncode or not match or tuple(map(int, match.groups())) < (2, 36, 0):
        raise ValueError("historical_import startup: Git >=2.36.0 required; version probe failed/unsupported")
    options = ["--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.hooksPath=" + os.devnull, "-c", "core.untrackedCache=false",
               "-c", "core.attributesFile=" + os.devnull, "-c", "maintenance.auto=false",
               "-c", "gc.auto=0", "-c", "protocol.allow=never"]
    allowed = {"rev-parse", "ls-tree", "ls-files", "check-attr", "cat-file"}
    def git(repo, *args, stdin=None):
        if not args or args[0] not in allowed:
            raise ValueError("historical_import startup: Git command outside read-only policy")
        proc = subprocess.run([executable, *options, "-C", str(repo), *args],
                              input=stdin, env=env, capture_output=True)
        if proc.returncode:
            raise ValueError("historical_import startup: Git verification failed: " + args[0])
        return proc.stdout
    return git


def bootstrap():
    if sys.flags.isolated != 1:
        raise ValueError("isolated Python -I required")
    if len(sys.argv) != 3:
        raise ValueError("expected repository and request")
    repo, request = (Path(s).absolute() for s in sys.argv[1:3])
    git = make_git(repo)
    launcher = repo / "tcg_live_historical_import_safe.py"
    if (not repo.is_dir() or repo.is_symlink()
            or getattr(repo, "is_junction", lambda: False)()
            or not launcher.is_file() or launcher.is_symlink()
            or not request.is_file() or request.is_symlink()):
        raise ValueError("unsafe launcher/repository/request path")
    revision = json.loads(request.read_bytes())["software_revision"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("expected full SHA-1 revision")
    if git(repo, "rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("requested revision is not HEAD")
    blob = git(repo, "cat-file", "blob", revision + ":tcg_live_historical_import_safe.py")
    raw = launcher.read_bytes()
    if raw != blob and raw.replace(b"\r\n", b"\n") != blob:
        raise ValueError("launcher not pinned to Git blob")
    sys._mars_historical_git = git
    sys._mars_historical_import_bootstrap_verified = True
    sys.argv = [str(launcher), "--request", str(request)]
    # Execute the exact verified bytes; don't reopen the launcher through runpy.
    exec(compile(raw, str(launcher), "exec"),
         {"__name__": "__main__", "__file__": str(launcher)})

if __name__ == "__main__":
    bootstrap()
```

Before any repository command, Git --version runs outside the checkout using
only the trusted executable. Git environment overrides are removed. System and
global Git config are disabled; local config callbacks are overridden before
commands execute. fsmonitor is false, hooks point to the OS null device, auto
maintenance is disabled and protocol use is forbidden. Only read-only plumbing
commands are permitted. No diff, textconv, checkout or filter invocation is used.
Effective repository attributes still fail closed; raw bytes are compared
without clean/smudge filters. The raw launcher bytes checked are those executed.

The external bootstrap pins HEAD and launcher; the launcher verifies the index,
tracked and untracked/ignored runtime dependencies, attributes and raw Git blobs
before importing repository code. Existing LF/CRLF compatibility, pycache
isolation and local import guard remain. The importer reuses the same policy at
initial verification and the existing sealing/rename checks. Manifest/lineage
formats and hashes are unchanged; no migration of historical manifests occurs.

## Git support boundary

Required support floor: Git 2.36.0. Earlier versions are refused before any
repository command. This floor is derived from the upstream introduction of
boolean fsmonitor handling, not the version number alone:
- https://github.com/git/git/blob/v2.36.0/fsmonitor-settings.c
- https://github.com/git/git/blob/v2.36.0/Documentation/config/core.txt
- https://github.com/git/git/blob/v2.35.1/fsmonitor.c

The v2.36.0 settings code interprets false as disabled outside platform-specific
IPC enablement; v2.35.1 treats the setting as a hook pathname. This applies to
Windows and Linux. Runtime tests use actual installed Git versions recorded in
the evidence; the exact binary versions and results are recorded below. Version
refusal tests use synthetic executables, separately from real-Git sentinel tests.

## Acceptance and limits

Tests use real isolated subprocesses for fsmonitor sentinels (including a positive
unsafe control proving each hook works), init/shadowing rejection, modified code,
index, ignored dependencies, HEAD and attributes. Unsupported/malformed/failing
Git probes refuse before repository commands. LF and CRLF positive imports run
with fsmonitor configured and must succeed without executing the sentinel;
manifests, hashes and lineage are read back. Legacy direct `python -m` remains
UNSAFE/UNSUPPORTED and its positive sentinel control remains explicit.

No production/LIVE/publication/bridge, Pocket, methodology, MARSConfig, productive
YAML, scheduler, state/pointer or FROZEN change. The same four production blockers
remain. Trusted interpreter/stdlib/third-party packages/Git executable and OS
are prerequisites. Hostile administrators and post-check hostile TOCTOU are
excluded; this is not a general sandbox. Technical implementation is not closure
of C2C-R1-01: one independent concluding review is required.

Validation and immutability readback are recorded after execution below.

## Linux verification — final implementation

Python 3.12, Git 2.52.0, repository requirements installed in a dedicated venv:
- Historical-import suite: 89 passed / 1 Windows-only junction skip, exit 0.
- Related regression suite: 115 passed, exit 0.
- Full suite: 1881 passed / 5 skips / 6 existing plotting
  deprecation warnings, exit 0, 62.11 seconds.
- Git 2.36.0 built from upstream v2.36.0: the full historical-import suite
  also passed 89 / 1 platform skip, exit 0, including real fsmonitor controls,
  supported LF/CRLF subprocesses and negative probes. The source archive SHA256
  is `59dbc158dce293798570166fe7acfe225514f2868bc2d6e25c1a5a00c4ac0888`.
  Build flags: NO_GETTEXT, NO_TCLTK, NO_CURL, NO_EXPAT. Only temporary tools were
  built; no global Git installation or project configuration was changed.
  The first minimum-version run had 4 fixture setup failures because an
  uninstalled Git build lacked templates/.git/info; rerunning with the existing
  system templates resolved this without changing code or excluding tests.

Final Linux logs are retained in the Windows evidence directory alongside
Windows results, immutable artifact readback and file hashes.

## Candidate CI

Implementation commit `2043d0fce542162e28158a23ee65009fe8c9d683`:
[Tests #167](https://github.com/indren9/ptcgp_ranking/actions/runs/42401181785)
SUCCESS; Ubuntu/Python 3.14, Git 2.56.0: 1882 passed, 4 skipped,
6 plotting deprecation warnings, pytest exit 0 (62.25 seconds).
CI checks exactly this candidate; neither CI nor this implementation closes the
independent gate or authorizes production. Final documentation-only commit and
its CI identity are reported in the concluding handoff.

## Windows verification — final implementation

Native Windows, existing Python 3.12. Both jobs exit 0:
- Git 2.36.0.windows.1 portable: historical-import suite plus latest-completed
  job, B3, C2A and historical-rebuild regressions: **204 passed, 1 skipped**,
  530.76 seconds. Skip: source-symlink privilege unavailable; junction test ran.
- Installed Git 2.56.0.windows.4: final startup adversarial/positive subset:
  **14 passed, 76 deselected**, 214.00 seconds. This includes real fsmonitor
  controls, LF/CRLF imports, index/ignored/init/shadow/attributes/HEAD refusals,
  invalid version probes, environment redirection and checkout Git shadowing.
- Earlier pre-lookup-hardening cycle on installed Git: 88 passed, 1 symlink
  privilege skip, exit 0. The final results above supersede it for acceptance.

No fsmonitor sentinel executed on the supported path. Positive unsafe controls
proved the hook executable on each OS/version; direct legacy `python -m` still
executes its intentional package-init sentinel and remains unsupported. Synthetic
LF/CRLF imports succeeded with manifest/QA and raw executed-byte hashes read back.

Minimum Windows binary came from the official portable release, extracted only
under the evidence directory, without changing installed Git or global PATH:
https://github.com/git-for-windows/git/releases/tag/v2.36.0.windows.1
`MinGit-2.36.0-64-bit.zip` SHA256 matched the official release body:
`0c6611fc04b9b111777a4f219e198bded07fea80fb14dedb01ebbcc231480986`.
Thus the 2.36.0 support floor was exercised with real binaries on both OSes,
in addition to source inspection and synthetic refusal tests below the floor.

## Immutability and committed runtime readback

Read-only pre/post SHA256 verification, exit 0:
- 481 CRI + 578 PBL = 1,059 original source files, no mismatches.
- 6 CRI + 6 PBL = 12 existing candidate CSVs, no mismatches.
- 82 protected tracked files, no mismatches.
- CRI import manifest: `8f16209b69a9a6c2f8a22e6177a9ba8b10976ec890d9c1af366eadeb2c28142c`.
- CRI run manifest: `a4dde4bfcd403b9b7a229fa1fef714a1ffae759a4b9cb55c79e4298f2b6ea3b1`.
- PBL import manifest: `70707acb896403cf32f7ae3bcec305a4b7ae24bc51ae7bcbeafdc7010f78229b`.
- PBL run manifest: `2c6570a53b8ee541dac3285f819457fa767930673449f149c227e4aa23be59b7`.

The existing inventories were reused; no duplicate global register or real-source
import/rebuild was created. Original manifests, candidates and state were only
read. Both manifests per window remain identical to the previous verified run.
The committed candidate's 99 runtime files passed the raw Git/tree/index pin.

Changed code/test SHA256 (raw LF checkout bytes, also verified on Windows):
- `tcg_live_historical_import_safe.py`:
  `a98b57ba925b720ae7c3cd1118527cb18fc81ad8ea04caf353d63d8f47af3932`.
- `scripts/tcg_live_historical_import.py`:
  `199eb61b62a6c0ccc9d67ed51d280312e7fc9e63f23bece70fdbce43a73f897e`.
- `tests/test_tcg_live_historical_import.py`:
  `53ec83f1efec87f45dcb5f57acd7f6d0dba6875ccd00bb9360c701df3b553ac4`.

Changes are confined to those three files and these three documents:
`docs/tcg-live-issue36-c2c-b.md`, `docs/tcg-live-issue36-c2c-r3.md`,
`docs/tcg-live-issue36-c2c.md`. The last two only label the superseded startup;
the previous evidence/history is retained.

Evidence on Andrea's PC: `C:\dev\Pokemon\c2c-b-qa-20261107`.
This contains Linux/current-Git/minimum-Git logs, Windows logs, before/after
immutability JSON and committed readback with all six file hashes. Original
historical inventories are referenced by the existing verification harness.

## Handoff / gate

RESULT: bounded architecture B implementation. EVIDENCE: tests, real subprocess
sentinels, CI, raw runtime pin and unchanged historical hashes as recorded above.
CHANGES: one shared Git policy before any repository command, neutralized local
callbacks, explicit trusted executable lookup, existing runtime and sealing
checks reused, LF/CRLF preserved; no manifest schema migration.
BLOCKERS: C2C-R1-01 remains OPEN pending the one concluding independent review.
The LOW finding remains closed. Production blockers remain original URLs,
four PBL invalid_record, uncertified freshness/completeness and missing bridge.
NEXT: Chat Madre 05 dispatches exactly one independent concluding review of
baseline-to-final diff and the frozen acceptance contract. No automatic further
remediation, merge, LIVE, publication or production activation is authorized.

SESSION CLOSE after the concluding handoff. Notion was not modified.
