from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from scripts.tcg_live_publication import (
    PUBLIC_FILES,
    publish_tcg_live_bundle,
)


COLUMNS = (
    "Rank",
    "Deck",
    "Score_%",
    "MAS_%",
    "LB_%",
    "BT_%",
    "SE_%",
    "N_eff",
    "Opp_used",
    "Opp_total",
    "Coverage_%",
)


def sha(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def fake_bundle(root: Path) -> Path:
    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    ranking = root / "ranking.csv"

    with ranking.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerow(
            [
                1,
                "deck-a",
                90,
                60,
                58,
                62,
                1,
                100,
                1,
                1,
                100,
            ]
        )

    heatmap = root / "heatmap.png"
    heatmap.write_bytes(
        b"\x89PNG\r\n\x1a\nfake"
    )

    manifest = {
        "set": {
            "code": "CRI",
            "name": "Chaos Rising Live window",
        },
        "source": {
            "contains_personal_data": False,
        },
        "outputs": {
            "ranking": {
                "sha256": sha(ranking),
            },
            "heatmap": {
                "sha256": sha(heatmap),
            },
        },
    }

    (root / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    (root / "fragment.md").write_text(
        "TCG Live public fragment\n",
        encoding="utf-8",
    )

    return root


def plan():
    window = {
        "code": "CRI",
        "name": "Chaos Rising Live window",
        "start": "2026-05-21T00:00:00Z",
        "end": "2026-07-16T00:00:00Z",
        "catalog_version": "test-v1",
    }

    return {
        "latest_completed_window": window,
        "state_after_publish": {
            "schema_version": 1,
            "game": "PTCG",
            "format": "STANDARD",
            "platform": "PTCGL",
            "published_completed_window": window,
            "updated_at": "2026-09-06T00:00:00Z",
        },
    }


def test_publication_is_separate_from_pocket(
    tmp_path,
):
    bundle = fake_bundle(
        tmp_path / "bundle"
    )

    state = tmp_path / "tcg-state.json"
    target = (
        tmp_path
        / "public"
        / "tcg-live"
        / "latest-meta"
    )

    result = publish_tcg_live_bundle(
        bundle_dir=bundle,
        plan=plan(),
        state_path=state,
        target_dir=target,
    )

    assert result["dry_run"] is False

    assert {
        path.name
        for path in target.iterdir()
    } == set(PUBLIC_FILES)

    payload = json.loads(
        state.read_text(encoding="utf-8")
    )

    assert (
        payload[
            "published_completed_window"
        ]["code"]
        == "CRI"
    )

    assert not (
        tmp_path
        / "public"
        / "latest-meta"
    ).exists()


def test_publication_dry_run_changes_nothing(
    tmp_path,
):
    bundle = fake_bundle(
        tmp_path / "bundle"
    )

    state = tmp_path / "state.json"
    target = tmp_path / "target"

    publish_tcg_live_bundle(
        bundle_dir=bundle,
        plan=plan(),
        state_path=state,
        target_dir=target,
        dry_run=True,
    )

    assert not state.exists()
    assert not target.exists()
