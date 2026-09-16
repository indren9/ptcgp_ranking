"""Advisory validation against supplied immutable canonical bytes. No writes."""
from __future__ import annotations

from dataclasses import dataclass
import json

from .model import Candidate, MATURE, digest


@dataclass(frozen=True)
class ParentSnapshot:
    version: str
    sha256: str
    starts: tuple[tuple[str, str], ...]

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ParentSnapshot":
        data = json.loads(raw)
        version = data["catalog_version"]
        if type(version) is not str or not version.strip():
            raise ValueError("invalid parent canonical version")
        starts = tuple((row["code"], row["release_datetime"]) for row in data["releases"])
        if len({code.casefold() for code, _ in starts}) != len(starts):
            raise ValueError("casefold collision in parent canonical codes")
        return cls(version, digest(raw), starts)


def validate_candidates(candidates: list[Candidate], parents: dict[str, ParentSnapshot]) -> list[dict]:
    """Validate a complete review bundle, including superseded ancestors.

    Parents are externally supplied current snapshots: candidate parent identity is
    compared, never refreshed. Promotable is advisory only, not approval/activation.
    """
    revisions = {c.candidate_revision: c for c in candidates}
    duplicate_revisions = len(revisions) != len(candidates)
    results = []
    for candidate in sorted(candidates, key=lambda c: (c.semantic_event_id, c.candidate_revision)):
        c = candidate
        errors: list[str] = []
        missing = list(c.missing_evidence)
        conflicts: list[str] = []
        if duplicate_revisions:
            errors.append("duplicate candidate revision in review bundle")
        parent = parents.get(c.game)
        parent_bound = bool(c.parent_canonical_version and c.parent_canonical_digest)
        stale = bool(parent_bound and parent and (
            c.parent_canonical_version != parent.version or c.parent_canonical_digest != parent.sha256))
        if stale:
            errors.append("stale parent: canonical bytes/version changed; explicit re-review required")
        if not parent:
            missing.append("current canonical parent snapshot")
        if not parent_bound:
            missing.append("parent canonical version and digest")
        if c.candidate_state in MATURE and (not parent_bound or not parent):
            errors.append("mature candidate requires a verifiable canonical parent")
        if not c.identity_verified:
            missing.append("reviewed release code/name and semantic event mapping")
        for evidence in c.evidence:
            if evidence.event_reference != c.semantic_event_id:
                errors.append("evidence belongs to a different semantic event")
        primary = c.evidence[0]
        eligible = [e for e in c.evidence if e.eligible]
        exact = [e for e in eligible if e.utc() is not None]
        if not eligible:
            missing.append("reviewed Tier A/B authority")
        if not primary.extracted_date:
            missing.append("release date")
        if not exact:
            missing.append("eligible exact time and timezone")
        if c.effective_date != primary.extracted_date:
            errors.append("proposed date does not match primary evidence (no inference allowed)")
        expected_time = primary.utc()
        if c.effective_utc_datetime != expected_time:
            errors.append("proposed UTC does not match deterministic primary conversion")
        if c.effective_local_datetime != primary.effective_local_datetime or c.source_timezone != primary.extracted_timezone:
            errors.append("proposed local time/timezone does not match primary evidence")
        if c.effective_utc_datetime and not primary.eligible:
            # Lower tiers may retain an observation but never confirm a future time.
            if c.candidate_state in MATURE:
                errors.append("Tier C/D/E cannot authorize an exact future timestamp")
        if c.candidate_state == "DATE_CONFIRMED":
            if not primary.eligible or not primary.extracted_date:
                errors.append("DATE_CONFIRMED requires reviewed official date evidence")
            if c.effective_utc_datetime is not None:
                errors.append("DATE_CONFIRMED must keep exact timestamp unknown")
        if c.candidate_state in MATURE and (not primary.eligible or expected_time is None):
            errors.append("timestamp-confirmed states require exact eligible primary evidence")
        # Date disagreements matter even if one announcement omits an exact hour.
        # Two local dates can describe the same instant in different zones.
        # Compare exact evidence in UTC; compare date-only evidence in its own
        # declared date context (no inferred UTC midnight).
        authoritative_dates = {e.extracted_date for e in eligible if e.extracted_date and e.utc() is None}
        authoritative_times = {e.utc() for e in exact}
        date_conflict = (len(authoritative_dates) > 1 or bool(authoritative_dates and
                         any(e.extracted_date not in authoritative_dates for e in exact)))
        authoritative_conflict = date_conflict or len(authoritative_times) > 1
        operational_conflict = any(
            e.source_tier in {"C", "D", "E"} and (
                (e.utc() is not None and expected_time is not None and e.utc() != expected_time)
                or ((e.utc() is None or expected_time is None) and e.extracted_date
                    and primary.extracted_date and e.extracted_date != primary.extracted_date)
            ) for e in c.evidence
        )
        conflict_status = "AUTHORITATIVE" if authoritative_conflict else (
            "OPERATIONAL_ADVISORY" if operational_conflict else "NONE")
        if authoritative_conflict:
            conflicts.append("eligible authoritative sources disagree")
            if c.candidate_state not in {"CONFLICT", "QUARANTINED", "SUPERSEDED"}:
                errors.append("authoritative disagreement requires CONFLICT/QUARANTINED")
        if operational_conflict:
            conflicts.append("operational/corroborating time disagrees; official evidence retains authority")
        if c.conflict_status != conflict_status:
            errors.append("declared conflict status does not match evidence")

        if parent:
            for code, start in parent.starts:
                if code.casefold() == c.release_code.casefold() and code != c.release_code:
                    errors.append("casefold collision with canonical code; no guessed alias")
                if (code == c.release_code and c.event_kind != "rotation" and c.effective_utc_datetime
                        and c.effective_utc_datetime != start and not c.supersedes_revision):
                    errors.append("canonical timing correction requires an explicit predecessor revision")
        same_event = [other for other in candidates if other.semantic_event_id == c.semantic_event_id]
        if sum(other.candidate_state != "SUPERSEDED" for other in same_event) > 1:
            errors.append("multiple live revisions for one semantic event")
        if c.supersedes_revision:
            previous = revisions.get(c.supersedes_revision)
            if previous is None:
                errors.append("supersession predecessor missing from review bundle")
            elif previous.semantic_event_id != c.semantic_event_id:
                errors.append("supersession cannot change semantic event identity")
            elif previous.candidate_state != "SUPERSEDED":
                errors.append("predecessor must be explicitly SUPERSEDED")
        elif len(same_event) > 1 and c.candidate_state != "SUPERSEDED":
            errors.append("new revision requires supersedes_revision")
        if c.candidate_state == "SUPERSEDED" and not any(
            other.supersedes_revision == c.candidate_revision for other in same_event
        ):
            errors.append("SUPERSEDED revision requires a successor in the review bundle")
        seen = set()
        cursor = c
        while cursor:
            if cursor.candidate_revision in seen:
                errors.append("supersession cycle")
                break
            seen.add(cursor.candidate_revision)
            cursor = revisions.get(cursor.supersedes_revision)

        for other in candidates:
            if other is c or other.candidate_state == "SUPERSEDED" or c.candidate_state == "SUPERSEDED":
                continue
            context = (c.game, c.platform, c.format)
            if context != (other.game, other.platform, other.format):
                continue
            if c.release_code.casefold() == other.release_code.casefold() and c.release_code != other.release_code:
                errors.append("casefold code collision between candidates")
            if other.semantic_event_id != c.semantic_event_id:
                if (c.effective_utc_datetime and c.effective_utc_datetime == other.effective_utc_datetime):
                    errors.append("coincident boundaries must be one event with multiple reasons")
                if (c.release_code.casefold() == other.release_code.casefold()
                        and c.event_kind != "rotation" and other.event_kind != "rotation"):
                    errors.append("same expansion boundary cannot acquire a new semantic identity")

        if c.candidate_state in {"CANONICAL_CANDIDATE", "CANONICAL_APPROVED"} and missing:
            errors.append("canonical candidate/approval requires all structural and evidence checks")
        promotable = (c.candidate_state in MATURE and not errors and not missing
                      and not authoritative_conflict and not stale)
        result = c.to_dict()
        result.update({
            "submitted_candidate_state": c.candidate_state,
            "candidate_state": ("CONFLICT" if authoritative_conflict and c.candidate_state not in
                                {"QUARANTINED", "SUPERSEDED"} else c.candidate_state),
            "source_tier": primary.source_tier, "source_url": primary.source_url,
            "conflict_status": conflict_status, "conflicts": sorted(set(conflicts)),
            "missing_evidence": sorted(set(missing)), "validation_errors": sorted(set(errors)),
            "stale_parent": stale, "structurally_promotable": promotable,
            "approval_is_activation": False, "canonical_mutation_allowed": False,
        })
        results.append(result)
    return results
