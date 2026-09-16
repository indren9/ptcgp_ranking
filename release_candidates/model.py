"""Strict, stdlib-only candidate contracts; no canonical writers or network IO."""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields
from datetime import date, datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


STATES = frozenset({"DISCOVERED", "DATE_CONFIRMED", "TIMESTAMP_CONFIRMED",
                    "CANONICAL_CANDIDATE", "CANONICAL_APPROVED", "CONFLICT",
                    "QUARANTINED", "SUPERSEDED"})
MATURE = frozenset({"TIMESTAMP_CONFIRMED", "CANONICAL_CANDIDATE", "CANONICAL_APPROVED"})
CONTEXTS = {("POCKET", "POCKET", "STANDARD"), ("PTCG", "PTCGL", "STANDARD")}
KINDS = {"expansion": ("full_expansion_availability",),
         "rotation": ("standard_rotation",),
         "combined": ("full_expansion_availability", "standard_rotation")}
AUTHORITY = {"A": ("pokemon", {"documented_api"}),
             "B": ("pokemon", {"official_announcement", "official_staff_announcement"}),
             "C": ("limitless", {"operational_metadata"}),
             "D": ("third_party", {"independent_report"}),
             "E": ("community", {"community_observation"})}
HEX = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nonempty(value: object, label: str) -> None:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be a nonempty trimmed string")


def aware(value: str) -> datetime:
    if type(value) is not str or "T" not in value:
        raise ValueError("exact timestamp requires date, time and offset")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("exact timestamp must be timezone-aware")
    return parsed


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def exact_date(value: str) -> None:
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    date.fromisoformat(value)


def https_host(value: str) -> str:
    nonempty(value, "URL")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.fragment or any(c.isspace() for c in value) or url.port not in (None, 443)):
        raise ValueError("evidence URL must be an absolute HTTPS URL without credentials/fragment")
    return url.hostname.lower()


