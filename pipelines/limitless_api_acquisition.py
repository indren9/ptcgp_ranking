from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from acquisition.aggregation import (
    MatchAggregationResult,
    MetaAggregationResult,
    aggregate_matchups,
    aggregate_meta,
)
from acquisition.contracts import (
    AcquisitionContracts,
    AcquisitionFrames,
    RawPayloadRef,
    adapt_matchup_raw,
    adapt_top_meta_decklist,
    build_acquisition_contracts,
    hash_dataframe,
    materialize_dense_score,
)
from acquisition.manifest import (
    AcquisitionManifest,
    AggregationSummary,
    NormalizedSummary,
    RawSummary,
    validate_manifest_dict,
)
from acquisition.scope import EligibilityPolicy, ScopePolicy
from acquisition.selection import TournamentSelection, select_tournaments
from domain.releases import ReleaseCatalog, require_utc
from sources.limitless.tournament_api.client import LimitlessTournamentApiClient
from sources.limitless.tournament_api.normalize import (
    PAIRING_COLUMNS,
    PARTICIPANT_COLUMNS,
    TOURNAMENT_COLUMNS,
    normalize_snapshot,
)
from sources.limitless.tournament_api.raw_store import (
    ImmutableRawStore,
    PAYLOAD_TYPES,
    TournamentRawSnapshot,
    sha256_json,
)
from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot,
    parse_utc_datetime,
    resolve_release,
    scope_for_release,
)
from storage.acquisition import FileJsonCache

DEFAULT_RELEASE_CATALOG = Path("data/reference/pocket_releases.json")
SOURCE_NAME = "Limitless Tournament API"
SCHEMA_VERSION = "1"


class AcquisitionPipelineError(RuntimeError):
    pass


class DiscoveryWindowIncompleteError(AcquisitionPipelineError):
    pass


@dataclass(frozen=True)
class AcquisitionRunResult:
    contracts: AcquisitionContracts
    manifest: AcquisitionManifest
    diagnostics: Mapping[str, Any]
    frames: AcquisitionFrames


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return path


def _git_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        value = completed.stdout.strip()
        return value or "UNKNOWN"
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def _coerce_catalog(value: ReleaseCatalog | str | Path | None) -> ReleaseCatalog:
    if isinstance(value, ReleaseCatalog):
        return value
    return load_release_catalog_snapshot(value or DEFAULT_RELEASE_CATALOG)


def _catalog_payload(catalog: ReleaseCatalog) -> dict[str, Any]:
    return {
        "catalog_version": catalog.catalog_version,
        "source": catalog.source,
        "releases": [
            {
                "code": release.code,
                "name": release.name,
                "release_datetime": release.release_datetime.isoformat().replace("+00:00", "Z"),
                "next_release_datetime": (
                    None
                    if release.next_release_datetime is None
                    else release.next_release_datetime.isoformat().replace("+00:00", "Z")
                ),
                "is_current": release.is_current,
                "source": release.source,
                "catalog_version": release.catalog_version,
            }
            for release in catalog.releases
        ],
    }


