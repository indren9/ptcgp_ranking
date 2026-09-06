from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from scripts.latest_completed_meta import validate_bundle
from scripts.latest_completed_meta_producer import build_bundle


PUBLIC_FILES = (
    "ranking.csv",
    "heatmap.png",
    "manifest.json",
)


def _validation_plan(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    window = plan.get("latest_completed_window") or {}

    code = str(window.get("code") or "").strip()
    name = str(window.get("name") or "").strip()

    if not code or not name:
        raise ValueError(
            "TCG Live plan is missing latest completed window"
        )

    return {
        "action": "publish",
        "completed_set": {
            "code": code,
            "name": name,
        },
    }


def _acquired_on(
    acquisition_manifest: Path,
) -> str:
    payload = json.loads(
        acquisition_manifest.read_text(
            encoding="utf-8"
        )
    )

    raw = str(
        payload.get("acquisition_started_at") or ""
    ).strip()

    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            "acquisition manifest has invalid "
            "acquisition_started_at"
        ) from exc

    return value.date().isoformat()


def build_tcg_live_bundle(
    *,
    source_run: Path,
    config_path: Path,
    acquisition_manifest: Path,
    bundle_dir: Path,
    plan: Mapping[str, Any],
    source_revision: str | None = None,
) -> dict[str, Any]:
    window = plan["latest_completed_window"]

    manifest = build_bundle(
        source_run=source_run,
        config_path=config_path,
        bundle_dir=bundle_dir,
        set_code=str(window["code"]),
        set_name=str(window["name"]),
        acquired_on=_acquired_on(
            acquisition_manifest
        ),
        acquisition_manifest=acquisition_manifest,
        source_revision=source_revision,
        game_code="PTCG",
        game_name="Pokémon TCG Live",
        meta_label="TCG Live",
        public_prefix="public/tcg-live/latest-meta",
    )

    validate_bundle(
        bundle_dir,
        _validation_plan(plan),
    )

    if manifest.get("game") != "PTCG":
        raise ValueError(
            "TCG Live public manifest game mismatch"
        )

    snapshot_game = (
        (manifest.get("snapshot") or {})
        .get("game")
        or {}
    )

    if snapshot_game.get("name") != "Pokémon TCG Live":
        raise ValueError(
            "TCG Live public manifest label mismatch"
        )

    return manifest


def _atomic_write(
    path: Path,
    payload: bytes,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )

    temp = Path(temp_name)

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp, path)

    finally:
        temp.unlink(missing_ok=True)


def _snapshot(
    *,
    target_dir: Path,
    state_path: Path,
) -> dict[Path, bytes | None]:
    paths = [
        state_path,
        *(
            target_dir / name
            for name in PUBLIC_FILES
        ),
    ]

    return {
        path: (
            path.read_bytes()
            if path.exists()
            else None
        )
        for path in paths
    }


def _restore(
    snapshot: Mapping[Path, bytes | None],
) -> None:
    for path, payload in snapshot.items():
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, payload)


def publish_tcg_live_bundle(
    *,
    bundle_dir: Path,
    plan: Mapping[str, Any],
    state_path: Path,
    target_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    validate_bundle(
        bundle_dir,
        _validation_plan(plan),
    )

    state = plan.get("state_after_publish")

    if not isinstance(state, Mapping):
        raise ValueError(
            "TCG Live plan is missing state_after_publish"
        )

    result = {
        "published_window":
            plan["latest_completed_window"],
        "target_dir": str(target_dir),
        "dry_run": bool(dry_run),
    }

    if dry_run:
        return result

    snapshot = _snapshot(
        target_dir=target_dir,
        state_path=state_path,
    )

    try:
        for name in PUBLIC_FILES:
            _atomic_write(
                target_dir / name,
                (bundle_dir / name).read_bytes(),
            )

        _atomic_write(
            state_path,
            (
                json.dumps(
                    dict(state),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )

    except Exception:
        _restore(snapshot)
        raise

    return result


__all__ = [
    "PUBLIC_FILES",
    "build_tcg_live_bundle",
    "publish_tcg_live_bundle",
]
