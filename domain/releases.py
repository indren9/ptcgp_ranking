from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


def require_utc(value: datetime, *, field_name: str) -> datetime:
    """Return an aware datetime normalized to UTC, rejecting naive values."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ExpansionRelease:
    """Versioned Pocket expansion release used to derive a half-open scope."""

    code: str
    name: str
    release_datetime: datetime
    next_release_datetime: datetime | None
    is_current: bool
    source: str
    catalog_version: str

    def __post_init__(self) -> None:
        code = str(self.code).strip()
        name = str(self.name).strip()
        source = str(self.source).strip()
        catalog_version = str(self.catalog_version).strip()
        if not code:
            raise ValueError("code must be non-empty")
        if not name:
            raise ValueError("name must be non-empty")
        if not source:
            raise ValueError("source must be non-empty")
        if not catalog_version:
            raise ValueError("catalog_version must be non-empty")

        release = require_utc(self.release_datetime, field_name="release_datetime")
        next_release = None
        if self.next_release_datetime is not None:
            next_release = require_utc(
                self.next_release_datetime,
                field_name="next_release_datetime",
            )
            if next_release <= release:
                raise ValueError("next_release_datetime must be after release_datetime")

        if self.is_current and next_release is not None:
            raise ValueError("current expansion must not define next_release_datetime")
        if not self.is_current and next_release is None:
            raise ValueError("completed expansion requires next_release_datetime")

        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "catalog_version", catalog_version)
        object.__setattr__(self, "release_datetime", release)
        object.__setattr__(self, "next_release_datetime", next_release)


@dataclass(frozen=True)
class ReleaseCatalog:
    catalog_version: str
    source: str
    releases: tuple[ExpansionRelease, ...]

    def __post_init__(self) -> None:
        catalog_version = str(self.catalog_version).strip()
        source = str(self.source).strip()
        if not catalog_version:
            raise ValueError("catalog_version must be non-empty")
        if not source:
            raise ValueError("source must be non-empty")
        releases = tuple(sorted(self.releases, key=lambda item: item.release_datetime))
        if not releases:
            raise ValueError("release catalog must not be empty")
        codes = [release.code for release in releases]
        if len(codes) != len(set(codes)):
            raise ValueError("release catalog contains duplicate codes")
        if any(release.catalog_version != catalog_version for release in releases):
            raise ValueError("release catalog entries must share catalog_version")
        if any(release.source != source for release in releases):
            raise ValueError("release catalog entries must share source")
        current = [release for release in releases if release.is_current]
        if len(current) != 1 or current[0] is not releases[-1]:
            raise ValueError("release catalog must have exactly one latest current expansion")
        for left, right in zip(releases, releases[1:]):
            if left.next_release_datetime != right.release_datetime:
                raise ValueError("release catalog next_release_datetime chain is inconsistent")
        object.__setattr__(self, "catalog_version", catalog_version)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "releases", releases)


__all__ = ["ExpansionRelease", "ReleaseCatalog", "require_utc"]