def under(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def strict_fields(cls, payload: dict) -> dict:
    if type(payload) is not dict:
        raise ValueError("expected JSON object")
    unknown = set(payload) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    return dict(payload)


@dataclass(frozen=True, kw_only=True)
class Evidence:
    event_reference: str
    source_tier: str
    source_url: str
    source_authority: str
    source_category: str
    retrieved_at: str
    evidence_digest: str
    retained_excerpt: str
    authority_verified: bool = False
    authority_note: str | None = None
    source_author: str | None = None
    documentation_url: str | None = None
    extracted_date: str | None = None
    extracted_time: str | None = None
    extracted_timezone: str | None = None
    effective_local_datetime: str | None = None
    conversion_rationale: str | None = None

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None and f.default is MISSING:
                raise ValueError(f"{f.name} cannot be null")
            if f.name != "authority_verified" and value is not None:
                nonempty(value, f.name)
        if type(self.authority_verified) is not bool:
            raise ValueError("authority_verified must be a JSON boolean")
        if self.source_tier not in AUTHORITY:
            raise ValueError("unsupported source tier")
        authority, categories = AUTHORITY[self.source_tier]
        if self.source_authority != authority or self.source_category not in categories:
            raise ValueError("authority/category inconsistent with tier")
        host = https_host(self.source_url)
        if self.source_tier in {"A", "B"} and not under(host, "pokemon.com"):
            raise ValueError("first-party evidence requires an approved official host")
        if self.source_tier == "C" and not under(host, "limitlesstcg.com"):
            raise ValueError("Tier C requires a Limitless host")
        if self.source_tier == "B" and under(host, "community.pokemon.com") and self.source_category != "official_staff_announcement":
            raise ValueError("official forum evidence requires the staff category and author review")
        if self.source_category == "official_staff_announcement" and host != "community.pokemon.com":
            raise ValueError("staff announcement category requires official forum host")
        if self.source_tier == "A":
            if not self.documentation_url or not under(https_host(self.documentation_url), "pokemon.com"):
                raise ValueError("Tier A requires official API documentation URL")
        if self.source_category == "official_staff_announcement" and not self.source_author:
            raise ValueError("official staff evidence requires named author")
        if self.authority_verified and not self.authority_note:
            raise ValueError("verified authority requires an explicit review note")
        aware(self.retrieved_at)
        if not HEX.fullmatch(self.evidence_digest):
            raise ValueError("evidence digest must be SHA-256")
        if len(self.retained_excerpt) > 1200:
            raise ValueError("retain only the relevant short evidence excerpt")
        if digest(self.retained_excerpt.encode("utf-8")) != self.evidence_digest:
            raise ValueError("retained evidence digest mismatch")
        if self.extracted_date is not None:
            exact_date(self.extracted_date)
        exact_parts = (self.extracted_time, self.extracted_timezone, self.effective_local_datetime)
        if any(p is not None for p in exact_parts):
            if not self.extracted_date or not all(exact_parts) or not self.conversion_rationale:
                raise ValueError("exact evidence requires date, time, timezone, local offset and rationale")
            self.utc()

    @property
    def eligible(self) -> bool:
        # URL shape is not authority proof: explicit recorded human attestation is required.
        return self.source_tier in {"A", "B"} and self.authority_verified

    def utc(self) -> str | None:
        if self.effective_local_datetime is None:
            return None
        local = aware(self.effective_local_datetime)
        if local.date().isoformat() != self.extracted_date:
            raise ValueError("extracted date/local timestamp mismatch")
        if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", self.extracted_time or ""):
            raise ValueError("extracted time must use HH:MM:SS")
        if local.time().isoformat() != self.extracted_time:
            raise ValueError("extracted time/local timestamp mismatch")
        zone = self.extracted_timezone
        if zone == "UTC":
            expected = timezone.utc
        elif re.fullmatch(r"[+-]\d{2}:\d{2}", zone or ""):
            expected = aware("2000-01-01T00:00:00" + zone).tzinfo
        else:
            try:
                expected = ZoneInfo(zone)
            except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
                raise ValueError("unsupported timezone; supply IANA name, UTC or explicit offset") from exc
        roundtrip = local.astimezone(timezone.utc).astimezone(expected)
        if roundtrip.replace(tzinfo=None) != local.replace(tzinfo=None) or roundtrip.utcoffset() != local.utcoffset():
            raise ValueError("timezone/local offset mismatch or nonexistent local time")
        # Explicit local offset disambiguates repeated DST hours; no guessed fold.
        return utc_text(local)

    @classmethod
    def from_dict(cls, payload):
        try:
            return cls(**strict_fields(cls, payload))
        except TypeError as exc:
            raise ValueError("invalid evidence schema") from exc


def event_identity(game: str, platform: str, format: str, event_key: str) -> str:
    if (game, platform, format) not in CONTEXTS or not TOKEN.fullmatch(event_key):
        raise ValueError("unsupported context or logical event key")
    return f"{game.lower()}:{platform.lower()}:{format.lower()}:{event_key}"


@dataclass(frozen=True, kw_only=True)
class Candidate:
    game: str
    platform: str
    format: str
    release_code: str
    release_name: str
    event_key: str
    semantic_event_id: str
    event_kind: str
    boundary_reasons: tuple[str, ...]
    candidate_state: str
    candidate_revision: str
    evidence: tuple[Evidence, ...]
    identity_verified: bool = False
    identity_note: str | None = None
    effective_date: str | None = None
    effective_local_datetime: str | None = None
    source_timezone: str | None = None
    effective_utc_datetime: str | None = None
    parent_canonical_version: str | None = None
    parent_canonical_digest: str | None = None
    conflict_status: str = "NONE"
    supersedes_revision: str | None = None
    missing_evidence: tuple[str, ...] = ()
    approval_reference: str | None = None

    def __post_init__(self):
        tuple_fields = {"boundary_reasons", "evidence", "missing_evidence"}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None and f.default is MISSING:
                raise ValueError(f"{f.name} cannot be null")
            if f.name not in tuple_fields | {"identity_verified"} and value is not None:
                nonempty(value, f.name)
        if type(self.identity_verified) is not bool:
            raise ValueError("identity_verified must be a JSON boolean")
        if self.identity_verified and not self.identity_note:
            raise ValueError("verified identity requires a mapping review note")
        if self.semantic_event_id != event_identity(self.game, self.platform, self.format, self.event_key):
            raise ValueError("semantic event ID does not match stable logical event key")
        if self.candidate_state not in STATES:
            raise ValueError("unsupported candidate state; activation is outside Phase B")
        if self.event_kind not in KINDS or self.boundary_reasons != KINDS[self.event_kind]:
            raise ValueError("boundary reasons must be sorted unique reasons matching event kind")
        if self.event_kind == "rotation" and not self.event_key.startswith("rotation:"):
            raise ValueError("rotation-only event requires its own rotation key")
        if self.event_kind in {"expansion", "combined"} and not self.event_key.startswith("release:"):
            raise ValueError("expansion event requires a release key")
        if type(self.evidence) is not tuple or not self.evidence or any(type(e) is not Evidence for e in self.evidence):
            raise ValueError("candidate requires a nonempty evidence tuple")
        if type(self.missing_evidence) is not tuple:
            raise ValueError("missing_evidence must be a tuple")
        for item in self.missing_evidence:
            nonempty(item, "missing_evidence")
        if self.conflict_status not in {"NONE", "OPERATIONAL_ADVISORY", "AUTHORITATIVE"}:
            raise ValueError("unsupported conflict_status")
        for value in (self.parent_canonical_digest, self.supersedes_revision):
            if value is not None and not HEX.fullmatch(value):
                raise ValueError("parent/revision digest must be SHA-256")
        if self.effective_date:
            exact_date(self.effective_date)
        if self.effective_local_datetime:
            aware(self.effective_local_datetime)
        if self.effective_utc_datetime:
            parsed = aware(self.effective_utc_datetime)
            if self.effective_utc_datetime != utc_text(parsed):
                raise ValueError("effective UTC must use normalized UTC Z notation")
        if self.candidate_state == "CANONICAL_APPROVED" and not self.approval_reference:
            raise ValueError("represented approval requires an external review reference")
        if self.candidate_revision != self.revision():
            raise ValueError("candidate revision does not match deterministic content digest")

    def to_dict(self) -> dict:
        # JSON-compatible arrays, including empty tuples.
        return json.loads(stable_json(asdict(self)))

    def revision(self) -> str:
        payload = asdict(self)
        # Lifecycle/review annotations do not create a different evidence revision.
        for field in ("candidate_revision", "candidate_state", "approval_reference"):
            payload.pop(field)
        return digest(stable_json(payload).encode("utf-8"))

    @classmethod
    def from_dict(cls, payload):
        data = strict_fields(cls, payload)
        for key in ("boundary_reasons", "evidence", "missing_evidence"):
            if key in data:
                if type(data[key]) is not list:
                    raise ValueError(f"{key} must be a JSON array")
                data[key] = tuple(data[key])
        data["evidence"] = tuple(Evidence.from_dict(e) for e in data.get("evidence", ()))
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError("invalid candidate schema") from exc


def prepare_candidate(payload: dict) -> Candidate:
    """Seal a supplied proposal, never infer evidence, approve, activate or publish it."""
    data = dict(payload)
    if data.get("candidate_state") == "CANONICAL_APPROVED":
        raise ValueError("automatic preparation cannot approve candidates")
    data.setdefault("missing_evidence", [])
    # Fill dataclass defaults before hashing, without skipping final strict construction.
    for field in fields(Candidate):
        if field.name not in data and field.default is not MISSING:
            data[field.name] = field.default
    revision_payload = dict(data)
    for key in ("candidate_revision", "candidate_state", "approval_reference"):
        revision_payload.pop(key, None)
    # Expand evidence defaults so identity is identical after loading/serialization.
    revision_payload["evidence"] = [asdict(Evidence.from_dict(e)) for e in data.get("evidence", [])]
    data["candidate_revision"] = digest(stable_json(revision_payload).encode("utf-8"))
    return Candidate.from_dict(data)
