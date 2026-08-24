from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Iterable, Mapping

import pandas as pd

from sources.limitless.tournament_api.release_catalog import parse_utc_datetime

TOURNAMENT_COLUMNS = (
    "tournament_id",
    "date",
    "game",
    "format",
    "name",
    "players",
    "organizer_id",
    "organizer_name",
    "platform",
    "is_public",
    "decklists",
    "is_online",
    "special_rules",
    "banned_cards",
    "phases",
    "raw_snapshot_id",
)
PARTICIPANT_COLUMNS = (
    "tournament_id",
    "player_id",
    "placing",
    "deck_id",
    "deck_name",
    "record_wins",
    "record_losses",
    "record_ties",
    "drop",
    "decklist_available",
)
PAIRING_COLUMNS = (
    "tournament_id",
    "phase",
    "round",
    "table",
    "match",
    "player1",
    "player2",
    "winner",
    "pairing_key",
)


class NormalizationConflictError(ValueError):
    pass


def _clean_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_required_str(value: Any, *, field_name: str) -> str:
    text = _clean_optional_str(value)
    if text is None:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer or null")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer or null") from exc
    return number


def _optional_nonnegative_int(value: Any, *, field_name: str) -> int | None:
    number = _optional_int(value, field_name=field_name)
    if number is not None and number < 0:
        raise ValueError(f"{field_name} must be a non-negative integer or null")
    return number


def _required_nonnegative_int(value: Any, *, field_name: str) -> int:
    number = _optional_nonnegative_int(value, field_name=field_name)
    if number is None:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return number


def _required_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _canonical_obj(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_obj(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return tuple(_canonical_obj(item) for item in value)
    return value


def _signature(row: Mapping[str, Any]) -> str:
    def default(value: Any):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, tuple):
            return list(value)
        raise TypeError(type(value).__name__)

    return json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default)


def _dedupe_rows(rows: list[dict[str, Any]], *, key_field: str, context: str) -> list[dict[str, Any]]:
    seen: dict[str, tuple[str, dict[str, Any]]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row[key_field])
        sig = _signature(row)
        if key not in seen:
            seen[key] = (sig, row)
            out.append(row)
            continue
        previous_sig, _ = seen[key]
        if previous_sig != sig:
            raise NormalizationConflictError(f"conflicting duplicate {context}: {key}")
    return out


def _frame(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(columns))


def normalize_tournament(details: Mapping[str, Any], *, raw_snapshot_id: str) -> dict[str, Any]:
    if not isinstance(details, Mapping):
        raise TypeError("details must be a mapping")
    organizer = details.get("organizer") or {}
    if not isinstance(organizer, Mapping):
        raise ValueError("organizer must be an object or null")
    raw_format = details.get("format")
    fmt = None if raw_format is None else _clean_optional_str(raw_format)
    row = {
        "tournament_id": _clean_required_str(details.get("id"), field_name="id"),
        "date": parse_utc_datetime(details.get("date"), field_name="date"),
        "game": _clean_required_str(details.get("game"), field_name="game").upper(),
        "format": None if fmt is None else fmt.upper(),
        "name": _clean_required_str(details.get("name"), field_name="name"),
        "players": _required_nonnegative_int(details.get("players"), field_name="players"),
        "organizer_id": _optional_int(organizer.get("id"), field_name="organizer.id"),
        "organizer_name": _clean_optional_str(organizer.get("name")),
        "platform": _clean_optional_str(details.get("platform")),
        "is_public": _required_bool(details.get("isPublic"), field_name="isPublic"),
        "decklists": _required_bool(details.get("decklists"), field_name="decklists"),
        "is_online": _required_bool(details.get("isOnline"), field_name="isOnline"),
        "special_rules": tuple(_canonical_obj(details.get("specialRules") or [])),
        "banned_cards": tuple(_canonical_obj(details.get("bannedCards") or [])),
        "phases": tuple(_canonical_obj(details.get("phases") or [])),
        "raw_snapshot_id": _clean_required_str(raw_snapshot_id, field_name="raw_snapshot_id"),
    }
    return row


def normalize_tournaments(
    payloads: Iterable[tuple[Mapping[str, Any], str]],
) -> pd.DataFrame:
    rows = [normalize_tournament(details, raw_snapshot_id=snapshot_id) for details, snapshot_id in payloads]
    rows = _dedupe_rows(rows, key_field="tournament_id", context="tournament")
    rows.sort(key=lambda row: (row["date"], row["tournament_id"]))
    return _frame(rows, TOURNAMENT_COLUMNS)


