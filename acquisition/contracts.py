from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any

import numpy as np
import pandas as pd

from domain.releases import require_utc

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TOP_META_COLUMNS = ("Rank", "Deck ID", "Deck", "Count", "Share_%")
MATCHUP_COLUMNS = ("Deck A ID", "Deck A", "Deck B ID", "Deck B", "W", "L", "T", "N", "WR_dir")
DENSE_SCORE_COLUMNS = MATCHUP_COLUMNS


@dataclass(frozen=True)
class RawPayloadRef:
    """Immutable reference to one canonical JSON payload in the raw store."""

    payload_type: str
    snapshot_id: str
    sha256: str
    fetched_at: datetime
    relative_path: str
    tournament_id: str | None = None

    def __post_init__(self) -> None:
        payload_type = str(self.payload_type).strip()
        snapshot_id = str(self.snapshot_id).strip()
        sha256 = str(self.sha256).strip().lower()
        relative_path = str(self.relative_path).strip().replace("\\", "/")
        tournament_id = None if self.tournament_id is None else str(self.tournament_id).strip() or None
        if not payload_type:
            raise ValueError("payload_type must be non-empty")
        if not snapshot_id:
            raise ValueError("snapshot_id must be non-empty")
        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError("sha256 must be a 64-character lowercase hexadecimal digest")
        if not relative_path:
            raise ValueError("relative_path must be non-empty")
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside the raw store")

        object.__setattr__(self, "payload_type", payload_type)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "fetched_at", require_utc(self.fetched_at, field_name="fetched_at"))
        object.__setattr__(self, "relative_path", path.as_posix())
        object.__setattr__(self, "tournament_id", tournament_id)


@dataclass(frozen=True)
class ContractArtifact:
    """Manifest-facing description of one generated contract artifact."""

    name: str
    columns: tuple[str, ...]
    row_count: int
    sha256: str | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        columns = tuple(str(value) for value in self.columns)
        row_count = int(self.row_count)
        sha256 = None if self.sha256 is None else str(self.sha256).strip().lower()
        if not name:
            raise ValueError("name must be non-empty")
        if not columns or any(not value for value in columns):
            raise ValueError("columns must be a non-empty tuple of non-empty names")
        if row_count < 0:
            raise ValueError("row_count must be non-negative")
        if sha256 is not None and not _SHA256_RE.fullmatch(sha256):
            raise ValueError("sha256 must be a 64-character lowercase hexadecimal digest")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "sha256", sha256)


@dataclass(frozen=True)
class AcquisitionContracts:
    """Canonical acquisition-boundary artifacts, independent from production core."""

    top_meta_decklist: ContractArtifact
    matchup_raw: ContractArtifact
    dense_score: ContractArtifact

    def __post_init__(self) -> None:
        expected = {
            "top_meta_decklist": TOP_META_COLUMNS,
            "matchup_raw": MATCHUP_COLUMNS,
            "dense_score": DENSE_SCORE_COLUMNS,
        }
        actual = {
            "top_meta_decklist": self.top_meta_decklist.columns,
            "matchup_raw": self.matchup_raw.columns,
            "dense_score": self.dense_score.columns,
        }
        for name, columns in actual.items():
            if columns != expected[name]:
                raise ValueError(f"{name} columns do not match the canonical acquisition contract")


