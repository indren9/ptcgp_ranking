from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from types import MappingProxyType
from typing import Any, Mapping

from acquisition.contracts import AcquisitionContracts, RawPayloadRef
from acquisition.scope import ScopePolicy
from acquisition.selection import TournamentSelection
from domain.releases import require_utc


@dataclass(frozen=True)
class RawSummary:
    snapshot_refs: tuple[RawPayloadRef, ...]


@dataclass(frozen=True)
class NormalizedSummary:
    tournaments_rows: int
    participants_rows: int
    pairings_rows: int
    hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        for field_name in ("tournaments_rows", "participants_rows", "pairings_rows"):
            value = int(getattr(self, field_name))
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        hashes = {str(key): str(value).strip().lower() for key, value in dict(self.hashes).items()}
        object.__setattr__(self, "hashes", MappingProxyType(hashes))


@dataclass(frozen=True)
class AggregationSummary:
    total_participants: int
    classified_participants: int
    unclassified_participants: int
    comparable_matches: int
    pairing_exclusion_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        values = (
            self.total_participants,
            self.classified_participants,
            self.unclassified_participants,
            self.comparable_matches,
        )
        if any(int(value) < 0 for value in values):
            raise ValueError("aggregation counts must be non-negative")
        if self.classified_participants + self.unclassified_participants != self.total_participants:
            raise ValueError("classified + unclassified must equal total_participants")
        if any(int(value) < 0 for value in self.pairing_exclusion_counts.values()):
            raise ValueError("pairing exclusion counts must be non-negative")
        object.__setattr__(self, "total_participants", int(self.total_participants))
        object.__setattr__(self, "classified_participants", int(self.classified_participants))
        object.__setattr__(self, "unclassified_participants", int(self.unclassified_participants))
        object.__setattr__(self, "comparable_matches", int(self.comparable_matches))
        counts = {str(key): int(value) for key, value in dict(self.pairing_exclusion_counts).items()}
        object.__setattr__(self, "pairing_exclusion_counts", MappingProxyType(counts))


@dataclass(frozen=True)
class AcquisitionManifest:
    """Reproducibility manifest for the isolated Tournament API acquisition layer."""

    schema_version: str
    run_id: str
    created_at: datetime
    acquisition_started_at: datetime
    source: str
    software_git_revision: str
    scope: ScopePolicy
    selection: TournamentSelection
    raw: RawSummary
    normalized: NormalizedSummary
    aggregation: AggregationSummary
    rate_limit_observations: tuple[Mapping[str, Any], ...]
    contracts: AcquisitionContracts

    def __post_init__(self) -> None:
        for field_name in ("schema_version", "run_id", "source", "software_git_revision"):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must be non-empty")
            object.__setattr__(self, field_name, value)
        created_at = require_utc(self.created_at, field_name="created_at")
        started_at = require_utc(self.acquisition_started_at, field_name="acquisition_started_at")
        if created_at < started_at:
            raise ValueError("created_at must not precede acquisition_started_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "acquisition_started_at", started_at)
        observations = tuple(MappingProxyType(dict(observation)) for observation in self.rate_limit_observations)
        object.__setattr__(self, "rate_limit_observations", observations)

    def to_dict(self) -> dict[str, Any]:
        def iso(value: datetime) -> str:
            return value.isoformat().replace("+00:00", "Z")

        raw_refs = [
            {
                "payload_type": ref.payload_type,
                "tournament_id": ref.tournament_id,
                "snapshot_id": ref.snapshot_id,
                "sha256": ref.sha256,
                "fetched_at": iso(ref.fetched_at),
                "relative_path": ref.relative_path,
            }
            for ref in self.raw.snapshot_refs
        ]
        artifacts = {}
        for field_name in ("top_meta_decklist", "matchup_raw", "dense_score"):
            artifact = getattr(self.contracts, field_name)
            artifacts[field_name] = {
                "columns": list(artifact.columns),
                "row_count": artifact.row_count,
                "sha256": artifact.sha256,
            }

        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": iso(self.created_at),
            "acquisition_started_at": iso(self.acquisition_started_at),
            "source": self.source,
            "software": {"git_revision": self.software_git_revision},
            "scope": {
                "policy_id": self.scope.policy_id,
                "game": self.scope.game,
                "format": self.scope.format,
                "set_code": self.scope.set_code,
                "set_name": self.scope.set_name,
                "start": iso(self.scope.start_datetime),
                "end": iso(self.scope.end_datetime),
                "catalog_version": self.scope.catalog_version,
            },
            "selection": {
                "tournament_ids": list(self.selection.tournament_ids),
                "included_count": self.selection.included_count,
                "exclusion_counts": dict(self.selection.exclusion_counts),
                "failures": list(self.selection.failures),
            },
            "raw": {"snapshot_refs": raw_refs},
            "normalized": {
                "row_counts": {
                    "tournaments": self.normalized.tournaments_rows,
                    "participants": self.normalized.participants_rows,
                    "pairings": self.normalized.pairings_rows,
                },
                "hashes": dict(self.normalized.hashes),
            },
            "aggregation": {
                "total_participants": self.aggregation.total_participants,
                "classified_participants": self.aggregation.classified_participants,
                "unclassified_participants": self.aggregation.unclassified_participants,
                "comparable_matches": self.aggregation.comparable_matches,
                "pairing_exclusion_counts": dict(self.aggregation.pairing_exclusion_counts),
            },
            "rate_limit_observations": [dict(item) for item in self.rate_limit_observations],
            "contracts": artifacts,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        payload = self.to_dict()
        validate_manifest_dict(payload)
        return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True) + ("\n" if indent is not None else "")


def validate_manifest_dict(payload: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "run_id",
        "created_at",
        "acquisition_started_at",
        "source",
        "software",
        "scope",
        "selection",
        "raw",
        "normalized",
        "aggregation",
        "rate_limit_observations",
        "contracts",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"manifest missing keys: {sorted(missing)}")
    selection = payload["selection"]
    ids = list(selection.get("tournament_ids") or [])
    if ids != sorted(set(ids)):
        raise ValueError("manifest selection tournament_ids must be sorted and unique")
    if int(selection.get("included_count", -1)) != len(ids):
        raise ValueError("manifest included_count does not match tournament_ids")
    if "player_id" in json.dumps(payload, ensure_ascii=False).lower():
        raise ValueError("public manifest must not contain player_id")


__all__ = [
    "AcquisitionManifest",
    "AggregationSummary",
    "NormalizedSummary",
    "RawSummary",
    "validate_manifest_dict",
]
