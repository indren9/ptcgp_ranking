from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.tcg_live_publication_lock import (
    PublicationLockedError,
    publication_lock,
    publication_lock_path,
)

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


def test_f08_direct_publish_success_releases_all_destination_locks(tmp_path):
    bundle = fake_bundle(tmp_path / "bundle")
    state = tmp_path / "state.json"
    target = tmp_path / "public" / "latest-meta"
    publish_tcg_live_bundle(
        bundle_dir=bundle, plan=plan(), state_path=state, target_dir=target
    )
    assert state.is_file()
    assert not publication_lock_path(state).exists()
    assert not target.with_name(target.name + ".publication.lock").exists()


def test_f08_same_state_or_same_local_destination_blocks_before_write(tmp_path):
    bundle = fake_bundle(tmp_path / "bundle")
    state = tmp_path / "state.json"
    target = tmp_path / "public" / "latest-meta"
    other_state = tmp_path / "other-state.json"
    other_target = tmp_path / "another" / "latest-meta"
    with publication_lock(state_path=state, target_dir=target) as first:
        with pytest.raises(PublicationLockedError, match="lock exists"):
            publish_tcg_live_bundle(
                bundle_dir=bundle, plan=plan(), state_path=state, target_dir=target
            )
        with pytest.raises(PublicationLockedError, match="lock exists"):
            with publication_lock(state_path=state, target_dir=other_target):
                pytest.fail("same state acquired twice")
        with pytest.raises(PublicationLockedError, match="lock exists"):
            with publication_lock(state_path=other_state, target_dir=target):
                pytest.fail("same local output acquired twice")
        assert not state.exists()
        assert not (target / "ranking.csv").exists()
        assert not publication_lock_path(other_state).exists()
        first.mark_clean()
    assert not publication_lock_path(state).exists()


def test_f08_different_state_and_local_target_same_onedrive_blocks(tmp_path):
    remote = tmp_path / "OneDrive" / "TCG"
    state_a, state_b = tmp_path / "a.json", tmp_path / "b.json"
    target_a, target_b = tmp_path / "public-a", tmp_path / "public-b"
    with publication_lock(
        state_path=state_a, target_dir=target_a, onedrive_target=remote
    ) as first:
        with pytest.raises(PublicationLockedError, match="lock exists"):
            with publication_lock(
                state_path=state_b, target_dir=target_b, onedrive_target=remote
            ):
                pytest.fail("shared OneDrive writer entered")
        assert not publication_lock_path(state_b).exists()
        first.mark_clean()
    assert not remote.with_name(remote.name + ".publication.lock").exists()


def test_f08_rollback_failure_retains_recovery_locks(tmp_path, monkeypatch):
    import scripts.tcg_live_publication as publisher

    bundle = fake_bundle(tmp_path / "bundle")
    state = tmp_path / "tcg-state.json"
    target = tmp_path / "public" / "latest-meta"
    target.mkdir(parents=True)
    ranking = target / "ranking.csv"
    ranking.write_bytes(b"old public ranking")
    state.write_bytes(b"old state")
    original = publisher._atomic_write
    fail_once = [True]

    def interrupted_write(path, data):
        if path.name == "heatmap.png" and fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("simulated local write interruption")
        return original(path, data)

    monkeypatch.setattr(publisher, "_atomic_write", interrupted_write)
    with pytest.raises(RuntimeError, match="simulated local"):
        publish_tcg_live_bundle(
            bundle_dir=bundle, plan=plan(), state_path=state, target_dir=target
        )
    assert ranking.read_bytes() == b"old public ranking"
    assert state.read_bytes() == b"old state"
    assert publication_lock_path(state).exists()
    assert target.with_name(target.name + ".publication.lock").exists()
    with pytest.raises(PublicationLockedError):
        publish_tcg_live_bundle(
            bundle_dir=bundle, plan=plan(), state_path=state, target_dir=target
        )
    # Simulated operator: after explicit verification of rollback/state only.
    assert ranking.read_bytes() == b"old public ranking"
    assert state.read_bytes() == b"old state"
    publication_lock_path(state).unlink()
    target.with_name(target.name + ".publication.lock").unlink()
    publish_tcg_live_bundle(
        bundle_dir=bundle, plan=plan(), state_path=state, target_dir=target
    )
    assert not publication_lock_path(state).exists()


def test_f08_process_crash_leaves_lock_and_never_autorecovers(tmp_path):
    root = Path(__file__).resolve().parents[1]
    state = tmp_path / "state.json"
    target = tmp_path / "pub"
    child = (
        "import os,sys; from pathlib import Path; "
        "from scripts.tcg_live_publication_lock import publication_lock; "
        "s=Path(sys.argv[1]);t=Path(sys.argv[2]); "
        "ctx=publication_lock(state_path=s,target_dir=t); "
        "ctx.__enter__();os._exit(17)"
    )
    p = subprocess.run(
        [sys.executable, "-c", child, str(state), str(target)],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    assert p.returncode == 17, p.stderr
    assert publication_lock_path(state).exists()
    with pytest.raises(PublicationLockedError, match="lock exists"):
        with publication_lock(state_path=state, target_dir=target):
            pytest.fail("stale owner was automatically replaced")


def test_f08_independent_process_collision_on_same_destination(tmp_path):
    root = Path(__file__).resolve().parents[1]
    state = tmp_path / "state.json"
    target = tmp_path / "shared-public"
    child = (
        "import sys; from pathlib import Path; "
        "from scripts.tcg_live_publication_lock import publication_lock,PublicationLockedError; "
        "s=Path(sys.argv[1]);t=Path(sys.argv[2]); "
        "def_call=lambda:publication_lock(state_path=s,target_dir=t).__enter__(); "
        "try_exec=None; "
        "\ntry:\n def_call()\nexcept PublicationLockedError:\n sys.exit(0)"
        "\nelse:\n sys.exit(3)"
    )
    with publication_lock(state_path=state, target_dir=target) as lease:
        result = subprocess.run(
            [sys.executable, "-c", child, str(state), str(target)],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        lease.mark_clean()
    assert not publication_lock_path(state).exists()
