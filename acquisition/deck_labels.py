"""Run-scoped display labels; canonical IDs and source observations are unchanged."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from sources.limitless.tournament_api.release_catalog import parse_utc_datetime

LABEL_POLICY_VERSION = "latest_tournament_utc_lexicographic_v1"


class DeckLabelResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class DeckLabelResolution:
    labels: dict[str, str]
    diagnostics: dict[str, Any]


def _text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value).strip() or None


def _tournament_dates(tournaments: pd.DataFrame | None) -> dict[str, Any]:
    if tournaments is None:
        return {}
    required = {"tournament_id", "date"}
    if not required.issubset(tournaments.columns):
        raise KeyError(f"tournaments missing columns: {sorted(required - set(tournaments.columns))}")
    dates: dict[str, Any] = {}
    observations = tournaments.assign(_tid=tournaments["tournament_id"].map(_text))
    for tid, group in observations.groupby("_tid", sort=True):
        parsed = set()
        for value in group["date"]:
            try:
                date = parse_utc_datetime(value, field_name="tournament.date")
                if pd.isna(date):
                    raise ValueError("missing date")
                parsed.add(date)
            except (TypeError, ValueError, OverflowError):
                parsed.add(None)
        # An ambiguous or incomplete relation cannot establish a latest date.
        dates[str(tid).strip()] = next(iter(parsed)) if len(parsed) == 1 else None
    return dates


def resolve_deck_labels(
    participants: pd.DataFrame,
    tournaments: pd.DataFrame | None = None,
) -> DeckLabelResolution:
    """Use only observations supplied for this run.

    Names use the existing stripped-text semantics. Ties compare Python strings
    by Unicode code points (case-sensitive, no locale/casefold/normalization).
    Tournament dates select evidence; they do not establish source rename dates.
    Missing dates are fatal only for named observations of a drifting ID.
    """
    required = {"deck_id", "deck_name"}
    if not required.issubset(participants.columns):
        raise KeyError(f"participants missing columns: {sorted(required - set(participants.columns))}")
    observed = participants.copy()
    observed["_id"] = observed["deck_id"].map(_text)
    observed["_name"] = observed["deck_name"].map(_text)
    observed = observed[observed["_id"].notna()]
    dates = None
    labels: dict[str, str] = {}
    drift = []
    fallbacks = []
    for deck_id, group in observed.groupby("_id", sort=True):
        named = group[group["_name"].notna()].copy()
        names = sorted(set(named["_name"]))
        if not names:
            labels[deck_id] = deck_id
            fallbacks.append(deck_id)
            continue
        if len(names) == 1:
            labels[deck_id] = names[0]
            continue
        if "tournament_id" not in named.columns:
            raise DeckLabelResolutionError(f"deck label resolution requires tournament_id for {deck_id}")
        if dates is None:
            dates = _tournament_dates(tournaments)
        named["_tid"] = named["tournament_id"].map(_text)
        named["_date"] = named["_tid"].map(dates)
        missing = named[named["_date"].isna()]
        if not missing.empty:
            tids = sorted({_text(tid) or "<missing>" for tid in missing["_tid"]})
            raise DeckLabelResolutionError(
                f"deck label resolution for {deck_id}: missing, invalid or conflicting "
                f"tournament date UTC for {tids}"
            )
        latest = named["_date"].max()
        contenders = sorted(set(named.loc[named["_date"] == latest, "_name"]))
        selected = min(contenders)
        labels[deck_id] = selected
        evidence = []
        for name, rows in named.groupby("_name", sort=True):
            evidence.append({
                "label": name,
                "count": int(len(rows)),
                "min_date_utc": rows["_date"].min().isoformat().replace("+00:00", "Z"),
                "max_date_utc": rows["_date"].max().isoformat().replace("+00:00", "Z"),
                "tournament_ids": sorted(set(rows["_tid"])),
                "latest_tournament_ids": sorted(set(rows.loc[rows["_date"] == rows["_date"].max(), "_tid"])),
            })
        drift.append({
            "deck_id": deck_id,
            "observed_labels": evidence,
            "unnamed_count": int(len(group) - len(named)),
            "selected_label": selected,
            "policy_version": LABEL_POLICY_VERSION,
            "fallback_used": False,
            "tie_break_used": len(contenders) > 1,
            "tie_break_labels": contenders if len(contenders) > 1 else [],
        })
    return DeckLabelResolution(labels, {
        "policy_version": LABEL_POLICY_VERSION,
        "lexicographic_comparison": "unicode_code_points_case_sensitive",
        "fallback_ids": fallbacks,
        "drift": drift,
    })
