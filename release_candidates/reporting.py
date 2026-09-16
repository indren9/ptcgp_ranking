"""Deterministic non-authoritative review reports, with no publication capability."""
from __future__ import annotations

from pathlib import Path

from .model import digest, stable_json


def report_json(results: list[dict]) -> str:
    return stable_json({"schema_version": 1, "status": "NON_AUTHORITATIVE_CANDIDATE_REVIEW",
                        "candidates": results})


def report_markdown(results: list[dict]) -> str:
    lines = ["# NON-AUTHORITATIVE release candidate review", "",
             "Candidate != canonical. Approval != activation. Promotable is advisory only.", ""]
    for result in results:
        lines += [f"## {result['semantic_event_id']}", ""]
        for key in ("candidate_state", "game", "platform", "format", "release_code", "release_name",
                    "candidate_revision", "event_kind", "boundary_reasons", "effective_utc_datetime",
                    "source_tier", "source_url", "missing_evidence", "conflict_status", "conflicts",
                    "parent_canonical_version", "parent_canonical_digest", "stale_parent",
                    "structurally_promotable", "validation_errors"):
            # JSON escaping prevents embedded newlines/control text from changing report structure.
            value = stable_json(result[key]).strip().replace("\n", " ")
            value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "&#96;")
            lines.append(f"- {key}: `{value}`")
        lines.append("")
    return "\n".join(lines)


def write_review(results: list[dict]) -> tuple[Path, Path]:
    """Only create immutable content-addressed reports under the fixed candidate area.

    No destination argument, canonical path, promotion, state or publisher hook.
    Refuse symlinks/junction redirection and existing files with different bytes.
    """
    root = Path(__file__).resolve().parents[1] / "data" / "candidates" / "releases" / "reviews"
    if root.resolve() != root:
        raise ValueError("candidate storage cannot redirect through symlinks/junctions")
    raw = report_json(results)
    name = digest(raw.encode("utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    paths = (root / f"{name}.candidate.json", root / f"{name}.candidate.md")
    for path, text in zip(paths, (raw, report_markdown(results))):
        if path.resolve() != path:
            raise ValueError("candidate report cannot redirect through symlinks/junctions")
        encoded = text.encode("utf-8")
        try:
            with path.open("xb") as handle:
                handle.write(encoded)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError("immutable candidate report already exists with different content")
    return paths
