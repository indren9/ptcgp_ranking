from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from acquisition.contracts import AcquisitionFrames


@dataclass(frozen=True)
class ProductionAcquisitionFrames:
    """Tournament-API frames adapted to the legacy name-keyed core boundary.

    ``Deck`` / ``Deck A`` / ``Deck B`` intentionally carry canonical deck IDs.
    Human-readable names live only in ``deck_identity_map`` and diagnostics.
    """

    top_meta_decklist: pd.DataFrame
    matchup_raw: pd.DataFrame
    dense_score: pd.DataFrame
    deck_identity_map: pd.DataFrame


def _assert_public_frame(df: pd.DataFrame, *, label: str) -> None:
    bad = [column for column in df.columns if "player_id" in str(column).strip().lower()]
    if bad:
        raise ValueError(f"{label} must not expose player_id columns: {bad}")


def _identity_map(frames: AcquisitionFrames) -> pd.DataFrame:
    parts = [
        frames.top_meta_decklist[["Deck ID", "Deck"]],
        frames.matchup_raw[["Deck A ID", "Deck A"]].rename(
            columns={"Deck A ID": "Deck ID", "Deck A": "Deck"}
        ),
        frames.matchup_raw[["Deck B ID", "Deck B"]].rename(
            columns={"Deck B ID": "Deck ID", "Deck B": "Deck"}
        ),
        frames.dense_score[["Deck A ID", "Deck A"]].rename(
            columns={"Deck A ID": "Deck ID", "Deck A": "Deck"}
        ),
        frames.dense_score[["Deck B ID", "Deck B"]].rename(
            columns={"Deck B ID": "Deck ID", "Deck B": "Deck"}
        ),
    ]
    mapping = pd.concat(parts, ignore_index=True).drop_duplicates()
    mapping["Deck ID"] = mapping["Deck ID"].astype(str).str.strip()
    mapping["Deck"] = mapping["Deck"].astype(str).str.strip()
    if (mapping["Deck ID"] == "").any() or (mapping["Deck"] == "").any():
        raise ValueError("deck identity mapping must contain non-empty IDs and display names")

    ambiguous = mapping.groupby("Deck ID", sort=False)["Deck"].nunique(dropna=False)
    ambiguous = ambiguous[ambiguous > 1]
    if not ambiguous.empty:
        raise ValueError(f"deck ID maps to multiple display names: {ambiguous.index[0]}")

    return mapping.sort_values(["Deck ID", "Deck"], kind="mergesort").reset_index(drop=True)


def _technical_top_meta(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["Rank", "Deck ID", "Count", "Share_%"]].copy()
    out["Deck"] = out["Deck ID"].astype(str).str.strip()
    out = out[["Rank", "Deck", "Count", "Share_%"]]
    return out.sort_values(["Rank", "Deck"], kind="mergesort").reset_index(drop=True)


def _technical_matchups(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["Deck A ID", "Deck B ID", "W", "L", "T", "N", "WR_dir"]].copy()
    out["Deck A"] = out["Deck A ID"].astype(str).str.strip()
    out["Deck B"] = out["Deck B ID"].astype(str).str.strip()
    out = out[["Deck A", "Deck B", "W", "L", "T", "N", "WR_dir"]]
    return out.sort_values(["Deck A", "Deck B"], kind="mergesort").reset_index(drop=True)


def bridge_tournament_api_frames(frames: AcquisitionFrames) -> ProductionAcquisitionFrames:
    """Adapt canonical Tournament API contracts to collision-free core keys.

    The adapter never groups by display name and never applies legacy aliases.
    Dense input remains dense; no matches or counts are synthesized here.
    """

    for label, df in (
        ("top_meta_decklist", frames.top_meta_decklist),
        ("matchup_raw", frames.matchup_raw),
        ("dense_score", frames.dense_score),
    ):
        _assert_public_frame(df, label=label)

    mapping = _identity_map(frames)
    top = _technical_top_meta(frames.top_meta_decklist)
    matchup = _technical_matchups(frames.matchup_raw)
    dense = _technical_matchups(frames.dense_score)

    axis = set(top["Deck"].astype(str))
    dense_ids = set(dense["Deck A"].astype(str)) | set(dense["Deck B"].astype(str))
    if dense_ids != axis:
        raise ValueError("dense_score technical axis must match top_meta deck IDs")

    for label, df in (
        ("production top_meta_decklist", top),
        ("production matchup_raw", matchup),
        ("production dense_score", dense),
        ("deck_identity_map", mapping),
    ):
        _assert_public_frame(df, label=label)

    return ProductionAcquisitionFrames(
        top_meta_decklist=top,
        matchup_raw=matchup,
        dense_score=dense,
        deck_identity_map=mapping,
    )


def identity_mapping_diagnostics(mapping: pd.DataFrame) -> Mapping[str, Any]:
    """Return deterministic public diagnostics for ID -> display reconstruction."""

    ordered = mapping.sort_values(["Deck ID", "Deck"], kind="mergesort").reset_index(drop=True)
    duplicate_names = (
        ordered.groupby("Deck", sort=True)["Deck ID"]
        .apply(lambda values: sorted(set(map(str, values))))
        .to_dict()
    )
    duplicate_names = {name: ids for name, ids in duplicate_names.items() if len(ids) > 1}
    return {
        "count": int(len(ordered)),
        "mapping": [
            {"deck_id": str(row["Deck ID"]), "deck_name": str(row["Deck"])}
            for _, row in ordered.iterrows()
        ],
        "duplicate_display_names": duplicate_names,
    }


__all__ = [
    "ProductionAcquisitionFrames",
    "bridge_tournament_api_frames",
    "identity_mapping_diagnostics",
]
