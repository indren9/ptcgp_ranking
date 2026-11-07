"""Verified historical_import launcher; only enter through the documented -I -c bootstrap.

This file is verified against the requested Git revision *before* the bootstrap
executes it. Neither this module nor the importer is a production/daily entrypoint.
"""
from __future__ import annotations

import hashlib
import json
from importlib.machinery import PathFinder
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

# Exact repo-local dependency boundary for the historical importer. Include the
# entire scripts package: a previously absent __init__.py is executable code.
RUNTIME_PATHS = (
    "historical_rebuild", "acquisition", "core", "mars", "pipelines",
    "sources", "storage", "config", "data/reference", "scripts",
    "domain", "reporting", "utils",
    "tcg_live_historical_import_safe.py", ".gitattributes",
)
TEXT_SUFFIXES = {".py", ".pyi", ".json", ".yaml", ".yml", ".txt", ".md",
                 ".cfg", ".ini", ".toml", ".csv", ".rst", ".xml"}
IMPORTER = "scripts/tcg_live_historical_import.py"


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError("historical_import startup: " + message)


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=stdin,
                          capture_output=True)
    _require(proc.returncode == 0, "Git " + (args[0] if args else "command") + " failed")
    return proc.stdout


def _records(data: bytes):
    return (record for record in data.split(b"\0") if record)


def _blob_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _safe_file(repo: Path, relative: str) -> Path:
    path = repo
    for part in relative.split("/"):
        _require(part not in ("", ".", ".."), "invalid Git pathname")
        path = path / part
        _require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(),
                 "symlink/junction runtime path: " + relative)
    _require(path.is_file(), "missing runtime file: " + relative)
    return path


def verify_runtime(repo: Path, revision: str) -> dict[str, str]:
    """Raw Git blob pin, with only literal CRLF->LF compatibility.

    Git attributes and configurable clean/smudge filters never participate in
    the content comparison. All tracked runtime files are checked, not merely
    Git's filtered diff; all non-cache untracked runtime paths are rejected.
    """
    repo = Path(repo).absolute()
    _require(repo.is_dir() and not repo.is_symlink()
             and not getattr(repo, "is_junction", lambda: False)(), "unsafe repository root")
    actual_root = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    _require(actual_root == repo.resolve(), "Git top-level differs from selected repository")
    _require(_git(repo, "rev-parse", "HEAD").decode().strip() == revision,
             "requested revision is not HEAD")
    _require(_git(repo, "rev-parse", "--show-object-format").strip() == b"sha1",
             "unsupported Git object format")
    tree = {}
    modes = {}
    for record in _records(_git(repo, "ls-tree", "-r", "-z", revision, "--", *RUNTIME_PATHS)):
        meta, name = record.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split(" ")
        relative = name.decode("utf-8")
        _require(kind == "blob" and mode in ("100644", "100755"),
                 "non-regular Git runtime object: " + relative)
        tree[relative] = oid
        modes[relative] = mode

    _require(IMPORTER in tree and "tcg_live_historical_import_safe.py" in tree,
             "importer/launcher absent from pinned revision")
    index = {}
    for record in _records(_git(repo, "ls-files", "--stage", "-z", "--", *RUNTIME_PATHS)):
        meta, name = record.split(b"\t", 1)
        mode, oid, stage = meta.decode("ascii").split(" ")
        _require(stage == "0", "unmerged runtime path")
        index[name.decode("utf-8")] = (mode, oid)
    _require(set(index) == set(tree)
             and all(index[name] == (modes[name], oid) for name, oid in tree.items()),
             "Git index differs from pinned runtime tree")

    others = [item.decode("utf-8") for item in _records(
        _git(repo, "ls-files", "--others", "-z", "--", *RUNTIME_PATHS))]
    for name in others:
        parts = name.split("/")
        # Cache files already present in a developer checkout cannot be loaded:
        # the launcher selects a fresh, outside-repo pycache prefix before import.
        if "__pycache__" in parts and name.endswith(".pyc"):
            continue
        raise ValueError("historical_import startup: untracked runtime dependency: " + name)

    attributes = _git(repo, "check-attr", "-z", "--stdin", "filter",
                      "working-tree-encoding",
                      stdin=b"\0".join(name.encode("utf-8") for name in tree) + b"\0")
    fields = list(_records(attributes))
    _require(len(fields) == len(tree) * 6, "unexpected Git attribute output")
    for i in range(0, len(fields), 3):
        attr = fields[i + 1].decode("ascii")
        val = fields[i + 2].decode("utf-8")
        _require(attr in ("filter", "working-tree-encoding")
                 and val in ("unspecified", "unset"),
                 "untrusted Git attribute " + attr + "=" + val)

    hashes = {}
    for name, oid in tree.items():
        raw = _safe_file(repo, name).read_bytes()
        clean = raw
        if _blob_oid(clean) != oid and Path(name).suffix.lower() in TEXT_SUFFIXES:
            clean = raw.replace(b"\r\n", b"\n")
        _require(_blob_oid(clean) == oid, "runtime bytes differ from pinned Git blob: " + name)
        if name in (IMPORTER, "tcg_live_historical_import_safe.py"):
            hashes[name] = hashlib.sha256(raw).hexdigest()
    return {"importer_sha256": hashes[IMPORTER],
            "launcher_sha256": hashes["tcg_live_historical_import_safe.py"],
            "verified_paths": frozenset(tree)}


class _PinnedLocalFinder:
    """Stop any later repo-local import that wasn't checked before import."""

    def __init__(self, repo: Path, verified_paths):
        self.repo = repo
        self.verified_paths = verified_paths

    def find_spec(self, fullname, path=None, target=None):
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.origin not in (None, "built-in", "frozen"):
            origin = Path(spec.origin).absolute()
            if origin.is_relative_to(self.repo):
                relative = origin.relative_to(self.repo).as_posix()
                _require(relative in self.verified_paths,
                         "unverified local import: " + relative)
                return spec
        return None


def main() -> int:
    # Deliberately refuse direct script execution. The documented -I -c entrypoint
    # binds this file to Git before *any* of its repo-local code is executed.
    _require(sys.flags.isolated == 1 and
             getattr(sys, "_mars_historical_import_bootstrap_verified", False) is True,
             "use documented isolated, pinned -I -c bootstrap; direct python/-m is unsafe")
    _require(len(sys.argv) == 3 and sys.argv[1] == "--request", "expected --request JSON")
    request_path = Path(sys.argv[2]).absolute()
    _require(request_path.is_file() and not request_path.is_symlink(), "unsafe request path")
    request = json.loads(request_path.read_bytes())
    _require(isinstance(request.get("software_revision"), str), "missing requested revision")
    repo = Path(__file__).absolute().parent
    pinned = verify_runtime(repo, request["software_revision"])

    # Redirect bytecode reads to a fresh location outside the checkout; the
    # untracked __pycache__ entries in existing checkouts are not executable.
    with tempfile.TemporaryDirectory(prefix="mars-historical-import-pycache-") as cache:
        sys.pycache_prefix = cache
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(repo))
        sys.meta_path.insert(0, _PinnedLocalFinder(repo, pinned["verified_paths"]))
        sys._mars_historical_import_startup_verified = True
        sys.argv = [str(repo / IMPORTER), "--request", str(request_path)]
        runpy.run_module("scripts.tcg_live_historical_import",
                         run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
