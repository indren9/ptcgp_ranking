from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from sources.limitless.tournament_api.release_catalog import (
    load_release_catalog_snapshot,
    resolve_release,
    scope_for_release,
)


CATALOG_PATH = Path("data/reference/pocket_releases.json")
EXPECTED_CODES = (
    "A1",
    "A1a",
    "A2",
    "A2a",
    "A2b",
    "A3",
    "A3a",
    "A3b",
    "A4",
    "A4a",
    "A4b",
    "B1",
    "B1a",
    "B2",
    "B2a",
    "B2b",
    "B3",
    "B3a",
    "B3b",
    "B4",
    "B4a",
)


def _raw_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _catalog():
    return load_release_catalog_snapshot(CATALOG_PATH)


def test_reference_catalog_covers_ranked_nonpromo_pocket_sequence():
    catalog = _catalog()
    assert tuple(item.code for item in catalog.releases) == EXPECTED_CODES
    assert "P-A" not in EXPECTED_CODES
    assert "P-B" not in EXPECTED_CODES
    assert len(set(EXPECTED_CODES)) == len(EXPECTED_CODES)
    assert all(item.name.strip() for item in catalog.releases)


def test_reference_catalog_has_strict_utc_order_and_coherent_next_chain():
    releases = _catalog().releases
    for index, release in enumerate(releases):
        assert release.release_datetime.tzinfo is UTC
        if index + 1 < len(releases):
            following = releases[index + 1]
            assert release.release_datetime < following.release_datetime
            assert release.next_release_datetime == following.release_datetime
            assert release.is_current is False
        else:
            assert release.next_release_datetime is None
            assert release.is_current is True


def test_reference_catalog_snapshot_has_complete_provenance():
    payload = _raw_catalog()
    assert payload["catalog_version"] == "pocket-releases-2026-09-03-v3"
    assert payload["source"] == "https://pocket.limitlesstcg.com/cards"

    for item in payload["releases"]:
        assert item["source"].startswith("https://")
        assert item["validation_source"].startswith("https://")
        assert item["validation_tier"] in {
            "pokemon_official",
            "third_party_operational",
            "third_party_press_release",
        }
        assert item["validation_note"].strip()
        assert item["catalog_version"] == payload["catalog_version"]


def test_auto_resolves_max_release_at_or_before_acquisition_time():
    catalog = _catalog()
    for release in catalog.releases:
        started = release.release_datetime + timedelta(seconds=1)
        selected = resolve_release(
            catalog,
            mode="auto",
            acquisition_started_at=started,
        )
        assert selected.code == release.code

    before_first = catalog.releases[0].release_datetime - timedelta(seconds=1)
    with pytest.raises(ValueError, match="no released expansion"):
        resolve_release(
            catalog,
            mode="auto",
            acquisition_started_at=before_first,
        )


def test_code_resolves_every_cataloged_release():
    catalog = _catalog()
    started = datetime(2026, 8, 22, 16, 0, tzinfo=UTC)
    for code in EXPECTED_CODES:
        assert resolve_release(
            catalog,
            mode="code",
            code=code,
            acquisition_started_at=started,
        ).code == code


def test_completed_release_scope_uses_next_release_boundary():
    catalog = _catalog()
    started = datetime(2026, 8, 22, 16, 0, tzinfo=UTC)
    release = resolve_release(
        catalog,
        mode="code",
        code="A4a",
        acquisition_started_at=started,
    )
    scope = scope_for_release(release, acquisition_started_at=started)
    assert scope.start_datetime == datetime(2025, 8, 28, 6, 0, tzinfo=UTC)
    assert scope.end_datetime == datetime(2025, 9, 30, 6, 0, tzinfo=UTC)


def test_completed_b4_scope_uses_b4a_release_boundary():
    catalog = _catalog()
    started = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)
    release = resolve_release(
        catalog,
        mode="code",
        code="B4",
        acquisition_started_at=started,
    )
    assert release.code == "B4"
    scope = scope_for_release(release, acquisition_started_at=started)
    assert scope.start_datetime == datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    assert scope.end_datetime == datetime(2026, 8, 27, 1, 0, tzinfo=UTC)


def test_current_b4a_scope_uses_frozen_acquisition_started_at():
    catalog = _catalog()
    started = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)
    release = resolve_release(
        catalog,
        mode="auto",
        acquisition_started_at=started,
    )
    assert release.code == "B4a"
    scope = scope_for_release(release, acquisition_started_at=started)
    assert scope.start_datetime == datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
    assert scope.end_datetime == started


def test_current_flag_is_snapshot_informational_but_auto_is_time_based():
    catalog = _catalog()
    # AUTO before B4 must still resolve B3b even though the versioned snapshot
    # marks B4a as the current expansion at catalog publication time.
    started = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    selected = resolve_release(
        catalog,
        mode="auto",
        acquisition_started_at=started,
    )
    assert selected.code == "B3b"
