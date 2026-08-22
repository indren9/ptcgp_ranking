from datetime import UTC, datetime, timedelta, timezone

import pytest

from acquisition.scope import EligibilityPolicy
from acquisition.selection import EXCLUSION_REASONS, select_tournaments
from domain.releases import ExpansionRelease, ReleaseCatalog
from sources.limitless.tournament_api.release_catalog import resolve_release, scope_for_release


def dt(day, hour=0):
    return datetime(2026, 8, day, hour, tzinfo=UTC)


def catalog():
    return ReleaseCatalog(
        catalog_version="cat-v1",
        source="limitless-pocket-database",
        releases=(
            ExpansionRelease("B3b", "Old", dt(1), dt(10), False, "limitless-pocket-database", "cat-v1"),
            ExpansionRelease("B4", "Current", dt(10), None, True, "limitless-pocket-database", "cat-v1"),
        ),
    )


def test_release_boundary_is_explicit_utc_and_half_open_scope():
    selected = resolve_release(catalog(), mode="code", code="B3b", acquisition_started_at=dt(20))
    scope = scope_for_release(selected, acquisition_started_at=dt(20))

    assert scope.start_datetime == dt(1)
    assert scope.end_datetime == dt(10)
    assert scope.start_datetime.tzinfo is UTC
    assert scope.end_datetime.tzinfo is UTC


def test_auto_selects_latest_release_at_acquisition_start():
    assert resolve_release(catalog(), mode="auto", acquisition_started_at=dt(9)).code == "B3b"
    assert resolve_release(catalog(), mode="auto", acquisition_started_at=dt(10)).code == "B4"


def test_code_is_explicit_and_unknown_code_fails():
    assert resolve_release(catalog(), mode="code", code="B3b", acquisition_started_at=dt(20)).code == "B3b"
    with pytest.raises(KeyError, match="unknown expansion code"):
        resolve_release(catalog(), mode="code", code="NOPE", acquisition_started_at=dt(20))


def test_choose_resolves_to_code_through_same_path():
    calls = []

    def chooser(releases):
        calls.append(tuple(item.code for item in releases))
        return "B3b"

    selected = resolve_release(catalog(), mode="choose", acquisition_started_at=dt(20), chooser=chooser)
    assert selected.code == "B3b"
    assert calls == [("B3b", "B4")]


def test_current_set_scope_ends_at_frozen_acquisition_started_at():
    acquisition_started_at = datetime(2026, 8, 22, 14, 15, tzinfo=timezone(timedelta(hours=2)))
    selected = resolve_release(catalog(), mode="code", code="B4", acquisition_started_at=acquisition_started_at)
    scope = scope_for_release(selected, acquisition_started_at=acquisition_started_at)

    assert scope.end_datetime == datetime(2026, 8, 22, 12, 15, tzinfo=UTC)


def base_record(tid, date, **overrides):
    row = {
        "tournament_id": tid,
        "date": date,
        "game": "POCKET",
        "format": "STANDARD",
        "is_public": True,
        "decklists": True,
    }
    row.update(overrides)
    return row


def test_selector_applies_half_open_boundary_and_is_deterministic():
    scope = scope_for_release(
        resolve_release(catalog(), mode="code", code="B3b", acquisition_started_at=dt(20)),
        acquisition_started_at=dt(20),
    )
    records = [
        base_record("inside-b", dt(9)),
        base_record("at-end", dt(10)),
        base_record("inside-a", dt(1)),
    ]

    first = select_tournaments(records, scope=scope, eligibility=EligibilityPolicy())
    second = select_tournaments(reversed(records), scope=scope, eligibility=EligibilityPolicy())

    assert first.tournament_ids == ("inside-a", "inside-b")
    assert second.tournament_ids == first.tournament_ids
    assert first.exclusion_counts["outside_window"] == 1


def test_selector_reports_all_frozen_exclusion_reasons():
    scope = scope_for_release(
        resolve_release(catalog(), mode="code", code="B3b", acquisition_started_at=dt(20)),
        acquisition_started_at=dt(20),
    )
    records = [
        base_record("ok", dt(2)),
        base_record("game", dt(2), game="PTCG"),
        base_record("format", dt(2), format="CUSTOM"),
        base_record("window", dt(10)),
        base_record("private", dt(2), is_public=False),
        base_record("nodecks", dt(2), decklists=False),
        {"tournament_id": "invalid", "game": "POCKET"},
        base_record("failed", dt(2), is_public=None, decklists=None),
    ]

    result = select_tournaments(
        records,
        scope=scope,
        eligibility=EligibilityPolicy(),
        acquisition_failures={"failed": "details fetch failed"},
    )

    assert result.tournament_ids == ("ok",)
    for reason in EXCLUSION_REASONS:
        assert result.exclusion_counts[reason] == 1
    assert result.failures == ("failed: details fetch failed",)


def test_null_format_is_eligible_under_pocket_v1():
    scope = scope_for_release(
        resolve_release(catalog(), mode="code", code="B3b", acquisition_started_at=dt(20)),
        acquisition_started_at=dt(20),
    )
    result = select_tournaments(
        [base_record("null-format", dt(2), format=None)],
        scope=scope,
        eligibility=EligibilityPolicy(),
    )
    assert result.tournament_ids == ("null-format",)
