from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("ptcgp")


def read_config(base_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    """
    Load config/config.yaml and locate the optional alias_map.json file.

    Returns (cfg, yaml_file, alias_json). It does not create files.
    """
    cfg_dir = Path(base_dir) / "config"
    yaml_file = cfg_dir / "config.yaml"
    alias_json = cfg_dir / "alias_map.json"

    if not yaml_file.exists():
        raise FileNotFoundError(
            f"[init] Missing config.yaml: {yaml_file}\n"
            "Create 'config/config.yaml' (see README) with the run configuration section."
        )

    with open(yaml_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    log.info("Config loaded from %s", yaml_file)

    if not alias_json.exists():
        log.warning("[init] alias_map.json not found: %s - continuing without aliases.", alias_json)

    return cfg, yaml_file, alias_json


def get_set_params(cfg: dict) -> tuple[str, str | None]:
    scraping = cfg.get("scraping", {}) or {}
    setcfg = scraping.get("set", {}) or {}
    mode = (setcfg.get("mode") or "auto").strip().lower()
    code = setcfg.get("code")
    return mode, code


__all__ = ["read_config", "get_set_params"]
