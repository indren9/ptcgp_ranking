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
- Full suite: 1881 passed / 5 platform-specific skips / 6 existing plotting
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
