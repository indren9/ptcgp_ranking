from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from acquisition.scope import EligibilityPolicy, ScopePolicy
from domain.releases import require_utc

EXCLUSION_REASONS = (
    "wrong_game",
    "wrong_format",
    "outside_window",
    "not_public",
    "decklists_disabled",
    "invalid_record",
    "acquisition_failure",
)


@dataclass(frozen=True)
class TournamentSelection:
    """Deterministic exact tournament selection plus aggregate diagnostics."""

    tournament_ids: tuple[str, ...]
    exclusion_counts: Mapping[str, int]
    failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ids = tuple(str(value).strip() for value in self.tournament_ids)
        if any(not value for value in ids):
            raise ValueError("tournament_ids must not contain blank values")
        if len(ids) != len(set(ids)):
            raise ValueError("tournament_ids must be unique")
        if ids != tuple(sorted(ids)):
            raise ValueError("tournament_ids must be sorted")

        counts = {str(key).strip(): int(value) for key, value in dict(self.exclusion_counts).items()}
        if any(not key for key in counts):
            raise ValueError("exclusion reason names must be non-empty")
        if any(value < 0 for value in counts.values()):
            raise ValueError("exclusion counts must be non-negative")

        failures = tuple(str(value).strip() for value in self.failures)
        if any(not value for value in failures):
            raise ValueError("failures must not contain blank values")

        object.__setattr__(self, "tournament_ids", ids)
        object.__setattr__(self, "exclusion_counts", MappingProxyType(counts))
        object.__setattr__(self, "failures", failures)

    @property
    def included_count(self) -> int:
        return len(self.tournament_ids)


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return require_utc(value, field_name="date")
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid tournament date") from exc
    return require_utc(parsed, field_name="date")


def _bool_field(record: Mapping[str, Any], key: str) -> bool:
    value = record.get(key)
    if isinstance(value, bool):
        return value
    raise ValueError(f"{key} must be boolean")


def select_tournaments(
    records: Iterable[Mapping[str, Any]],
    *,
    scope: ScopePolicy,
    eligibility: EligibilityPolicy,
    acquisition_failures: Mapping[str, str] | None = None,
) -> TournamentSelection:
    """Apply the frozen Pocket eligibility policy with one deterministic exclusion reason per record."""
    if scope.game != eligibility.game:
        raise ValueError("scope.game and eligibility.game must match")

    failures_map = {str(key).strip(): str(value).strip() for key, value in dict(acquisition_failures or {}).items()}
    counts = {reason: 0 for reason in EXCLUSION_REASONS}
    included: list[str] = []
    seen_ids: set[str] = set()

    for record in records:
        if not isinstance(record, Mapping):
            counts["invalid_record"] += 1
            continue
        tid = str(record.get("tournament_id") or record.get("id") or "").strip()
        if not tid:
            counts["invalid_record"] += 1
            continue
        if tid in seen_ids:
            raise ValueError(f"duplicate tournament_id in selector input: {tid}")
        seen_ids.add(tid)

        if tid in failures_map:
            counts["acquisition_failure"] += 1
            continue

        try:
            game = str(record["game"]).strip().upper()
            raw_format = record.get("format")
            fmt = None if raw_format is None else str(raw_format).strip().upper() or None
            date = _parse_date(record["date"])
            is_public = _bool_field(record, "is_public")
            decklists = _bool_field(record, "decklists")
        except (KeyError, TypeError, ValueError):
            counts["invalid_record"] += 1
            continue

        if game != eligibility.game:
            counts["wrong_game"] += 1
        elif fmt not in eligibility.allowed_formats:
            counts["wrong_format"] += 1
        elif not (scope.start_datetime <= date < scope.end_datetime):
            counts["outside_window"] += 1
        elif eligibility.require_public and not is_public:
            counts["not_public"] += 1
        elif eligibility.require_decklists and not decklists:
            counts["decklists_disabled"] += 1
        else:
            included.append(tid)

    failure_rows = tuple(f"{tid}: {failures_map[tid]}" for tid in sorted(failures_map))
    return TournamentSelection(
        tournament_ids=tuple(sorted(included)),
        exclusion_counts=counts,
        failures=failure_rows,
    )


__all__ = ["EXCLUSION_REASONS", "TournamentSelection", "select_tournaments"]
