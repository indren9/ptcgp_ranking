"""Reviewed boundary input: starts are authoritative; ends are always derived."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
from urllib.parse import urlparse


class RebuildError(RuntimeError):
    kind = "BLOCKED"


class NotReady(RebuildError):
    kind = "NOT_READY"


class IntegrityError(RebuildError):
    kind = "INTEGRITY_FAILURE"


class CheckpointError(RebuildError):
    kind = "STALE_CHECKPOINT"


class RetryableError(RebuildError):
    kind = "FAILED_RETRYABLE"


class ValidationError(RebuildError):
    kind = "VALIDATION_FAILURE"


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value
    ):
        raise NotReady("NOT READY: an explicit official UTC timestamp, including time, is required")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise NotReady(f"unsafe identity: {value!r}")
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
        raise NotReady(f"reserved Windows identity: {value}")
    return value


@dataclass(frozen=True)
class Window:
    window_id: str
    name: str
    start_utc: str
    end_utc: str | None
    events: tuple[dict, ...]
    end_events: tuple[dict, ...]
    observation_cutoff_utc: str | None = None

    def definition(self) -> dict:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return digest(self.definition())

    @property
    def effective_end(self) -> str:
        if self.end_utc:
            return self.end_utc
        if not self.observation_cutoff_utc:
            raise NotReady(f"{self.window_id}: OPEN requires an explicit observation_cutoff_utc; it is not a canonical end")
        return self.observation_cutoff_utc


def load_windows(payload: dict) -> list[Window]:
    """Validate review attestations, never discover/promote/infer official times.

    Coincident events remain distinct identities on one boundary. Reject an
    incomplete bundle rather than accidentally joining across a missing start.
    """
    if payload.get("schema_version") != 1 or payload.get("reviewed") is not True:
        raise NotReady("NOT READY: schema_version=1 and explicit reviewed=true are required")
    events = payload.get("boundaries")
    if not isinstance(events, list) or not events:
        raise NotReady("NOT READY: non-empty ordered boundaries required")
    groups: list[list[dict]] = []
    seen = set()
    previous = None
    for event in events:
        if not isinstance(event, dict) or set(event) != {"event_id", "name", "kind", "start_utc", "reasons", "evidence"}:
            raise NotReady("NOT READY: boundary fields must be event_id/name/kind/start_utc/reasons/evidence; independent ends are forbidden")
        identity = safe_id(event["event_id"])
        if identity.casefold() in seen:
            raise NotReady("duplicate/case-colliding boundary identity")
        seen.add(identity.casefold())
        start = utc(event["start_utc"])
        if previous is not None and start < previous:
            raise NotReady("canonical boundaries must already be chronologically ordered")
        if event["kind"] not in {"expansion", "rotation"} or not isinstance(event["name"], str) or not event["name"].strip():
            raise NotReady("invalid event identity/kind")
        reasons = event["reasons"]
        if not isinstance(reasons, list) or not reasons or any(not isinstance(r, str) or not r.strip() for r in reasons):
            raise NotReady("explicit boundary reasons required")
        evidence = event["evidence"]
        if not isinstance(evidence, dict) or evidence.get("reviewed") is not True or evidence.get("exact_time") is not True:
            raise NotReady("NOT READY: reviewed exact first-party evidence required")
        url = urlparse(evidence.get("url", ""))
        host = url.hostname or ""
        if url.scheme != "https" or not any(host == d or host.endswith("." + d) for d in ("pokemon.com", "pokemon-support.com")):
            raise NotReady("NOT READY: official Pokémon first-party evidence URL required")
        clean = {**event, "start_utc": stamp(start), "reasons": sorted(set(reasons))}
        if previous == start:
            groups[-1].append(clean)
        else:
            groups.append([clean])
        previous = start
    cutoff = payload.get("observation_cutoff_utc")
    if cutoff is not None:
        cutoff = stamp(utc(cutoff))
        if utc(cutoff) <= previous:
            raise NotReady("OPEN observation cutoff must be after the final official start")
    windows = []
    for i, group in enumerate(groups):
        group.sort(key=lambda e: (e["kind"] != "expansion", e["event_id"]))
        following = groups[i + 1] if i + 1 < len(groups) else []
        windows.append(Window(group[0]["event_id"], " / ".join(e["name"] for e in group),
                              group[0]["start_utc"], following[0]["start_utc"] if following else None,
                              tuple(group), tuple(sorted(following, key=lambda e: e["event_id"])),
                              cutoff if not following else None))
    return windows
