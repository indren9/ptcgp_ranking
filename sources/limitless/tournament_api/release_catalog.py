from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Callable, Iterable

from acquisition.scope import ScopePolicy
from domain.releases import ExpansionRelease, ReleaseCatalog, require_utc


def parse_utc_datetime(value: str | datetime, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return require_utc(value, field_name=field_name)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    return require_utc(parsed, field_name=field_name)


def load_release_catalog_snapshot(path: str | Path) -> ReleaseCatalog:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("release catalog snapshot must be a JSON object")
    catalog_version = str(data.get("catalog_version") or "").strip()
    source = str(data.get("source") or "").strip()
    items = data.get("releases")
    if not isinstance(items, list):
        raise ValueError("release catalog snapshot requires a releases array")

    releases = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("release catalog entry must be an object")
        next_raw = item.get("next_release_datetime")
        releases.append(
            ExpansionRelease(
                code=item.get("code", ""),
                name=item.get("name", ""),
                release_datetime=parse_utc_datetime(item.get("release_datetime"), field_name="release_datetime"),
                next_release_datetime=(
                    None
                    if next_raw is None
                    else parse_utc_datetime(next_raw, field_name="next_release_datetime")
                ),
                is_current=bool(item.get("is_current", False)),
                source=str(item.get("source") or source),
                catalog_version=str(item.get("catalog_version") or catalog_version),
            )
        )
    return ReleaseCatalog(catalog_version=catalog_version, source=source, releases=tuple(releases))


def save_release_catalog_snapshot(catalog: ReleaseCatalog, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "catalog_version": catalog.catalog_version,
        "source": catalog.source,
        "releases": [
            {
                "code": release.code,
                "name": release.name,
                "release_datetime": release.release_datetime.isoformat().replace("+00:00", "Z"),
                "next_release_datetime": (
                    None
                    if release.next_release_datetime is None
                    else release.next_release_datetime.isoformat().replace("+00:00", "Z")
                ),
                "is_current": release.is_current,
                "source": release.source,
                "catalog_version": release.catalog_version,
            }
            for release in catalog.releases
        ],
    }
    temp = out.with_suffix(out.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(out)
    return out


def prompt_choose(
    releases: Iterable[ExpansionRelease],
    *,
    input_fn=input,
    output_fn=print,
) -> str:
    items = tuple(releases)
    if not items:
        raise ValueError("cannot choose from an empty release catalog")
    for index, release in enumerate(items, start=1):
        marker = " [current]" if release.is_current else ""
        output_fn(f"{index:>2}. {release.code} — {release.name}{marker}")
    raw = str(input_fn("Select expansion: ")).strip()
    try:
        index = int(raw)
    except ValueError as exc:
        raise ValueError("selection must be a numeric index") from exc
    if index < 1 or index > len(items):
        raise ValueError("selection index out of range")
    return items[index - 1].code


def resolve_release(
    catalog: ReleaseCatalog,
    *,
    mode: str,
    acquisition_started_at: datetime,
    code: str | None = None,
    chooser: Callable[[Iterable[ExpansionRelease]], str] | None = None,
) -> ExpansionRelease:
    started = require_utc(acquisition_started_at, field_name="acquisition_started_at")
    normalized_mode = str(mode).strip().lower()
    if normalized_mode == "choose":
        selected_code = (chooser or prompt_choose)(catalog.releases)
        return resolve_release(
            catalog,
            mode="code",
            code=selected_code,
            acquisition_started_at=started,
        )
    if normalized_mode == "code":
        target = str(code or "").strip()
        if not target:
            raise ValueError("code is required when mode='code'")
        for release in catalog.releases:
            if release.code == target:
                return release
        raise KeyError(f"unknown expansion code: {target}")
    if normalized_mode == "auto":
        eligible = [release for release in catalog.releases if release.release_datetime <= started]
        if not eligible:
            raise ValueError("no released expansion exists at acquisition_started_at")
        return max(eligible, key=lambda release: release.release_datetime)
    raise ValueError("mode must be one of: auto, code, choose")


def scope_for_release(
    release: ExpansionRelease,
    *,
    acquisition_started_at: datetime,
    game: str = "POCKET",
    format: str | None = "STANDARD",
    policy_id: str = "pocket_release_window_v1",
) -> ScopePolicy:
    started = require_utc(acquisition_started_at, field_name="acquisition_started_at")
    if started <= release.release_datetime:
        raise ValueError("acquisition_started_at must be after release_datetime")
    end = release.next_release_datetime or started
    if end > started:
        raise ValueError("cannot acquire a completed release window before its next release boundary")
    return ScopePolicy(
        policy_id=policy_id,
        game=game,
        format=format,
        set_code=release.code,
        set_name=release.name,
        start_datetime=release.release_datetime,
        end_datetime=end,
        catalog_version=release.catalog_version,
    )


__all__ = [
    "load_release_catalog_snapshot",
    "parse_utc_datetime",
    "prompt_choose",
    "resolve_release",
    "save_release_catalog_snapshot",
    "scope_for_release",
]
