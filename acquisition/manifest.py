from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from types import MappingProxyType
from typing import Any, Mapping

from acquisition.contracts import AcquisitionContracts, RawPayloadRef
from acquisition.scope import EligibilityPolicy, ScopePolicy
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
    diagnostics: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        for field_name in ("tournaments_rows", "participants_rows", "pairings_rows"):
            value = int(getattr(self, field_name))
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        hashes = {str(key): str(value).strip().lower() for key, value in dict(self.hashes).items()}
        object.__setattr__(self, "hashes", MappingProxyType(hashes))
        diagnostics = {str(key): int(value) for key, value in dict(self.diagnostics or {}).items()}
        if any(value < 0 for value in diagnostics.values()):
            raise ValueError("normalization diagnostics must be non-negative")
        object.__setattr__(self, "diagnostics", MappingProxyType(diagnostics))


@dataclass(frozen=True)
class AggregationSummary:
    total_participants: int
    classified_participants: int
    unclassified_participants: int
    comparable_matches: int
    pairing_exclusion_counts: Mapping[str, int]
    deck_identity_diagnostics: Mapping[str, Any] | None = None

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
        identity_diag = dict(self.deck_identity_diagnostics or {})
        object.__setattr__(self, "deck_identity_diagnostics", MappingProxyType(identity_diag))


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
    eligibility: EligibilityPolicy | None = None

    def __post_init__(self) -> None:
        for field_name in ("schema_version", "run_id", "source", "software_git_revision"):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must be non-empty")
            object.__setattr__(self, field_name, value)
        if self.schema_version == "2" and self.eligibility is None:
            raise ValueError("schema v2 requires eligibility")
        if self.schema_version != "2" and self.eligibility is not None:
            raise ValueError("eligibility requires schema v2")

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

        eligibility_payload: dict[str, Any] = {}
        if self.schema_version == "2":
            assert self.eligibility is not None
            eligibility_payload["eligibility"] = {
                "policy_id": self.eligibility.policy_id,
                "game": self.eligibility.game,
                "allowed_formats": list(self.eligibility.allowed_formats),
                "require_public": self.eligibility.require_public,
                "require_decklists": self.eligibility.require_decklists,
                "require_online": self.eligibility.require_online,
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
            **eligibility_payload,
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
                "diagnostics": dict(self.normalized.diagnostics),
            },
            "aggregation": {
                "total_participants": self.aggregation.total_participants,
                "classified_participants": self.aggregation.classified_participants,
                "unclassified_participants": self.aggregation.unclassified_participants,
                "comparable_matches": self.aggregation.comparable_matches,
                "pairing_exclusion_counts": dict(self.aggregation.pairing_exclusion_counts),
                "deck_identity_diagnostics": dict(self.aggregation.deck_identity_diagnostics),
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
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version not in {"1", "1.0", "2"}:
        raise ValueError(f"unsupported manifest schema_version: {schema_version}")

    has_eligibility = "eligibility" in payload
    if schema_version == "2":
        if not has_eligibility:
            raise ValueError("schema v2 manifest requires eligibility")
        eligibility = payload["eligibility"]
        if not isinstance(eligibility, Mapping):
            raise ValueError("manifest eligibility must be an object")

        required_eligibility = {
            "policy_id",
            "game",
            "allowed_formats",
            "require_public",
            "require_decklists",
            "require_online",
        }
        missing_eligibility = required_eligibility - set(eligibility)
        if missing_eligibility:
            raise ValueError(
                "manifest eligibility missing keys: "
                f"{sorted(missing_eligibility)}"
            )

        policy_id = str(eligibility.get("policy_id") or "").strip()
        game = str(eligibility.get("game") or "").strip().upper()
        if not policy_id:
            raise ValueError("manifest eligibility policy_id must be non-empty")
        if not game:
            raise ValueError("manifest eligibility game must be non-empty")

        allowed_formats = eligibility.get("allowed_formats")
        if not isinstance(allowed_formats, list) or not allowed_formats:
            raise ValueError(
                "manifest eligibility allowed_formats must be a non-empty list"
            )
        normalized_formats: list[str | None] = []
        for value in allowed_formats:
            if value is None:
                normalized = None
            elif isinstance(value, str) and value.strip():
                normalized = value.strip().upper()
            else:
                raise ValueError(
                    "manifest eligibility allowed_formats values must be non-empty strings or null"
                )
            if normalized in normalized_formats:
                raise ValueError(
                    "manifest eligibility allowed_formats must be unique"
                )
            normalized_formats.append(normalized)

        for field_name in ("require_public", "require_decklists"):
            if not isinstance(eligibility.get(field_name), bool):
                raise ValueError(
                    f"manifest eligibility {field_name} must be boolean"
                )
        require_online = eligibility.get("require_online")
        if require_online is not None and not isinstance(require_online, bool):
            raise ValueError(
                "manifest eligibility require_online must be boolean or null"
            )

        scope = payload.get("scope")
        if not isinstance(scope, Mapping):
            raise ValueError("manifest scope must be an object")
        scope_game = str(scope.get("game") or "").strip().upper()
        raw_scope_format = scope.get("format")
        scope_format = (
            None
            if raw_scope_format is None
            else str(raw_scope_format).strip().upper() or None
        )
        if game != scope_game:
            raise ValueError(
                "manifest eligibility game does not match scope game"
            )
        if scope_format not in normalized_formats:
            raise ValueError(
                "manifest scope format is not allowed by eligibility"
            )
    elif has_eligibility:
        raise ValueError("legacy manifest must not contain eligibility")

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