def normalize_participants(tournament_id: str, standings: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    tid = _clean_required_str(tournament_id, field_name="tournament_id")
    rows: list[dict[str, Any]] = []
    for item in standings:
        if not isinstance(item, Mapping):
            raise TypeError("standings rows must be objects")
        record = item.get("record") or {}
        if not isinstance(record, Mapping):
            raise ValueError("record must be an object")
        deck = item.get("deck") or {}
        if not isinstance(deck, Mapping):
            raise ValueError("deck must be an object or null")
        rows.append(
            {
                "tournament_id": tid,
                "player_id": _clean_required_str(item.get("player"), field_name="player"),
                "placing": _optional_nonnegative_int(item.get("placing"), field_name="placing"),
                "deck_id": _clean_optional_str(deck.get("id")),
                "deck_name": _clean_optional_str(deck.get("name")),
                "record_wins": _required_nonnegative_int(record.get("wins", 0), field_name="record.wins"),
                "record_losses": _required_nonnegative_int(record.get("losses", 0), field_name="record.losses"),
                "record_ties": _required_nonnegative_int(record.get("ties", 0), field_name="record.ties"),
                "drop": _optional_int(item.get("drop"), field_name="drop"),
                "decklist_available": item.get("decklist") is not None,
            }
        )
    rows = _dedupe_rows(rows, key_field="player_id", context=f"player in tournament {tid}")
    rows.sort(
        key=lambda row: (
            row["placing"] is None,
            row["placing"] if row["placing"] is not None else 0,
            row["player_id"],
        )
    )
    return _frame(rows, PARTICIPANT_COLUMNS)


def _normalize_winner(value: Any) -> str | int | None:
    if value is None or value == "":
        return None
    if value in (0, -1, "0", "-1"):
        return int(value)
    return _clean_required_str(value, field_name="winner")


def _pairing_base_key(
    *,
    tournament_id: str,
    phase: int,
    round_number: int,
    player1: str,
    player2: str | None,
) -> str:
    if player2 is None:
        participant_identity = f"bye:{json.dumps(player1, ensure_ascii=False)}"
    else:
        pair = sorted((player1, player2))
        participant_identity = (
            "players:"
            + json.dumps(pair, ensure_ascii=False, separators=(",", ":"))
        )
    return (
        f"{tournament_id}|phase:{phase}|round:{round_number}|"
        f"{participant_identity}"
    )


def _pairing_analytic_signature(row: Mapping[str, Any]) -> str:
    player1 = row.get("player1")
    player2 = row.get("player2")
    if player2 is None:
        classification = "bye"
        participants = [player1]
    else:
        classification = "normal"
        participants = sorted((player1, player2))
    return _signature(
        {
            "tournament_id": row.get("tournament_id"),
            "phase": row.get("phase"),
            "round": row.get("round"),
            "classification": classification,
            "participants": participants,
            "winner": row.get("winner"),
        }
    )


def _increment_diagnostic(
    diagnostics: dict[str, int] | None,
    key: str,
    amount: int = 1,
) -> None:
    if diagnostics is not None and amount:
        diagnostics[key] = int(diagnostics.get(key, 0)) + int(amount)


def _raise_pairing_conflict(
    *,
    context: str,
    base_key: str,
    diagnostics: dict[str, int] | None,
) -> None:
    _increment_diagnostic(diagnostics, "pairing_unresolved_conflict_count")
    raise NormalizationConflictError(f"conflicting duplicate {context}: {base_key}")


def _occurrence_key(base_key: str, *, kind: str, value: Any = None) -> str:
    if kind == "single":
        return f"{base_key}|occurrence:single"
    if kind == "match":
        return f"{base_key}|match:{json.dumps(value, ensure_ascii=False)}"
    if kind == "table":
        return f"{base_key}|table:{json.dumps(value, ensure_ascii=False)}"
    raise ValueError(f"unsupported occurrence key kind: {kind}")


def _deterministic_representative(
    rows: list[dict[str, Any]],
    *,
    match: str | None = None,
    table: int | None = None,
) -> dict[str, Any]:
    candidates = rows
    if match is not None:
        candidates = [row for row in rows if row.get("match") == match]
    elif table is not None:
        candidates = [
            row
            for row in rows
            if row.get("match") is None and row.get("table") == table
        ]
    if not candidates:
        candidates = rows
    return min(candidates, key=_signature)


def _resolve_pairing_base_group(
    base_key: str,
    rows: list[dict[str, Any]],
    *,
    context: str,
    diagnostics: dict[str, int] | None,
) -> list[dict[str, Any]]:
    if len(rows) > 1:
        _increment_diagnostic(diagnostics, "pairing_base_collision_count")

    by_semantic: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_semantic.setdefault(_pairing_analytic_signature(row), []).append(row)

    # One analytical occurrence: locator disagreement is only context noise.
    if len(by_semantic) == 1:
        representative = _deterministic_representative(rows)
        representative = dict(representative)
        representative["pairing_key"] = _occurrence_key(base_key, kind="single")
        _increment_diagnostic(
            diagnostics,
            "pairing_deduplicated_count",
            len(rows) - 1,
        )
        return [representative]

    with_match = [row for row in rows if row.get("match") is not None]
    without_match = [row for row in rows if row.get("match") is None]

    if with_match:
        match_semantics: dict[str, set[str]] = {}
        semantic_matches: dict[str, set[str]] = {}
        for row in with_match:
            match = str(row["match"])
            semantic = _pairing_analytic_signature(row)
            match_semantics.setdefault(match, set()).add(semantic)
            semantic_matches.setdefault(semantic, set()).add(match)

        if any(len(values) > 1 for values in match_semantics.values()):
            _raise_pairing_conflict(
                context=context,
                base_key=base_key,
                diagnostics=diagnostics,
            )

        # In a mixed group, a match-missing row can only be absorbed into a
        # uniquely evidenced analytical occurrence. A new semantic outcome
        # without a match label is ambiguous and must fail fast.
        for row in without_match:
            semantic = _pairing_analytic_signature(row)
            if semantic not in semantic_matches:
                _raise_pairing_conflict(
                    context=context,
                    base_key=base_key,
                    diagnostics=diagnostics,
                )

        if set(by_semantic) != set(semantic_matches):
            _raise_pairing_conflict(
                context=context,
                base_key=base_key,
                diagnostics=diagnostics,
            )

        resolved: list[dict[str, Any]] = []
        for semantic in sorted(by_semantic):
            match_label = min(semantic_matches[semantic])
            representative = _deterministic_representative(
                by_semantic[semantic],
                match=match_label,
            )
            representative = dict(representative)
            representative["pairing_key"] = _occurrence_key(
                base_key,
                kind="match",
                value=match_label,
            )
            resolved.append(representative)

        _increment_diagnostic(
            diagnostics,
            "pairing_rematch_occurrence_count",
            len(resolved) - 1,
        )
        _increment_diagnostic(
            diagnostics,
            "pairing_match_discriminator_count",
            len(resolved),
        )
        _increment_diagnostic(
            diagnostics,
            "pairing_deduplicated_count",
            len(rows) - len(resolved),
        )
        return resolved

    # No match labels exist in this base group. Table is allowed only as the
    # local fallback discriminator for analytically incompatible occurrences.
    with_table = [row for row in rows if row.get("table") is not None]
    without_table = [row for row in rows if row.get("table") is None]
    if not with_table:
        _raise_pairing_conflict(
            context=context,
            base_key=base_key,
            diagnostics=diagnostics,
        )

    table_semantics: dict[int, set[str]] = {}
    semantic_tables: dict[str, set[int]] = {}
    for row in with_table:
        table = int(row["table"])
        semantic = _pairing_analytic_signature(row)
        table_semantics.setdefault(table, set()).add(semantic)
        semantic_tables.setdefault(semantic, set()).add(table)

    if any(len(values) > 1 for values in table_semantics.values()):
        _raise_pairing_conflict(
            context=context,
            base_key=base_key,
            diagnostics=diagnostics,
        )

    for row in without_table:
        semantic = _pairing_analytic_signature(row)
        if semantic not in semantic_tables:
            _raise_pairing_conflict(
                context=context,
                base_key=base_key,
                diagnostics=diagnostics,
            )

    if set(by_semantic) != set(semantic_tables):
        _raise_pairing_conflict(
            context=context,
            base_key=base_key,
            diagnostics=diagnostics,
        )

    resolved = []
    for semantic in sorted(by_semantic):
        table = min(semantic_tables[semantic])
        representative = _deterministic_representative(
            by_semantic[semantic],
            table=table,
        )
        representative = dict(representative)
        representative["pairing_key"] = _occurrence_key(
            base_key,
            kind="table",
            value=table,
        )
        resolved.append(representative)

    _increment_diagnostic(
        diagnostics,
        "pairing_rematch_occurrence_count",
        len(resolved) - 1,
    )
    _increment_diagnostic(
        diagnostics,
        "pairing_table_fallback_count",
        len(resolved),
    )
    _increment_diagnostic(
        diagnostics,
        "pairing_deduplicated_count",
        len(rows) - len(resolved),
    )
    return resolved


def _resolve_pairing_occurrences(
    rows: list[dict[str, Any]],
    *,
    context: str,
    diagnostics: dict[str, int] | None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["pairing_key"]), []).append(row)

    resolved: list[dict[str, Any]] = []
    for base_key in sorted(grouped):
        resolved.extend(
            _resolve_pairing_base_group(
                base_key,
                grouped[base_key],
                context=context,
                diagnostics=diagnostics,
            )
        )
    return resolved