def _canonical_discovery_records(records: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    duplicate_count = 0
    for raw in records:
        if not isinstance(raw, Mapping):
            raise TypeError("tournament discovery rows must be JSON objects")
        row = dict(raw)
        tid = str(row.get("id") or "").strip()
        if not tid:
            raise ValueError("tournament discovery row is missing id")
        signature = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        if tid in seen:
            if seen[tid] != signature:
                raise ValueError(f"conflicting duplicate tournament discovery row: {tid}")
            duplicate_count += 1
            continue
        seen[tid] = signature
        out.append(row)
    out.sort(key=lambda row: (str(row.get("date") or ""), str(row["id"])))
    return out, duplicate_count


def _discovery_candidates(
    records: list[dict[str, Any]],
    *,
    scope: ScopePolicy,
    page_size: int,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    valid_dates: list[datetime] = []
    candidate_ids: list[str] = []
    invalid = 0
    wrong_game = 0
    for row in records:
        try:
            date = parse_utc_datetime(row.get("date"), field_name="tournament.date")
            game = str(row.get("game") or "").strip().upper()
            tid = str(row.get("id") or "").strip()
            if not tid:
                raise ValueError("missing id")
        except (TypeError, ValueError):
            invalid += 1
            continue
        valid_dates.append(date)
        if game != scope.game:
            wrong_game += 1
            continue
        if scope.start_datetime <= date < scope.end_datetime:
            candidate_ids.append(tid)

    if not valid_dates:
        raise DiscoveryWindowIncompleteError("tournament discovery returned no valid dates")

    oldest = min(valid_dates)
    exhausted = len(records) < page_size or (len(records) % page_size) != 0
    if oldest > scope.start_datetime and not exhausted:
        raise DiscoveryWindowIncompleteError(
            "discovery pagination did not reach the scope start; increase discovery_max_pages"
        )

    diagnostics = {
        "discovery_rows": len(records),
        "discovery_oldest": oldest.isoformat().replace("+00:00", "Z"),
        "discovery_invalid_rows": invalid,
        "discovery_wrong_game_rows": wrong_game,
        "candidate_count": len(set(candidate_ids)),
    }
    return tuple(sorted(set(candidate_ids))), diagnostics


def _selection_record(details: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": details.get("id"),
        "game": details.get("game"),
        "format": details.get("format"),
        "date": details.get("date"),
        "is_public": details.get("isPublic"),
        "decklists": details.get("decklists"),
    }


def _snapshot_refs_from_loaded(
    *,
    tournament_id: str,
    snapshot_id: str,
    loaded: Mapping[str, Any],
) -> tuple[RawPayloadRef, ...]:
    metadata = dict(loaded["metadata"])
    fetched_at = parse_utc_datetime(metadata["fetched_at"], field_name="fetched_at")
    payload_hashes = dict(metadata.get("payload_hashes") or {})
    return tuple(
        RawPayloadRef(
            payload_type=name,
            tournament_id=tournament_id,
            snapshot_id=snapshot_id,
            sha256=str(payload_hashes[name]),
            fetched_at=fetched_at,
            relative_path=(
                Path("tournaments") / tournament_id / "snapshots" / snapshot_id / f"{name}.json"
            ).as_posix(),
        )
        for name in PAYLOAD_TYPES
    )


def _acquire_selected_snapshot(
    *,
    tournament_id: str,
    details: Mapping[str, Any],
    raw_store: ImmutableRawStore,
    client: LimitlessTournamentApiClient,
    fetched_at: datetime,
    reuse_latest_raw: bool,
) -> TournamentRawSnapshot:
    if reuse_latest_raw:
        latest_id = raw_store.latest_snapshot_id(tournament_id)
        if latest_id:
            loaded = raw_store.load_tournament_snapshot(tournament_id, latest_id, validate=True)
            if sha256_json(loaded["details"]) == sha256_json(dict(details)):
                return TournamentRawSnapshot(
                    tournament_id=tournament_id,
                    snapshot_id=latest_id,
                    refs=_snapshot_refs_from_loaded(
                        tournament_id=tournament_id,
                        snapshot_id=latest_id,
                        loaded=loaded,
                    ),
                )

    standings = client.get_tournament_standings(tournament_id, use_cache=True)
    pairings = client.get_tournament_pairings(tournament_id, use_cache=True)
    return raw_store.save_tournament_snapshot(
        tournament_id,
        details=details,
        standings=standings,
        pairings=pairings,
        fetched_at=fetched_at,
    )


def _load_raw_ref(raw_store: ImmutableRawStore, ref: RawPayloadRef) -> Any:
    path = raw_store.paths.root / ref.relative_path
    if not path.exists():
        raise FileNotFoundError(f"offline replay missing raw ref: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    actual = sha256_json(payload)
    if actual != ref.sha256:
        raise ValueError(f"offline replay raw hash mismatch: {ref.relative_path}")
    return payload


def _raw_ref_from_manifest(item: Mapping[str, Any]) -> RawPayloadRef:
    return RawPayloadRef(
        payload_type=item["payload_type"],
        tournament_id=item.get("tournament_id"),
        snapshot_id=item["snapshot_id"],
        sha256=item["sha256"],
        fetched_at=parse_utc_datetime(item["fetched_at"], field_name="raw_ref.fetched_at"),
        relative_path=item["relative_path"],
    )


def _scope_from_manifest(payload: Mapping[str, Any]) -> ScopePolicy:
    item = payload["scope"]
    return ScopePolicy(
        policy_id=item["policy_id"],
        game=item["game"],
        format=item.get("format"),
        set_code=item["set_code"],
        set_name=item["set_name"],
        start_datetime=parse_utc_datetime(item["start"], field_name="scope.start"),
        end_datetime=parse_utc_datetime(item["end"], field_name="scope.end"),
        catalog_version=item["catalog_version"],
    )


def _selection_from_manifest(payload: Mapping[str, Any]) -> TournamentSelection:
    item = payload["selection"]
    return TournamentSelection(
        tournament_ids=tuple(item.get("tournament_ids") or ()),
        exclusion_counts=dict(item.get("exclusion_counts") or {}),
        failures=tuple(item.get("failures") or ()),
    )


def _concat_or_empty(frames: list[pd.DataFrame], columns: tuple[str, ...]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=list(columns))
    return pd.concat(frames, ignore_index=True).reindex(columns=list(columns))


def _normalize_selected(
    *,
    raw_store: ImmutableRawStore,
    selection: TournamentSelection,
    tournament_snapshot_ids: Mapping[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    tournament_frames: list[pd.DataFrame] = []
    participant_frames: list[pd.DataFrame] = []
    pairing_frames: list[pd.DataFrame] = []
    normalization_diagnostics = {
        "canonicalized_player2_bye_count": 0,
        "excluded_pairing_no_players_count": 0,
        "pairing_base_collision_count": 0,
        "pairing_rematch_occurrence_count": 0,
        "pairing_match_discriminator_count": 0,
        "pairing_table_fallback_count": 0,
        "pairing_deduplicated_count": 0,
        "pairing_unresolved_conflict_count": 0,
    }

    for tid in selection.tournament_ids:
        if tid not in tournament_snapshot_ids:
            raise FileNotFoundError(f"offline replay has no tournament snapshot ref for selected tournament: {tid}")
        snapshot_id = tournament_snapshot_ids[tid]
        loaded = raw_store.load_tournament_snapshot(tid, snapshot_id, validate=True)
        tournaments, participants, pairings = normalize_snapshot(
            tournament_id=tid,
            raw_snapshot_id=snapshot_id,
            details=loaded["details"],
            standings=loaded["standings"],
            pairings=loaded["pairings"],
            diagnostics=normalization_diagnostics,
        )
        tournament_frames.append(tournaments)
        participant_frames.append(participants)
        pairing_frames.append(pairings)

    tournaments = _concat_or_empty(tournament_frames, TOURNAMENT_COLUMNS)
    participants = _concat_or_empty(participant_frames, PARTICIPANT_COLUMNS)
    pairings = _concat_or_empty(pairing_frames, PAIRING_COLUMNS)

    tournament_ids = set(tournaments["tournament_id"].astype(str)) if not tournaments.empty else set()
    participant_fk_missing = (
        int((~participants["tournament_id"].astype(str).isin(tournament_ids)).sum())
        if not participants.empty
        else 0
    )
    pairing_fk_missing = (
        int((~pairings["tournament_id"].astype(str).isin(tournament_ids)).sum())
        if not pairings.empty
        else 0
    )
    if participant_fk_missing or pairing_fk_missing:
        raise ValueError("normalized tournament foreign-key invariant failed")

    return tournaments, participants, pairings, normalization_diagnostics


def _build_derivatives(
    tournaments: pd.DataFrame,
    participants: pd.DataFrame,
    pairings: pd.DataFrame,
    normalization_diagnostics: Mapping[str, int] | None = None,
) -> tuple[
    MetaAggregationResult,
    MatchAggregationResult,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    AcquisitionContracts,
    NormalizedSummary,
]:
    normalized = NormalizedSummary(
        tournaments_rows=len(tournaments),
        participants_rows=len(participants),
        pairings_rows=len(pairings),
        hashes={
            "tournaments": hash_dataframe(tournaments),
            "participants": hash_dataframe(participants),
            "pairings": hash_dataframe(pairings),
        },
        diagnostics=dict(normalization_diagnostics or {}),
    )

    meta_result = aggregate_meta(participants)
    match_result = aggregate_matchups(participants, pairings)
    top_meta = adapt_top_meta_decklist(meta_result.meta)
    matchup_raw = adapt_matchup_raw(match_result.matchups)
    axis = tuple(zip(top_meta["Deck ID"].tolist(), top_meta["Deck"].tolist()))
    dense_score = materialize_dense_score(matchup_raw, axis)
    contracts = build_acquisition_contracts(top_meta, matchup_raw, dense_score)
    return (
        meta_result,
        match_result,
        top_meta,
        matchup_raw,
        dense_score,
        contracts,
        normalized,
    )


def _rate_limit_rows(client: Any) -> tuple[Mapping[str, Any], ...]:
    observations = getattr(client, "rate_limit_observations", ())
    rows = []
    for item in observations:
        fetched_at = getattr(item, "fetched_at", None)
        rows.append(
            {
                "fetched_at": (
                    fetched_at.isoformat().replace("+00:00", "Z")
                    if isinstance(fetched_at, datetime)
                    else str(fetched_at or "")
                ),
                "status_code": int(getattr(item, "status_code", 0)),
                "headers": dict(getattr(item, "headers", {}) or {}),
            }
        )
    return tuple(rows)


def _pairing_diagnostics(match_result: MatchAggregationResult) -> dict[str, int]:
    diagnostics = dict(match_result.pairing_exclusion_counts)
    tie_total = int(pd.to_numeric(match_result.matchups.get("T", pd.Series(dtype=int)), errors="coerce").fillna(0).sum())
    diagnostics["ties"] = tie_total // 2
    diagnostics["byes"] = int(diagnostics.get("bye", 0))
    diagnostics["unresolved"] = int(diagnostics.get("unresolved_result", 0))
    diagnostics["conflict"] = 0
    return {str(key): int(value) for key, value in diagnostics.items()}


def _persist_run(
    *,
    raw_store: ImmutableRawStore,
    run_id: str,
    manifest: AcquisitionManifest,
    diagnostics: Mapping[str, Any],
) -> None:
    run_root = raw_store.paths.runs / run_id
    _atomic_write_json(run_root / "manifest.json", manifest.to_dict())
    _atomic_write_json(run_root / "diagnostics.json", dict(diagnostics))


def _live_run(
    *,
    game: str,
    format: str | None,
    set_mode: str,
    set_code: str | None,
    acquisition_started_at: datetime,
    raw_store: ImmutableRawStore,
    catalog: ReleaseCatalog,
    eligibility: EligibilityPolicy,
    client: LimitlessTournamentApiClient,
    run_id: str,
    software_git_revision: str,
    discovery_page_size: int,
    discovery_max_pages: int,
    reuse_latest_raw: bool,
    now_fn: Callable[[], datetime],
) -> AcquisitionRunResult:
    release = resolve_release(
        catalog,
        mode=set_mode,
        code=set_code,
        acquisition_started_at=acquisition_started_at,
    )
    scope = scope_for_release(
        release,
        acquisition_started_at=acquisition_started_at,
        game=game,
        format=format,
    )

    catalog_ref = raw_store.save_catalog_snapshot(
        "release-catalog",
        _catalog_payload(catalog),
        fetched_at=acquisition_started_at,
    )

    discovery_raw = client.list_tournaments(
        game=scope.game,
        format=None,
        page_size=discovery_page_size,
        max_pages=discovery_max_pages,
        use_cache=True,
    )
    discovery, duplicate_discovery = _canonical_discovery_records(discovery_raw)
    candidate_ids, discovery_diag = _discovery_candidates(
        discovery,
        scope=scope,
        page_size=discovery_page_size,
    )
    discovery_ref = raw_store.save_catalog_snapshot(
        "tournament-discovery",
        discovery,
        fetched_at=now_fn(),
    )

    details_by_id: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for tid in candidate_ids:
        try:
            details_by_id[tid] = client.get_tournament_details(tid, use_cache=True)
        except Exception as exc:  # captured as deterministic selection failure diagnostic
            failures[tid] = f"{type(exc).__name__}: {exc}"

    selection_rows = [_selection_record(details_by_id[tid]) for tid in sorted(details_by_id)]
    selection_rows.extend({"id": tid} for tid in sorted(failures))
    selection = select_tournaments(
        selection_rows,
        scope=scope,
        eligibility=eligibility,
        acquisition_failures=failures,
    )

    selection_details_ref = raw_store.save_catalog_snapshot(
        "tournament-selection-details",
        {
            "details": [details_by_id[tid] for tid in sorted(details_by_id)],
            "failures": {tid: failures[tid] for tid in sorted(failures)},
        },
        fetched_at=now_fn(),
    )

    raw_refs: list[RawPayloadRef] = [catalog_ref, discovery_ref, selection_details_ref]
    snapshot_ids: dict[str, str] = {}
    reused_snapshot_count = 0
    for tid in selection.tournament_ids:
        details = details_by_id.get(tid)
        if details is None:
            raise AcquisitionPipelineError(f"selected tournament has no details payload: {tid}")
        previous = raw_store.latest_snapshot_id(tid) if reuse_latest_raw else None
        snapshot = _acquire_selected_snapshot(
            tournament_id=tid,
            details=details,
            raw_store=raw_store,
            client=client,
            fetched_at=now_fn(),
            reuse_latest_raw=reuse_latest_raw,
        )
        if previous is not None and snapshot.snapshot_id == previous:
            reused_snapshot_count += 1
        snapshot_ids[tid] = snapshot.snapshot_id
        raw_refs.extend(snapshot.refs)

    raw_refs_tuple = tuple(raw_refs)
    raw_store.write_run_raw_refs(
        run_id,
        tournament_ids=selection.tournament_ids,
        refs=raw_refs_tuple,
    )

    tournaments, participants, pairings, normalization_diagnostics = _normalize_selected(
        raw_store=raw_store,
        selection=selection,
        tournament_snapshot_ids=snapshot_ids,
    )
    (
        meta_result,
        match_result,
        top_meta,
        matchup_raw,
        dense_score,
        contracts,
        normalized,
    ) = _build_derivatives(
        tournaments,
        participants,
        pairings,
        normalization_diagnostics=normalization_diagnostics,
    )

    aggregation = AggregationSummary(
        total_participants=meta_result.total_participants,
        classified_participants=meta_result.classified_participants,
        unclassified_participants=meta_result.unclassified_participants,
        comparable_matches=match_result.comparable_matches,
        pairing_exclusion_counts=match_result.pairing_exclusion_counts,
        deck_identity_diagnostics={
            "duplicate_display_names": {
                name: list(deck_ids)
                for name, deck_ids in meta_result.duplicate_display_names.items()
            }
        },
    )
    manifest = AcquisitionManifest(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        created_at=now_fn(),
        acquisition_started_at=acquisition_started_at,
        source=SOURCE_NAME,
        software_git_revision=software_git_revision,
        scope=scope,
        selection=selection,
        raw=RawSummary(snapshot_refs=raw_refs_tuple),
        normalized=normalized,
        aggregation=aggregation,
        rate_limit_observations=_rate_limit_rows(client),
        contracts=contracts,
    )
    manifest.to_json()

    diagnostics = {
        "execution_mode": "live",
        "run_id": run_id,
        "catalog_version": catalog.catalog_version,
        "scope_start": scope.start_datetime.isoformat().replace("+00:00", "Z"),
        "scope_end": scope.end_datetime.isoformat().replace("+00:00", "Z"),
        "selected_tournament_count": selection.included_count,
        "selected_tournament_ids": list(selection.tournament_ids),
        "selection_exclusion_counts": dict(selection.exclusion_counts),
        "selection_failures": list(selection.failures),
        "raw_ref_count": len(raw_refs_tuple),
        "reused_tournament_snapshots": reused_snapshot_count,
        "normalized_row_counts": {
            "tournaments": normalized.tournaments_rows,
            "participants": normalized.participants_rows,
            "pairings": normalized.pairings_rows,
        },
        "normalized_hashes": dict(normalized.hashes),
        "normalization_diagnostics": dict(normalized.diagnostics),
        "meta_rows": len(top_meta),
        "classified_participants": meta_result.classified_participants,
        "known_deck_matches": match_result.comparable_matches,
        "pairing_diagnostics": _pairing_diagnostics(match_result),
        "deck_identity_diagnostics": {
            "duplicate_display_names": {
                name: list(deck_ids)
                for name, deck_ids in meta_result.duplicate_display_names.items()
            }
        },
        "contract_hashes": {
            "top_meta_decklist": contracts.top_meta_decklist.sha256,
            "matchup_raw": contracts.matchup_raw.sha256,
            "dense_score": contracts.dense_score.sha256,
        },
        "contract_row_counts": {
            "top_meta_decklist": contracts.top_meta_decklist.row_count,
            "matchup_raw": contracts.matchup_raw.row_count,
            "dense_score": contracts.dense_score.row_count,
        },
        "discovery_duplicate_rows": duplicate_discovery,
        **discovery_diag,
    }
    _persist_run(raw_store=raw_store, run_id=run_id, manifest=manifest, diagnostics=diagnostics)
    return AcquisitionRunResult(
        contracts=contracts,
        manifest=manifest,
        diagnostics=diagnostics,
        frames=AcquisitionFrames(
            top_meta_decklist=top_meta,
            matchup_raw=matchup_raw,
            dense_score=dense_score,
        ),
    )


def _offline_run(
    *,
    game: str,
    format: str | None,
    set_code: str | None,
    acquisition_started_at: datetime | None,
    raw_store: ImmutableRawStore,
    catalog: ReleaseCatalog,
    run_id: str,
    replay_run_id: str,
    software_git_revision: str,
    now_fn: Callable[[], datetime],
) -> AcquisitionRunResult:
    source_manifest_path = raw_store.paths.runs / replay_run_id / "manifest.json"
    if not source_manifest_path.exists():
        raise FileNotFoundError(f"offline replay manifest not found: {source_manifest_path}")
    source_payload = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    validate_manifest_dict(source_payload)
    scope = _scope_from_manifest(source_payload)
    selection = _selection_from_manifest(source_payload)
    source_started = parse_utc_datetime(
        source_payload["acquisition_started_at"],
        field_name="acquisition_started_at",
    )

    if str(game).strip().upper() != scope.game:
        raise ValueError("offline replay game does not match source manifest scope")
    requested_format = None if format is None else str(format).strip().upper() or None
    if requested_format != scope.format:
        raise ValueError("offline replay format does not match source manifest scope")
    if set_code is not None and str(set_code).strip() != scope.set_code:
        raise ValueError("offline replay set_code does not match source manifest scope")
    if acquisition_started_at is not None:
        requested_started = require_utc(acquisition_started_at, field_name="acquisition_started_at")
        if requested_started != source_started:
            raise ValueError("offline replay acquisition_started_at must match the frozen source manifest")
    if catalog.catalog_version != scope.catalog_version:
        raise ValueError("offline replay release catalog version does not match source manifest scope")

    refs = tuple(_raw_ref_from_manifest(item) for item in source_payload["raw"]["snapshot_refs"])
    for ref in refs:
        _load_raw_ref(raw_store, ref)

    snapshot_ids: dict[str, str] = {}
    by_tid_types: dict[str, set[str]] = {}
    for ref in refs:
        if ref.tournament_id is None or ref.payload_type not in PAYLOAD_TYPES:
            continue
        existing = snapshot_ids.get(ref.tournament_id)
        if existing is not None and existing != ref.snapshot_id:
            raise ValueError(f"offline replay contains multiple snapshots for tournament {ref.tournament_id}")
        snapshot_ids[ref.tournament_id] = ref.snapshot_id
        by_tid_types.setdefault(ref.tournament_id, set()).add(ref.payload_type)

    for tid in selection.tournament_ids:
        if by_tid_types.get(tid) != set(PAYLOAD_TYPES):
            raise FileNotFoundError(f"offline replay has incomplete raw refs for selected tournament: {tid}")

    raw_store.write_run_raw_refs(run_id, tournament_ids=selection.tournament_ids, refs=refs)
    tournaments, participants, pairings, normalization_diagnostics = _normalize_selected(
        raw_store=raw_store,
        selection=selection,
        tournament_snapshot_ids=snapshot_ids,
    )
    (
        meta_result,
        match_result,
        top_meta,
        matchup_raw,
        dense_score,
        contracts,
        normalized,
    ) = _build_derivatives(
        tournaments,
        participants,
        pairings,
        normalization_diagnostics=normalization_diagnostics,
    )

    aggregation = AggregationSummary(
        total_participants=meta_result.total_participants,
        classified_participants=meta_result.classified_participants,
        unclassified_participants=meta_result.unclassified_participants,
        comparable_matches=match_result.comparable_matches,
        pairing_exclusion_counts=match_result.pairing_exclusion_counts,
        deck_identity_diagnostics={
            "duplicate_display_names": {
                name: list(deck_ids)
                for name, deck_ids in meta_result.duplicate_display_names.items()
            }
        },
    )
    manifest = AcquisitionManifest(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        created_at=now_fn(),
        acquisition_started_at=source_started,
        source=SOURCE_NAME,
        software_git_revision=software_git_revision,
        scope=scope,
        selection=selection,
        raw=RawSummary(snapshot_refs=refs),
        normalized=normalized,
        aggregation=aggregation,
        rate_limit_observations=(),
        contracts=contracts,
    )
    manifest.to_json()

    diagnostics = {
        "execution_mode": "offline",
        "run_id": run_id,
        "replay_run_id": replay_run_id,
        "catalog_version": catalog.catalog_version,
        "scope_start": scope.start_datetime.isoformat().replace("+00:00", "Z"),
        "scope_end": scope.end_datetime.isoformat().replace("+00:00", "Z"),
        "selected_tournament_count": selection.included_count,
        "selected_tournament_ids": list(selection.tournament_ids),
        "network_calls": 0,
        "raw_ref_count": len(refs),
        "normalized_row_counts": {
            "tournaments": normalized.tournaments_rows,
            "participants": normalized.participants_rows,
            "pairings": normalized.pairings_rows,
        },
        "normalized_hashes": dict(normalized.hashes),
        "normalization_diagnostics": dict(normalized.diagnostics),
        "meta_rows": len(top_meta),
        "classified_participants": meta_result.classified_participants,
        "known_deck_matches": match_result.comparable_matches,
        "pairing_diagnostics": _pairing_diagnostics(match_result),
        "deck_identity_diagnostics": {
            "duplicate_display_names": {
                name: list(deck_ids)
                for name, deck_ids in meta_result.duplicate_display_names.items()
            }
        },
        "contract_hashes": {
            "top_meta_decklist": contracts.top_meta_decklist.sha256,
            "matchup_raw": contracts.matchup_raw.sha256,
            "dense_score": contracts.dense_score.sha256,
        },
        "contract_row_counts": {
            "top_meta_decklist": contracts.top_meta_decklist.row_count,
            "matchup_raw": contracts.matchup_raw.row_count,
            "dense_score": contracts.dense_score.row_count,
        },
    }
    _persist_run(raw_store=raw_store, run_id=run_id, manifest=manifest, diagnostics=diagnostics)
    return AcquisitionRunResult(
        contracts=contracts,
        manifest=manifest,
        diagnostics=diagnostics,
        frames=AcquisitionFrames(
            top_meta_decklist=top_meta,
            matchup_raw=matchup_raw,
            dense_score=dense_score,
        ),
    )


def run_limitless_api_acquisition(
    *,
    game: str = "POCKET",
    format: str | None = "STANDARD",
    set_mode: str = "auto",
    set_code: str | None = None,
    acquisition_started_at: datetime | None = None,
    execution_mode: str = "live",
    raw_store_root: str | Path = "data/raw/limitless_api",
    release_catalog: ReleaseCatalog | str | Path | None = None,
    client: LimitlessTournamentApiClient | None = None,
    cache_root: str | Path = "cache/limitless_api",
    eligibility: EligibilityPolicy | None = None,
    run_id: str | None = None,
    replay_run_id: str | None = None,
    software_git_revision: str | None = None,
    discovery_page_size: int = 50,
    discovery_max_pages: int = 20,
    reuse_latest_raw: bool = True,
    now_fn: Callable[[], datetime] | None = None,
) -> AcquisitionRunResult:
    """Run the isolated Limitless Tournament API acquisition pipeline through public contracts/manifest only."""
    mode = str(execution_mode).strip().lower()
    if mode not in {"live", "offline"}:
        raise ValueError("execution_mode must be 'live' or 'offline'")
    if discovery_page_size <= 0:
        raise ValueError("discovery_page_size must be positive")
    if discovery_max_pages <= 0:
        raise ValueError("discovery_max_pages must be positive")

    now = now_fn or (lambda: datetime.now(UTC))
    catalog = _coerce_catalog(release_catalog)
    raw_store = ImmutableRawStore(raw_store_root)
    revision = str(software_git_revision or _git_revision()).strip() or "UNKNOWN"
    rid = str(run_id or "").strip()
    if not rid:
        stamp = now().astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        rid = f"limitless-api-{mode}-{stamp}"

    if mode == "offline":
        replay_id = str(replay_run_id or "").strip()
        if not replay_id:
            raise ValueError("replay_run_id is required in offline mode")
        return _offline_run(
            game=game,
            format=format,
            set_code=set_code,
            acquisition_started_at=acquisition_started_at,
            raw_store=raw_store,
            catalog=catalog,
            run_id=rid,
            replay_run_id=replay_id,
            software_git_revision=revision,
            now_fn=now,
        )

    started = require_utc(
        acquisition_started_at or now(),
        field_name="acquisition_started_at",
    )
    policy = eligibility or EligibilityPolicy(game=game)
    own_client = client is None
    api_client = client or LimitlessTournamentApiClient(cache=FileJsonCache(cache_root))
    try:
        return _live_run(
            game=game,
            format=format,
            set_mode=set_mode,
            set_code=set_code,
            acquisition_started_at=started,
            raw_store=raw_store,
            catalog=catalog,
            eligibility=policy,
            client=api_client,
            run_id=rid,
            software_git_revision=revision,
            discovery_page_size=discovery_page_size,
            discovery_max_pages=discovery_max_pages,
            reuse_latest_raw=reuse_latest_raw,
            now_fn=now,
        )
    finally:
        if own_client:
            api_client.close()


__all__ = [
    "AcquisitionPipelineError",
    "AcquisitionRunResult",
    "DiscoveryWindowIncompleteError",
    "run_limitless_api_acquisition",
]
