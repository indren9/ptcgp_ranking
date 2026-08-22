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


def _pairing_key(
    *,
    tournament_id: str,
    phase: int,
    round_number: int,
    table: int | None,
    match: str | None,
    player1: str,
    player2: str | None,
) -> str:
    if match is not None:
        locator = f"match:{match}"
    elif table is not None:
        locator = f"table:{table}"
    else:
        locator = f"players:{player1}>{player2 or ''}"
    return f"{tournament_id}|phase:{phase}|round:{round_number}|{locator}"


def normalize_pairings(tournament_id: str, pairings: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    tid = _clean_required_str(tournament_id, field_name="tournament_id")
    rows: list[dict[str, Any]] = []
    for item in pairings:
        if not isinstance(item, Mapping):
            raise TypeError("pairing rows must be objects")
        phase = _required_nonnegative_int(item.get("phase"), field_name="phase")
        round_number = _required_nonnegative_int(item.get("round"), field_name="round")
        table = _optional_int(item.get("table"), field_name="table")
        match = _clean_optional_str(item.get("match"))
        player1 = _clean_required_str(item.get("player1"), field_name="player1")
        player2 = _clean_optional_str(item.get("player2"))
        row = {
            "tournament_id": tid,
            "phase": phase,
            "round": round_number,
            "table": table,
            "match": match,
            "player1": player1,
            "player2": player2,
            "winner": _normalize_winner(item.get("winner")),
            "pairing_key": _pairing_key(
                tournament_id=tid,
                phase=phase,
                round_number=round_number,
                table=table,
                match=match,
                player1=player1,
                player2=player2,
            ),
        }
        rows.append(row)
    rows = _dedupe_rows(rows, key_field="pairing_key", context=f"pairing in tournament {tid}")
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
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    details_id = _clean_required_str(details.get("id"), field_name="details.id")
    if details_id != _clean_required_str(tournament_id, field_name="tournament_id"):
        raise ValueError("tournament_id does not match details.id")
    tournaments = normalize_tournaments(((details, raw_snapshot_id),))
    participants = normalize_participants(tournament_id, standings)
    pairing_df = normalize_pairings(tournament_id, pairings)
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
