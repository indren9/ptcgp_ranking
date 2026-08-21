from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from scripts.export_expansions_catalog import expansion_code_sort_key


SCHEMA_VERSION = 1
README_START = "<!-- latest-completed-meta:start -->"
README_END = "<!-- latest-completed-meta:end -->"
REQUIRED_BUNDLE_FILES = ("fragment.md", "heatmap.png", "ranking.csv", "manifest.json")
PUBLIC_BUNDLE_FILES = ("heatmap.png", "ranking.csv", "manifest.json")
PUBLIC_RANKING_COLUMNS = (
    "Rank",
    "Deck",
    "Score_%",
    "MAS_%",
    "LB_%",
    "BT_%",
    "SE_%",
    "N_eff",
    "Opp_used",
    "Opp_total",
    "Coverage_%",
)


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    name: str


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def read_catalog(path: Path) -> list[CatalogEntry]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["code", "name"]:
            raise ValueError(f"Catalog must have exactly the columns code,name: {path}")
        entries = [
            CatalogEntry(code=str(row["code"] or "").strip(), name=str(row["name"] or "").strip())
            for row in reader
        ]

    if not entries:
        raise ValueError(f"Catalog is empty: {path}")
    if any(not entry.code or not entry.name for entry in entries):
        raise ValueError(f"Catalog entries must have both code and name: {path}")

    folded = [entry.code.casefold() for entry in entries]
    if len(folded) != len(set(folded)):
        raise ValueError(f"Catalog contains duplicate expansion codes: {path}")

    return sorted(entries, key=lambda entry: expansion_code_sort_key(entry.code))


def read_json(path: Path, *, missing: Any = None) -> Any:
    if not path.exists():
        return missing
    return json.loads(path.read_text(encoding="utf-8"))


def _entry_dict(entry: CatalogEntry | None) -> dict[str, str] | None:
    return asdict(entry) if entry is not None else None


def _find_entry(entries: Sequence[CatalogEntry], code: str) -> CatalogEntry:
    wanted = code.strip().casefold()
    for entry in entries:
        if entry.code.casefold() == wanted:
            return entry
    raise ValueError(f"Requested completed set is not present in the catalog: {code}")