def normalize_pairings(
    tournament_id: str,
    pairings: Iterable[Mapping[str, Any]],
    *,
    participant_ids: Iterable[str] | None = None,
    diagnostics: dict[str, int] | None = None,
) -> pd.DataFrame:
    tid = _clean_required_str(tournament_id, field_name="tournament_id")
    known_participants = (
        {_clean_required_str(value, field_name="participant_id") for value in participant_ids}
        if participant_ids is not None
        else None
    )
    rows: list[dict[str, Any]] = []
    for item in pairings:
        if not isinstance(item, Mapping):
            raise TypeError("pairing rows must be objects")
        phase = _required_nonnegative_int(item.get("phase"), field_name="phase")
        round_number = _required_nonnegative_int(item.get("round"), field_name="round")
        table = _optional_int(item.get("table"), field_name="table")
        match = _clean_optional_str(item.get("match"))
        player1 = _clean_optional_str(item.get("player1"))
        player2 = _clean_optional_str(item.get("player2"))
        winner = _normalize_winner(item.get("winner"))

        if player1 is None:
            if player2 is None:
                if diagnostics is not None:
                    diagnostics["excluded_pairing_no_players_count"] = (
                        int(diagnostics.get("excluded_pairing_no_players_count", 0)) + 1
                    )
                continue
            if winner != player2:
                raise ValueError(
                    "player1 is missing but player2 cannot be canonicalized: "
                    "winner must equal player2"
                )
            if known_participants is not None and player2 not in known_participants:
                raise ValueError(
                    "player1 is missing but player2 cannot be canonicalized: "
                    "player2 is not a normalized participant"
                )
            player1 = player2
            player2 = None
            if diagnostics is not None:
                diagnostics["canonicalized_player2_bye_count"] = (
                    int(diagnostics.get("canonicalized_player2_bye_count", 0)) + 1
                )

        row = {
            "tournament_id": tid,
            "phase": phase,
            "round": round_number,
            "table": table,
            "match": match,
            "player1": player1,
            "player2": player2,
            "winner": winner,
            "pairing_key": _pairing_base_key(
                tournament_id=tid,
                phase=phase,
                round_number=round_number,
                player1=player1,
                player2=player2,
            ),
        }
        rows.append(row)
    rows = _resolve_pairing_occurrences(
        rows,
        context=f"pairing in tournament {tid}",
        diagnostics=diagnostics,
    )
    rows.sort(
        key=lambda row: (
            row["phase"],
            row["round"],
            row["table"] is None,
            row["table"] if row["table"] is not None else 0,
            row["match"] or "",
            row["pairing_key"],
        )
    )
    return _frame(rows, PAIRING_COLUMNS)


def normalize_snapshot(
    *,
    tournament_id: str,
    raw_snapshot_id: str,
    details: Mapping[str, Any],
    standings: Iterable[Mapping[str, Any]],
    pairings: Iterable[Mapping[str, Any]],
    diagnostics: dict[str, int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    details_id = _clean_required_str(details.get("id"), field_name="details.id")
    if details_id != _clean_required_str(tournament_id, field_name="tournament_id"):
        raise ValueError("tournament_id does not match details.id")
    tournaments = normalize_tournaments(((details, raw_snapshot_id),))
    participants = normalize_participants(tournament_id, standings)
    normalization_diagnostics = diagnostics
    participant_ids = participants["player_id"].astype(str).tolist()
    pairing_df = normalize_pairings(
        tournament_id,
        pairings,
        participant_ids=participant_ids,
        diagnostics=normalization_diagnostics,
    )
    return tournaments, participants, pairing_df


__all__ = [
    "NormalizationConflictError",
    "PAIRING_COLUMNS",
    "PARTICIPANT_COLUMNS",
    "TOURNAMENT_COLUMNS",
    "normalize_pairings",
    "normalize_participants",
    "normalize_snapshot",
    "normalize_tournament",
    "normalize_tournaments",
]
