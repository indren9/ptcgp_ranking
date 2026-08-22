"""Isolated Tournament API acquisition contracts and transformations."""

from .aggregation import aggregate_matchups, aggregate_meta
from .contracts import (
    AcquisitionContracts,
    ContractArtifact,
    RawPayloadRef,
    adapt_matchup_raw,
    adapt_top_meta_decklist,
    materialize_dense_score,
)
from .manifest import AcquisitionManifest, AggregationSummary, NormalizedSummary, RawSummary
from .scope import EligibilityPolicy, ScopePolicy
from .selection import TournamentSelection, select_tournaments

__all__ = [
    "AcquisitionContracts",
    "AcquisitionManifest",
    "AggregationSummary",
    "ContractArtifact",
    "EligibilityPolicy",
    "NormalizedSummary",
    "RawPayloadRef",
    "RawSummary",
    "ScopePolicy",
    "TournamentSelection",
    "adapt_matchup_raw",
    "adapt_top_meta_decklist",
    "aggregate_matchups",
    "aggregate_meta",
    "materialize_dense_score",
    "select_tournaments",
]
