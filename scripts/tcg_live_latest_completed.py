from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot,
    resolve_release,
)


SCHEMA_VERSION = 1

DEFAULT_CATALOG = Path("data/reference/ptcg_live_windows.json")
DEFAULT_STATE = Path(
    ".github/tcg-live-latest-completed-meta-state.json"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def read_json(
    path: Path,
    *,
    missing: Any = None,
) -> Any:
    if not path.exists():
        return missing

    return json.loads(path.read_text(encoding="utf-8"))


def window_dict(release) -> dict[str, Any]:
    return {
        "code": release.code,
        "name": release.name,
        "start": (
            release.release_datetime
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "end": (
            None
            if release.next_release_datetime is None
            else release.next_release_datetime
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "catalog_version": release.catalog_version,
    }


def build_plan(
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    state: Mapping[str, Any] | None = None,
    acquisition_started_at: datetime | None = None,
) -> dict[str, Any]:
    started = acquisition_started_at or utc_now()

    catalog = load_release_catalog_snapshot(catalog_path)

    completed = resolve_release(
        catalog,
        mode="latest_completed",
        acquisition_started_at=started,
    )

    current_window = window_dict(completed)

    previous = (
        (state or {})
        .get("published_completed_window")
    )

    previous_code = str(
        (previous or {}).get("code") or ""
    ).strip()

    changed = (
        not previous_code
        or previous_code.casefold()
        != completed.code.casefold()
    )

    action = (
        "publish_required"
        if changed
        else "noop"
    )

    reason = (
        "no_published_state"
        if not previous_code
        else (
            "latest_completed_changed"
            if changed
            else "latest_completed_already_published"
        )
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "game": "PTCG",
        "format": "STANDARD",
        "platform": "PTCGL",
        "action": action,
        "reason": reason,
        "latest_completed_window": current_window,
        "state_after_publish": {
            "schema_version": SCHEMA_VERSION,
            "game": "PTCG",
            "format": "STANDARD",
            "platform": "PTCGL",
            "published_completed_window": current_window,
            "updated_at": (
                started
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z")
            ),
        },
    }


def _write_json(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan the dedicated Pokémon TCG Live "
            "latest-completed production action."
        )
    )

    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
    )

    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    state = read_json(
        args.state,
        missing=None,
    )

    plan = build_plan(
        catalog_path=args.catalog,
        state=state,
    )

    if args.output is not None:
        _write_json(
            args.output,
            plan,
        )

    print(
        json.dumps(
            plan,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
