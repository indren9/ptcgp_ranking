from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass
class ProjectPaths:
    base: Path
    output_root: Path
    outputs: Path
    cache: Path
    logs: Path


Paths = ProjectPaths


def _sanitize_scope_node(value: Any) -> str | None:
    if value is None:
        return None
    node = str(value).strip()
    if not node:
        return None
    node = re.sub(r"\s+", "_", node)
    node = re.sub(r"[^A-Za-z0-9_.-]+", "", node)
    return node or None


def _query_value(url: str | None, key: str) -> str | None:
    if not url:
        return None
    try:
        return parse_qs(urlparse(str(url)).query).get(key, [None])[0]
    except Exception:
        return None


def _format_code_from_config(source: dict[str, Any]) -> str | None:
    fmt_cfg = source.get("format", {}) if isinstance(source, dict) else {}
    if isinstance(fmt_cfg, str):
        return _sanitize_scope_node(fmt_cfg)
    if not isinstance(fmt_cfg, dict):
        return None
    mode = str(fmt_cfg.get("mode") or "auto").strip().lower()
    if mode != "code":
        return None
    return _sanitize_scope_node(fmt_cfg.get("code"))


def source_scope_from_config(cfg: dict | None, *, source_url: str | None = None) -> tuple[str, ...]:
    """
    Return optional output scope nodes, currently game and Limitless format.

    The resulting output layout is:
    outputs/<GAME>/<FORMAT>/<CODE>__<NAME>/...

    In auto mode, the format is derived from the resolved source URL. In code
    mode, source.format.code is used as a manual override. Missing nodes are
    skipped so legacy callers without config keep the old
    outputs/<CODE>__<NAME>/... layout.
    """
    cfg = cfg or {}
    source = (cfg.get("source", {}) or {})
    scraping = (cfg.get("scraping", {}) or {})
    url = source_url or scraping.get("decks_url")

    game = _sanitize_scope_node(_query_value(url, "game") or source.get("game"))
    fmt = _sanitize_scope_node(_format_code_from_config(source) or _query_value(url, "format"))

    nodes: list[str] = []
    if game:
        nodes.append(game.upper())
    if fmt:
        nodes.append(fmt.lower())
    return tuple(nodes)


def output_scope_from_config(cfg: dict | None, *, source_url: str | None = None) -> tuple[str, ...]:
    """Backward-compatible alias for source_scope_from_config."""
    return source_scope_from_config(cfg, source_url=source_url)


def output_root_from_config(base_dir: Path | str, cfg: dict | None) -> Path:
    """
    Resolve the configured output root.

    Relative paths are anchored to the project base directory. Absolute paths are
    used as-is, which allows storing heavy outputs outside the repository.
    """
    base = Path(base_dir)
    paths_cfg = ((cfg or {}).get("paths", {}) or {})
    raw = paths_cfg.get("output_dir", "outputs")
    output_dir = Path(str(raw or "outputs")).expanduser()
    if output_dir.is_absolute():
        return output_dir.resolve()
    return (base / output_dir).resolve()


def init_paths(base_dir: Path | str, cfg: dict | None = None, *, source_url: str | None = None) -> ProjectPaths:
    """
    Create only the project roots. Deeper output folders are created by routing
    and writer helpers when they are actually needed.
    """
    base = Path(base_dir)
    output_root = output_root_from_config(base, cfg)
    out = output_root
    for node in source_scope_from_config(cfg, source_url=source_url):
        out = out / node
    cache = base / "cache" / "requests"
    logs = base / "logs"

    output_root.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    return ProjectPaths(base=base, output_root=output_root, outputs=out, cache=cache, logs=logs)
