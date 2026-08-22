from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from acquisition.contracts import MATCHUP_COLUMNS

META_COLUMNS = ("Deck ID", "Deck", "Count", "Share_%", "Rank")
PAIRING_DIAGNOSTIC_KEYS = (
    "bye",
    "missing_participant",
    "missing_deck",
    "double_loss",
    "unresolved_result",
    "same_archetype",
    "duplicate_pairing",
)


class AggregationConflictError(ValueError):
    pass


@dataclass(frozen=True)
class MetaAggregationResult:
    meta: pd.DataFrame
    total_participants: int
    classified_participants: int
    unclassified_participants: int
    classification_coverage: float
    duplicate_display_names: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class MatchAggregationResult:
    matchups: pd.DataFrame
    comparable_matches: int
    pairing_exclusion_counts: dict[str, int]


def _clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _validate_deck_identity(participants: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    """Validate canonical deck_id -> label mapping and diagnose duplicate labels."""
    required = {"deck_id", "deck_name"}
    if not required.issubset(participants.columns):
        raise KeyError(f"participants missing columns: {sorted(required - set(participants.columns))}")
    classified = participants.copy()
    classified["_deck_id"] = classified["deck_id"].map(_clean_text)
    classified["_deck_name"] = classified["deck_name"].map(_clean_text)
    classified = classified[classified["_deck_id"].notna() & classified["_deck_name"].notna()]
    if classified.empty:
        return {}

    by_id = classified.groupby("_deck_id")["_deck_name"].nunique(dropna=True)
    bad_ids = by_id[by_id > 1]
    if not bad_ids.empty:
        raise AggregationConflictError(f"deck_id maps to multiple deck names: {bad_ids.index[0]}")

    duplicate_display_names: dict[str, tuple[str, ...]] = {}
    for deck_name, group in classified.groupby("_deck_name", sort=True):
        ids = tuple(sorted(set(group["_deck_id"].astype(str))))
        if len(ids) > 1:
            duplicate_display_names[str(deck_name)] = ids
    return duplicate_display_names


def aggregate_meta(participants: pd.DataFrame) -> MetaAggregationResult:
    required = {"tournament_id", "player_id", "deck_id", "deck_name"}
    missing = required - set(participants.columns)
    if missing:
        raise KeyError(f"participants missing columns: {sorted(missing)}")

    if participants.duplicated(["tournament_id", "player_id"]).any():
        raise AggregationConflictError("duplicate participant join key")
    duplicate_display_names = _validate_deck_identity(participants)

    df = participants.copy()
    df["_deck_id"] = df["deck_id"].map(_clean_text)
    df["_deck_name"] = df["deck_name"].map(_clean_text)
    classified = df[df["_deck_id"].notna() & df["_deck_name"].notna()].copy()

    total = int(len(df))
    classified_count = int(len(classified))
    unclassified = total - classified_count
    coverage = (classified_count / total) if total else 0.0

    if classified.empty:
        meta = pd.DataFrame(columns=list(META_COLUMNS))
    else:
        meta = (
            classified.groupby(["_deck_id", "_deck_name"], as_index=False)
            .size()
            .rename(columns={"_deck_id": "Deck ID", "_deck_name": "Deck", "size": "Count"})
        )
        meta["Share_%"] = 100.0 * meta["Count"].astype(float) / float(classified_count)
        meta = meta.sort_values(
            ["Count", "Deck", "Deck ID"],
            ascending=[False, True, True],
            kind="mergesort",
        ).reset_index(drop=True)
        meta["Rank"] = np.arange(1, len(meta) + 1, dtype=int)
        meta = meta[list(META_COLUMNS)]
        meta["Count"] = meta["Count"].astype("Int64")
        meta["Rank"] = meta["Rank"].astype("Int64")

    return MetaAggregationResult(
        meta=meta,
        total_participants=total,
        classified_participants=classified_count,
        unclassified_participants=unclassified,
        classification_coverage=coverage,
        duplicate_display_names=duplicate_display_names,
    )


def _participant_lookup(participants: pd.DataFrame) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    required = {"tournament_id", "player_id", "deck_id", "deck_name"}
    missing = required - set(participants.columns)
    if missing:
        raise KeyError(f"participants missing columns: {sorted(missing)}")
    if participants.duplicated(["tournament_id", "player_id"]).any():
        raise AggregationConflictError("duplicate participant join key")
    _validate_deck_identity(participants)

    out: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for row in participants.itertuples(index=False):
        tid = str(getattr(row, "tournament_id")).strip()
        player_id = str(getattr(row, "player_id")).strip()
        out[(tid, player_id)] = (
            _clean_text(getattr(row, "deck_id")),
            _clean_text(getattr(row, "deck_name")),
        )
    return out


def _pairing_signature(row: pd.Series, columns: list[str]) -> tuple[Any, ...]:
    values: list[Any] = []
    for column in columns:
        value = row[column]
        if pd.isna(value):
            value = None
        values.append(value)
    return tuple(values)


def _dedupe_pairings(pairings: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if "pairing_key" not in pairings.columns:
        raise KeyError("pairings missing column: pairing_key")
    compare_cols = [column for column in pairings.columns if column != "pairing_key"]
    rows = []
    duplicate_count = 0
    for key, group in pairings.groupby("pairing_key", sort=False, dropna=False):
        if len(group) == 1:
            rows.append(group.iloc[0])
            continue
        signatures = {_pairing_signature(row, compare_cols) for _, row in group.iterrows()}
        if len(signatures) != 1:
            raise AggregationConflictError(f"conflicting duplicate pairing: {key}")
        duplicate_count += len(group) - 1
        rows.append(group.iloc[0])
    if not rows:
        return pairings.iloc[0:0].copy(), duplicate_count
    out = pd.DataFrame(rows).reset_index(drop=True)
    return out.reindex(columns=pairings.columns), duplicate_count


def aggregate_matchups(participants: pd.DataFrame, pairings: pd.DataFrame) -> MatchAggregationResult:
    required_pairings = {
        "tournament_id",
        "player1",
        "player2",
        "winner",
        "pairing_key",
    }
    missing = required_pairings - set(pairings.columns)
    if missing:
        raise KeyError(f"pairings missing columns: {sorted(missing)}")

    lookup = _participant_lookup(participants)
    clean_pairings, duplicate_count = _dedupe_pairings(pairings)
    diagnostics = {key: 0 for key in PAIRING_DIAGNOSTIC_KEYS}
    diagnostics["duplicate_pairing"] = int(duplicate_count)
    counts: dict[tuple[str, str, str, str], list[int]] = {}
    comparable_matches = 0

    def add(
        a_id: str,
        a_name: str,
        b_id: str,
        b_name: str,
        *,
        w: int = 0,
        l: int = 0,
        t: int = 0,
    ) -> None:
        bucket = counts.setdefault((a_id, a_name, b_id, b_name), [0, 0, 0])
        bucket[0] += int(w)
        bucket[1] += int(l)
        bucket[2] += int(t)

    for _, row in clean_pairings.iterrows():
        tid = str(row["tournament_id"]).strip()
        p1 = _clean_text(row["player1"])
        p2 = _clean_text(row["player2"])
        winner = row["winner"]
        if p1 is None:
            diagnostics["missing_participant"] += 1
            continue
        if p2 is None:
            diagnostics["bye"] += 1
            continue

        key1 = (tid, p1)
        key2 = (tid, p2)
        if key1 not in lookup or key2 not in lookup:
            diagnostics["missing_participant"] += 1
            continue
        deck1_id, deck1_name = lookup[key1]
        deck2_id, deck2_name = lookup[key2]
        if not all((deck1_id, deck1_name, deck2_id, deck2_name)):
            diagnostics["missing_deck"] += 1
            continue
        if deck1_id == deck2_id:
            diagnostics["same_archetype"] += 1
            continue

        if winner in (-1, "-1"):
            diagnostics["double_loss"] += 1
            continue
        if winner in (0, "0"):
            add(deck1_id, deck1_name, deck2_id, deck2_name, t=1)
            add(deck2_id, deck2_name, deck1_id, deck1_name, t=1)
            comparable_matches += 1
            continue

        winner_text = _clean_text(winner)
        if winner_text == p1:
            add(deck1_id, deck1_name, deck2_id, deck2_name, w=1)
            add(deck2_id, deck2_name, deck1_id, deck1_name, l=1)
            comparable_matches += 1
        elif winner_text == p2:
            add(deck1_id, deck1_name, deck2_id, deck2_name, l=1)
            add(deck2_id, deck2_name, deck1_id, deck1_name, w=1)
            comparable_matches += 1
        else:
            diagnostics["unresolved_result"] += 1

    rows: list[dict[str, Any]] = []
    for (deck_a_id, deck_a, deck_b_id, deck_b), (wins, losses, ties) in sorted(counts.items()):
        n = wins + losses + ties
        decisive = wins + losses
        wr = (100.0 * wins / decisive) if decisive > 0 else np.nan
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
                "WR_dir": wr,
            }
        )

    matchups = pd.DataFrame(rows, columns=list(MATCHUP_COLUMNS))
    for column in ("W", "L", "T", "N"):
        if column in matchups.columns:
            matchups[column] = matchups[column].astype("Int64")
    if "WR_dir" in matchups.columns:
        matchups["WR_dir"] = pd.to_numeric(matchups["WR_dir"], errors="coerce")

    _assert_directional_invariants(matchups)
    return MatchAggregationResult(
        matchups=matchups,
        comparable_matches=comparable_matches,
        pairing_exclusion_counts=diagnostics,
    )


