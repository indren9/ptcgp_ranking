"""Apply P2 R3 candidate private-publisher integration to an ISOLATED checkout.

Explicitly requires the verified main-branch Git blob SHA. Runs no tests,
no pipeline, no scheduler, and makes no remote Git changes.

Usage (ONLY after isolated checkout and authorization):
    python patches/apply_private_job_patch.py --repo-root PATH --apply
Omit --apply for a preview of the matching anchors (READ-ONLY).
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

BASE_BLOB = "4d786774ddf3616e6dbe2385bdea16a465e60c0a"
PATH = Path("scripts/tcg_live_latest_completed_job.py")


def replacement_pair(old: str, new: str) -> tuple[str, str]:
    return old, new


PAIRS = [
    replacement_pair(
        "from scripts.tcg_live_latest_completed import (\n    build_plan,\n    read_json,\n)\n",
        "from scripts.tcg_live_latest_completed import (\n    build_plan,\n    read_json,\n)\n"
        "from scripts.tcg_live_private_plan import private_plan\n"
        "from storage.tcg_private_guarded import publish_private_bundle_guarded\n",
    ),
    replacement_pair(
        "    publish: bool = False,\n    public_target: Path | None = None,\n) -> dict[str, Any]:\n",
        "    publish: bool = False,\n    public_target: Path | None = None,\n"
        "    publish_private: bool = False,\n) -> dict[str, Any]:\n",
    ),
    replacement_pair(
        "    repo_root = repo_root.resolve()\n    work_root = work_root.resolve()\n",
        "    if publish_private and publish:\n"
        "        raise ValueError(\"public and private-only modes are mutually exclusive\")\n"
        "    repo_root = repo_root.resolve()\n    work_root = work_root.resolve()\n",
    ),
    replacement_pair(
        "    plan = build_plan(\n        catalog_path=catalog_path,\n        state=state,\n        acquisition_started_at=started,\n    )\n",
        "    plan = (\n"
        "        private_plan(catalog_path=catalog_path, acquisition_started_at=started)\n"
        "        if publish_private else\n"
        "        build_plan(catalog_path=catalog_path, state=state, acquisition_started_at=started)\n"
        "    )\n",
    ),
    replacement_pair(
        "    if publish:\n        bundle_dir = work_root / \"bundle\"\n",
        "    if publish or publish_private:\n        bundle_dir = work_root / \"bundle\"\n",
    ),
    replacement_pair(
        "        if onedrive_publication_enabled(cfg):\n            try:\n"
        "                onedrive_publication_result = publish_tcg_bundle(\n",
        "        if publish_private:\n"
        "            if not onedrive_publication_enabled(cfg):\n"
        "                raise RuntimeError(\"private publication is disabled by profile\")\n"
        "            onedrive_publication_result = publish_private_bundle_guarded(\n"
        "                bundle_dir=bundle_dir, plan=plan\n"
        "            ).to_dict()\n"
        "        elif onedrive_publication_enabled(cfg):\n            try:\n"
        "                onedrive_publication_result = publish_tcg_bundle(\n",
    ),
    replacement_pair(
        "        target = (\n            public_target\n            or repo_root\n            / \"public\"\n            / \"tcg-live\"\n            / \"latest-meta\"\n        )\n\n        publication_result = publish_tcg_live_bundle(\n            bundle_dir=bundle_dir,\n            plan=plan,\n            state_path=state_path,\n            target_dir=target,\n            dry_run=False,\n        )\n",
        "        if publish:\n"
        "            target = (\n                public_target\n                or repo_root\n"
        "                / \"public\"\n                / \"tcg-live\"\n"
        "                / \"latest-meta\"\n            )\n\n"
        "            publication_result = publish_tcg_live_bundle(\n"
        "                bundle_dir=bundle_dir,\n                plan=plan,\n"
        "                state_path=state_path,\n                target_dir=target,\n"
        "                dry_run=False,\n            )\n",
    ),
    replacement_pair(
        "        \"action\": (\n            \"published\"\n            if publish\n            else \"candidate_ready\"\n        ),\n",
        "        \"action\": (\n            \"published\" if publish else\n"
        "            \"published_private\" if publish_private else \"candidate_ready\"\n        ),\n",
    ),
    replacement_pair(
        "        \"published\": bool(publish),\n",
        "        \"published\": bool(publish or publish_private),\n"
        "        \"published_public\": bool(publish),\n"
        "        \"published_private\": bool(publish_private),\n",
    ),
    replacement_pair(
        "    parser.add_argument(\n        \"--allow-public-write\",\n",
        "    parser.add_argument(\n        \"--publish-private\",\n"
        "        action=\"store_true\",\n"
        "        help=\"Publish only the validated private OneDrive bundle (no GitHub writes).\",\n"
        "    )\n\n"
        "    parser.add_argument(\n        \"--allow-public-write\",\n",
    ),
    replacement_pair(
        "    if args.publish and not args.allow_public_write:\n",
        "    if args.publish_private and (args.publish or args.allow_public_write):\n"
        "        raise SystemExit(\"--publish-private excludes all public-write flags\")\n"
        "    if args.publish and not args.allow_public_write:\n",
    ),
    replacement_pair(
        "        publish=args.publish,\n    )\n",
        "        publish=args.publish,\n        publish_private=args.publish_private,\n    )\n",
    ),
]


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="requires isolated, authorized checkout")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    path = root / PATH
    content = path.read_bytes()
    has_crlf = b"\r\n" in content
    if b"\r" in content.replace(b"\r\n", b""):
        raise SystemExit("STOP: inconsistent Windows source newlines")
    normalized = content.replace(b"\r\n", b"\n")
    found = git_blob_sha(normalized)
    if found != BASE_BLOB:
        raise SystemExit(f"STOP: normalized baseline blob {found} differs from GitHub verified {BASE_BLOB}")
    text = normalized.decode("utf-8")
    for i, (old, new) in enumerate(PAIRS, 1):
        if text.count(old) != 1:
            raise SystemExit(f"STOP: patch anchor {i} missing or ambiguous ({text.count(old)})")
        text = text.replace(old, new, 1)
    print(f"Anchors: {len(PAIRS)} matched; target: {path}")
    if args.apply:
        output = text.replace("\n", "\r\n") if has_crlf else text
        path.write_bytes(output.encode("utf-8"))
        print("Written isolated source, preserving original newline convention. No tests run.")
    else:
        print("READ-ONLY PREVIEW: no changes written (use --apply after GO).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