def _clean_deck(value: Any, *, field_name: str) -> str:
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _coerce_nonnegative_counts(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in ("W", "L", "T"):
        values = pd.to_numeric(out[column], errors="raise")
        if values.isna().any() or (values < 0).any():
            raise ValueError(f"{column} must contain non-negative counts")
        if not np.all(np.equal(values, np.floor(values))):
            raise ValueError(f"{column} must contain integer counts")
        out[column] = values.astype("int64")
    return out


def adapt_top_meta_decklist(meta: pd.DataFrame, *, include_legacy_share: bool = False) -> pd.DataFrame:
    required = {"Rank", "Deck ID", "Deck", "Count", "Share_%"}
    missing = required - set(meta.columns)
    if missing:
        raise KeyError(f"meta aggregation missing columns: {sorted(missing)}")
    out = meta.loc[:, list(TOP_META_COLUMNS)].copy()
    out["Rank"] = pd.to_numeric(out["Rank"], errors="raise").astype("int64")
    out["Count"] = pd.to_numeric(out["Count"], errors="raise").astype("int64")
    out["Share_%"] = pd.to_numeric(out["Share_%"], errors="raise").astype(float)
    out["Deck ID"] = out["Deck ID"].map(lambda value: _clean_deck(value, field_name="Deck ID"))
    out["Deck"] = out["Deck"].map(lambda value: _clean_deck(value, field_name="Deck"))
    out = out.sort_values(["Rank", "Deck", "Deck ID"], kind="mergesort").reset_index(drop=True)
    if include_legacy_share:
        out["Share"] = out["Share_%"].map(lambda value: f"{value:.2f}%")
    assert_no_player_ids(out)
    return out


def adapt_matchup_raw(matchups: pd.DataFrame) -> pd.DataFrame:
    required = {"Deck A ID", "Deck A", "Deck B ID", "Deck B", "W", "L", "T"}
    missing = required - set(matchups.columns)
    if missing:
        raise KeyError(f"matchup aggregation missing columns: {sorted(missing)}")
    out = matchups.loc[:, ["Deck A ID", "Deck A", "Deck B ID", "Deck B", "W", "L", "T"]].copy()
    for column in ("Deck A ID", "Deck A", "Deck B ID", "Deck B"):
        out[column] = out[column].map(lambda value, c=column: _clean_deck(value, field_name=c))
    if (out["Deck A ID"] == out["Deck B ID"]).any():
        raise ValueError("matchup_raw must not contain same-ID mirror rows")
    if out.duplicated(["Deck A ID", "Deck B ID"]).any():
        raise ValueError("matchup_raw contains duplicate directional deck-ID rows")
    _validate_contract_identity_labels(out)
    out = _coerce_nonnegative_counts(out)
    out["N"] = (out["W"] + out["L"] + out["T"]).astype("int64")
    decisive = (out["W"] + out["L"]).to_numpy(dtype=float)
    wins = out["W"].to_numpy(dtype=float)
    wr = np.full(len(out), np.nan, dtype=float)
    np.divide(100.0 * wins, decisive, out=wr, where=decisive > 0)
    out["WR_dir"] = wr
    out = out.loc[:, list(MATCHUP_COLUMNS)].sort_values(
        ["Deck A ID", "Deck B ID", "Deck A", "Deck B"], kind="mergesort"
    ).reset_index(drop=True)
    _validate_observed_symmetry(out)
    assert_no_player_ids(out)
    return out


def _validate_contract_identity_labels(matchups: pd.DataFrame) -> None:
    identity_rows = pd.concat(
        [
            matchups[["Deck A ID", "Deck A"]].rename(columns={"Deck A ID": "Deck ID", "Deck A": "Deck"}),
            matchups[["Deck B ID", "Deck B"]].rename(columns={"Deck B ID": "Deck ID", "Deck B": "Deck"}),
        ],
        ignore_index=True,
    ).drop_duplicates()
    by_id = identity_rows.groupby("Deck ID")["Deck"].nunique(dropna=True)
    bad = by_id[by_id > 1]
    if not bad.empty:
        raise ValueError(f"deck ID maps to multiple display names in acquisition contract: {bad.index[0]}")


def _validate_observed_symmetry(matchups: pd.DataFrame) -> None:
    if matchups.empty:
        return
    lookup = matchups.set_index(["Deck A ID", "Deck B ID"])
    for (a_id, b_id), row in lookup.iterrows():
        if (b_id, a_id) not in lookup.index:
            raise ValueError(f"missing reverse direction for observed pair: {a_id} vs {b_id}")
        reverse = lookup.loc[(b_id, a_id)]
        if int(row["W"]) != int(reverse["L"]):
            raise ValueError(f"W/L symmetry failed for {a_id} vs {b_id}")
        if int(row["L"]) != int(reverse["W"]):
            raise ValueError(f"L/W symmetry failed for {a_id} vs {b_id}")
        if int(row["T"]) != int(reverse["T"]):
            raise ValueError(f"tie symmetry failed for {a_id} vs {b_id}")


def materialize_dense_score(
    matchup_raw: pd.DataFrame,
    decks: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> pd.DataFrame:
    """Materialize every ordered unequal deck-ID pair on the acquisition axis."""
    axis = tuple(
        (
            _clean_deck(deck_id, field_name="deck axis ID"),
            _clean_deck(deck_name, field_name="deck axis display name"),
        )
        for deck_id, deck_name in decks
    )
    ids = tuple(deck_id for deck_id, _ in axis)
    if len(ids) != len(set(ids)):
        raise ValueError("deck axis IDs must be unique")
    observed = adapt_matchup_raw(matchup_raw)
    axis_set = set(ids)
    observed = observed[observed["Deck A ID"].isin(axis_set) & observed["Deck B ID"].isin(axis_set)]
    lookup = observed.set_index(["Deck A ID", "Deck B ID"])

    rows: list[dict[str, Any]] = []
    for deck_a_id, deck_a in axis:
        for deck_b_id, deck_b in axis:
            if deck_a_id == deck_b_id:
                continue
            if (deck_a_id, deck_b_id) in lookup.index:
                row = lookup.loc[(deck_a_id, deck_b_id)]
                wins = int(row["W"])
                losses = int(row["L"])
                ties = int(row["T"])
            else:
                wins = losses = ties = 0
            n = wins + losses + ties
            decisive = wins + losses
            rows.append(
                {
                    "Deck A ID": deck_a_id,
                    "Deck A": deck_a,
                    "Deck B ID": deck_b_id,
                    "Deck B": deck_b,
                    "W": wins,
                    "L": losses,
                    "T": ties,
                    "N": n,
                    "WR_dir": (100.0 * wins / decisive) if decisive > 0 else np.nan,
                }
            )

    dense = pd.DataFrame(rows, columns=list(DENSE_SCORE_COLUMNS))
    for column in ("W", "L", "T", "N"):
        dense[column] = dense[column].astype("int64")
    dense["WR_dir"] = pd.Series(dense["WR_dir"].to_numpy(dtype=float), dtype=float)
    expected_rows = len(axis) * max(0, len(axis) - 1)
    if len(dense) != expected_rows:
        raise AssertionError("dense score cardinality mismatch")
    assert_no_player_ids(dense)
    return dense


def hash_dataframe(df: pd.DataFrame) -> str:
    """Stable SHA-256 over columns and ordered records with explicit nulls."""
    normalized = df.copy().astype(object)
    normalized = normalized.where(pd.notna(normalized), None)
    payload = {
        "columns": [str(column) for column in normalized.columns],
        "records": normalized.to_dict(orient="records"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_acquisition_contracts(
    top_meta_decklist: pd.DataFrame,
    matchup_raw: pd.DataFrame,
    dense_score: pd.DataFrame,
) -> AcquisitionContracts:
    canonical_top = adapt_top_meta_decklist(top_meta_decklist)
    canonical_matchups = adapt_matchup_raw(matchup_raw)
    if tuple(dense_score.columns) != DENSE_SCORE_COLUMNS:
        raise ValueError("dense_score columns do not match canonical contract")
    assert_no_player_ids(dense_score)
    return AcquisitionContracts(
        top_meta_decklist=ContractArtifact(
            "top_meta_decklist",
            TOP_META_COLUMNS,
            len(canonical_top),
            hash_dataframe(canonical_top),
        ),
        matchup_raw=ContractArtifact(
            "matchup_raw",
            MATCHUP_COLUMNS,
            len(canonical_matchups),
            hash_dataframe(canonical_matchups),
        ),
        dense_score=ContractArtifact(
            "dense_score",
            DENSE_SCORE_COLUMNS,
            len(dense_score),
            hash_dataframe(dense_score),
        ),
    )


def assert_no_player_ids(df: pd.DataFrame) -> None:
    forbidden = [column for column in df.columns if "player" in str(column).strip().lower()]
    if forbidden:
        raise ValueError(f"public acquisition contract contains player identifier columns: {forbidden}")


__all__ = [
    "AcquisitionContracts",
    "ContractArtifact",
    "DENSE_SCORE_COLUMNS",
    "MATCHUP_COLUMNS",
    "RawPayloadRef",
    "TOP_META_COLUMNS",
    "adapt_matchup_raw",
    "adapt_top_meta_decklist",
    "assert_no_player_ids",
    "build_acquisition_contracts",
    "hash_dataframe",
    "materialize_dense_score",
]