def _assert_directional_invariants(matchups: pd.DataFrame) -> None:
    if matchups.empty:
        return
    lookup = matchups.set_index(["Deck A ID", "Deck B ID"])
    for (a_id, b_id), row in lookup.iterrows():
        if a_id == b_id:
            raise AssertionError("matchup aggregation emitted a mirror row")
        if (b_id, a_id) not in lookup.index:
            raise AssertionError(f"missing reverse direction for {a_id} vs {b_id}")
        reverse = lookup.loc[(b_id, a_id)]
        if int(row["W"]) != int(reverse["L"]):
            raise AssertionError(f"W/L symmetry failed for {a_id} vs {b_id}")
        if int(row["L"]) != int(reverse["W"]):
            raise AssertionError(f"L/W symmetry failed for {a_id} vs {b_id}")
        if int(row["T"]) != int(reverse["T"]):
            raise AssertionError(f"tie symmetry failed for {a_id} vs {b_id}")
        if int(row["N"]) != int(row["W"]) + int(row["L"]) + int(row["T"]):
            raise AssertionError(f"N formula failed for {a_id} vs {b_id}")


__all__ = [
    "AggregationConflictError",
    "META_COLUMNS",
    "MatchAggregationResult",
    "MetaAggregationResult",
    "PAIRING_DIAGNOSTIC_KEYS",
    "aggregate_matchups",
    "aggregate_meta",
]
