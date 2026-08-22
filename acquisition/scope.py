from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.releases import require_utc


@dataclass(frozen=True)
class ScopePolicy:
    """Resolved acquisition window for one game/format/set."""

    policy_id: str
    game: str
    format: str | None
    set_code: str
    set_name: str
    start_datetime: datetime
    end_datetime: datetime
    catalog_version: str

    def __post_init__(self) -> None:
        policy_id = str(self.policy_id).strip()
        game = str(self.game).strip().upper()
        fmt = None if self.format is None else str(self.format).strip().upper() or None
        set_code = str(self.set_code).strip()
        set_name = str(self.set_name).strip()
        catalog_version = str(self.catalog_version).strip()
        if not policy_id:
            raise ValueError("policy_id must be non-empty")
        if not game:
            raise ValueError("game must be non-empty")
        if not set_code:
            raise ValueError("set_code must be non-empty")
        if not set_name:
            raise ValueError("set_name must be non-empty")
        if not catalog_version:
            raise ValueError("catalog_version must be non-empty")

        start = require_utc(self.start_datetime, field_name="start_datetime")
        end = require_utc(self.end_datetime, field_name="end_datetime")
        if end <= start:
            raise ValueError("end_datetime must be after start_datetime")

        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "game", game)
        object.__setattr__(self, "format", fmt)
        object.__setattr__(self, "set_code", set_code)
        object.__setattr__(self, "set_name", set_name)
        object.__setattr__(self, "catalog_version", catalog_version)
        object.__setattr__(self, "start_datetime", start)
        object.__setattr__(self, "end_datetime", end)


@dataclass(frozen=True)
class EligibilityPolicy:
    """Eligibility rules applied after tournament records are normalized."""

    policy_id: str = "pocket_minimal_v1"
    game: str = "POCKET"
    allowed_formats: tuple[str | None, ...] = (None, "STANDARD")
    require_public: bool = True
    require_decklists: bool = True

    def __post_init__(self) -> None:
        policy_id = str(self.policy_id).strip()
        game = str(self.game).strip().upper()
        if not policy_id:
            raise ValueError("policy_id must be non-empty")
        if not game:
            raise ValueError("game must be non-empty")

        normalized_formats: list[str | None] = []
        for value in tuple(self.allowed_formats):
            normalized = None if value is None else str(value).strip().upper() or None
            if normalized not in normalized_formats:
                normalized_formats.append(normalized)
        if not normalized_formats:
            raise ValueError("allowed_formats must contain at least one value")

        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "game", game)
        object.__setattr__(self, "allowed_formats", tuple(normalized_formats))


__all__ = ["EligibilityPolicy", "ScopePolicy"]