def build_publication_plan(
    entries: Sequence[CatalogEntry],
    state: Mapping[str, Any] | None = None,
    *,
    force_completed_set: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not entries:
        raise ValueError("At least one catalog entry is required")

    ordered = sorted(entries, key=lambda entry: expansion_code_sort_key(entry.code))
    current = ordered[-1]
    completed = _find_entry(ordered, force_completed_set) if force_completed_set else (
        ordered[-2] if len(ordered) >= 2 else None
    )
    if completed is not None and completed.code.casefold() == current.code.casefold():
        raise ValueError("The current set cannot be published as the completed meta")

    previous_published = ((state or {}).get("published_completed_set") or {}).get("code")
    should_publish = completed is not None and (
        force_completed_set is not None
        or not previous_published
        or str(previous_published).casefold() != completed.code.casefold()
    )

    action = "publish" if should_publish else "noop"
    if completed is None:
        reason = "catalog_has_no_previous_set"
    elif force_completed_set is not None:
        reason = "manual_override"
    elif not previous_published:
        reason = "initial_previous_set"
    elif should_publish:
        reason = "new_current_set_detected"
    else:
        reason = "completed_set_already_published"

    timestamp = generated_at or utc_now_iso()
    next_state = {
        "schema_version": SCHEMA_VERSION,
        "game": "POCKET",
        "format": "standard",
        "observed_current_set": _entry_dict(current),
        "published_completed_set": _entry_dict(completed) if should_publish else (
            (state or {}).get("published_completed_set")
        ),
        "updated_at": timestamp,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action,
        "reason": reason,
        "current_set": _entry_dict(current),
        "completed_set": _entry_dict(completed),
        "state_after_publish": next_state,
        "generated_at": timestamp,
    }


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def replace_readme_block(readme: str, fragment: str) -> str:
    if readme.count(README_START) != 1 or readme.count(README_END) != 1:
        raise ValueError("README must contain exactly one latest-completed-meta marker pair")
    start = readme.index(README_START)
    end = readme.index(README_END, start)
    if end < start:
        raise ValueError("README latest-completed-meta markers are out of order")
    clean_fragment = fragment.strip()
    if README_START in clean_fragment or README_END in clean_fragment:
        raise ValueError("Published fragment cannot contain README boundary markers")
    return (
        readme[: start + len(README_START)]
        + "\n\n"
        + clean_fragment
        + "\n\n"
        + readme[end:]
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bundle(bundle_dir: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    missing = [name for name in REQUIRED_BUNDLE_FILES if not (bundle_dir / name).is_file()]
    if missing:
        raise ValueError(f"Latest-meta bundle is missing required files: {', '.join(missing)}")

    manifest = read_json(bundle_dir / "manifest.json")
    manifest_code = ((manifest or {}).get("set") or {}).get("code")
    planned_code = ((plan.get("completed_set") or {}).get("code"))
    if not manifest_code or str(manifest_code).casefold() != str(planned_code or "").casefold():
        raise ValueError(
            f"Bundle manifest set {manifest_code!r} does not match planned completed set {planned_code!r}"
        )
    if plan.get("action") != "publish":
        raise ValueError(f"Publication plan action must be 'publish', got {plan.get('action')!r}")
    if (bundle_dir / "heatmap.png").stat().st_size == 0:
        raise ValueError("Bundle heatmap.png is empty")
    if (bundle_dir / "ranking.csv").stat().st_size == 0:
        raise ValueError("Bundle ranking.csv is empty")
    if (bundle_dir / "heatmap.png").read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Bundle heatmap.png is not a valid PNG file")

    with (bundle_dir / "ranking.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != PUBLIC_RANKING_COLUMNS:
            raise ValueError("Bundle ranking.csv does not match the public ranking column contract")
        ranking_rows = list(reader)
    if not ranking_rows:
        raise ValueError("Bundle ranking.csv has no data rows")
    if [row.get("Rank") for row in ranking_rows] != [str(rank) for rank in range(1, len(ranking_rows) + 1)]:
        raise ValueError("Bundle ranking.csv Rank must be contiguous and start at 1")
    if any(not str(row.get("Deck") or "").strip() for row in ranking_rows):
        raise ValueError("Bundle ranking.csv contains a blank deck name")

    source = (manifest or {}).get("source") or {}
    if source.get("contains_personal_data") is not False:
        raise ValueError("Bundle manifest must explicitly state contains_personal_data=false")
    outputs = (manifest or {}).get("outputs") or {}
    for key, filename in (("ranking", "ranking.csv"), ("heatmap", "heatmap.png")):
        expected_hash = ((outputs.get(key) or {}).get("sha256"))
        if not expected_hash or str(expected_hash).lower() != _sha256(bundle_dir / filename):
            raise ValueError(f"Bundle {filename} hash does not match manifest")

    public_text = "\n".join(
        [
            (bundle_dir / "fragment.md").read_text(encoding="utf-8"),
            (bundle_dir / "ranking.csv").read_text(encoding="utf-8"),
            (bundle_dir / "manifest.json").read_text(encoding="utf-8"),
        ]
    ).casefold()
    forbidden = ("c:\\users\\", "onedrive", "cookie", "authorization:", "x-access-key")
    found = [token for token in forbidden if token in public_text]
    if found:
        raise ValueError(f"Bundle contains forbidden local/sensitive text: {', '.join(found)}")
    return manifest


def publish_bundle(
    *,
    bundle_dir: Path,
    plan_path: Path,
    readme_path: Path,
    state_path: Path,
    target_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = read_json(plan_path)
    validate_bundle(bundle_dir, plan)
    updated_readme = replace_readme_block(
        readme_path.read_text(encoding="utf-8"),
        (bundle_dir / "fragment.md").read_text(encoding="utf-8"),
    )
    next_state = plan.get("state_after_publish")
    if not isinstance(next_state, dict):
        raise ValueError("Publication plan is missing state_after_publish")

    result = {
        "published_set": plan["completed_set"],
        "target_dir": str(target_dir),
        "dry_run": bool(dry_run),
    }
    if dry_run:
        return result

    for name in PUBLIC_BUNDLE_FILES:
        _atomic_write_bytes(target_dir / name, (bundle_dir / name).read_bytes())
    _atomic_write_text(readme_path, updated_readme)
    _atomic_write_text(state_path, json.dumps(next_state, indent=2, ensure_ascii=False) + "\n")
    return result


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _append_github_output(path: Path, plan: Mapping[str, Any]) -> None:
    values = {
        "action": plan.get("action") or "",
        "reason": plan.get("reason") or "",
        "current_set_code": ((plan.get("current_set") or {}).get("code") or ""),
        "completed_set_code": ((plan.get("completed_set") or {}).get("code") or ""),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and publish the single latest completed meta snapshot.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Select current and completed sets from the expansion catalog.")
    plan.add_argument("--catalog", type=Path, default=Path("public/expansions_pocket_standard.csv"))
    plan.add_argument("--state", type=Path, default=Path(".github/latest-completed-meta-state.json"))
    plan.add_argument("--output", type=Path, default=Path("latest-meta-plan.json"))
    plan.add_argument("--force-completed-set", default=None)
    plan.add_argument("--github-output", type=Path, default=None)

    publish = subparsers.add_parser("publish", help="Validate and replace the one public latest-meta bundle.")
    publish.add_argument("--bundle", type=Path, required=True)
    publish.add_argument("--plan", type=Path, default=Path("latest-meta-plan.json"))
    publish.add_argument("--readme", type=Path, default=Path("README.md"))
    publish.add_argument("--state", type=Path, default=Path(".github/latest-completed-meta-state.json"))
    publish.add_argument("--target", type=Path, default=Path("public/latest-meta"))
    publish.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        plan = build_publication_plan(
            read_catalog(args.catalog),
            read_json(args.state, missing=None),
            force_completed_set=args.force_completed_set,
        )
        _write_json(args.output, plan)
        if args.github_output is not None:
            _append_github_output(args.github_output, plan)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    result = publish_bundle(
        bundle_dir=args.bundle,
        plan_path=args.plan,
        readme_path=args.readme,
        state_path=args.state,
        target_dir=args.target,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
