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
        "game": "PTCG",
        "format": "standard",
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
        "state_before_publish": None,
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

    with publication_lock(state_path=state, target_dir=target) as guard:
        result = publish_tcg_live_bundle(
            bundle_dir=bundle,
            plan=plan(),
            state_path=state,
            target_dir=target,
            _lease=guard,
        )
        guard.mark_clean()

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
    with publication_lock(state_path=state, target_dir=target) as guard:
        publish_tcg_live_bundle(
            bundle_dir=bundle, plan=plan(), state_path=state,
            target_dir=target, _lease=guard,
        )
        guard.mark_clean()
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
        with pytest.raises(PublicationLockedError, match="coordinated"):
            publish_tcg_live_bundle(
                bundle_dir=bundle, plan=plan(), state_path=state, target_dir=target
            )
        with pytest.raises(PublicationLockedError, match="lock exists"):
            with publication_lock(state_path=state, target_dir=target):
                pytest.fail("second coordinator entered")
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
    old_state = {"published_completed_window": {"code": "OLD"}}
    state.write_text(json.dumps(old_state), encoding="utf-8")
    previous_state_bytes = state.read_bytes()
    planned = plan()
    planned["state_before_publish"] = old_state
    original = publisher._atomic_write
    fail_once = [True]

    def interrupted_write(path, data):
        if path.name == "heatmap.png" and fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("simulated local write interruption")
        return original(path, data)

    monkeypatch.setattr(publisher, "_atomic_write", interrupted_write)
    with pytest.raises(RuntimeError, match="simulated local"):
        with publication_lock(state_path=state, target_dir=target) as guard:
            publish_tcg_live_bundle(
                bundle_dir=bundle, plan=planned, state_path=state,
                target_dir=target, _lease=guard,
            )
            guard.mark_clean()
    assert ranking.read_bytes() == b"old public ranking"
    assert state.read_bytes() == previous_state_bytes
    assert publication_lock_path(state).exists()
    assert target.with_name(target.name + ".publication.lock").exists()
    with pytest.raises(PublicationLockedError):
        publish_tcg_live_bundle(
            bundle_dir=bundle, plan=plan(), state_path=state, target_dir=target
        )
    # Simulated operator: after explicit verification of rollback/state only.
    assert ranking.read_bytes() == b"old public ranking"
    assert state.read_bytes() == previous_state_bytes
    publication_lock_path(state).unlink()
    target.with_name(target.name + ".publication.lock").unlink()
    with publication_lock(state_path=state, target_dir=target) as guard:
        publish_tcg_live_bundle(
            bundle_dir=bundle, plan=planned, state_path=state,
            target_dir=target, _lease=guard,
        )
        guard.mark_clean()
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


def test_f08r1_local_stale_plan_a_after_published_b_never_regresses(tmp_path):
    bundle_a = fake_bundle(tmp_path / "bundle-A")
    bundle_b = fake_bundle(tmp_path / "bundle-B")
    state = tmp_path / "state.json"
    target = tmp_path / "public" / "latest-meta"
    first = plan()
    with publication_lock(state_path=state, target_dir=target) as guard:
        publish_tcg_live_bundle(
            bundle_dir=bundle_a, plan=first, state_path=state,
            target_dir=target, _lease=guard,
        )
        guard.mark_clean()
    state_a = json.loads(state.read_text(encoding="utf-8"))
    second = plan()
    second["state_before_publish"] = state_a
    second["state_after_publish"] = dict(state_a)
    second["state_after_publish"]["updated_at"] = "2026-10-23T10:00:00Z"
    # Different output bytes make the regression visible on the public target.
    ranking_b = bundle_b / "ranking.csv"
    ranking_b.write_bytes(ranking_b.read_bytes().replace(b"deck-a", b"deck-b"))
    manifest_b = json.loads((bundle_b / "manifest.json").read_text(encoding="utf-8"))
    manifest_b["outputs"]["ranking"]["sha256"] = sha(ranking_b)
    (bundle_b / "manifest.json").write_text(
        json.dumps(manifest_b), encoding="utf-8"
    )
    with publication_lock(state_path=state, target_dir=target) as guard:
        publish_tcg_live_bundle(
            bundle_dir=bundle_b, plan=second, state_path=state,
            target_dir=target, _lease=guard,
        )
        guard.mark_clean()
    before_state = state.read_bytes()
    before_files = {name: (target / name).read_bytes() for name in PUBLIC_FILES}
    with publication_lock(state_path=state, target_dir=target) as guard:
        with pytest.raises(RuntimeError, match="state changed after planning"):
            publish_tcg_live_bundle(
                bundle_dir=bundle_a, plan=first, state_path=state,
                target_dir=target, _lease=guard,
            )
        guard.mark_clean()  # Preflight only; no file writes occurred.
    assert state.read_bytes() == before_state
    assert all((target / name).read_bytes() == before_files[name] for name in PUBLIC_FILES)


def test_f08r1_local_direct_without_coordinator_denied_no_artifacts(tmp_path):
    bundle = fake_bundle(tmp_path / "bundle")
    state = tmp_path / "state.json"
    target = tmp_path / "public"
    with pytest.raises(PublicationLockedError, match="coordinated"):
        publish_tcg_live_bundle(
            bundle_dir=bundle, plan=plan(), state_path=state,
            target_dir=target,
        )
    assert not state.exists()
    assert not target.exists()
    assert not publication_lock_path(state).exists()


def test_f08r1_one_lease_cannot_write_two_different_release_plans(tmp_path):
    bundle = fake_bundle(tmp_path / "bundle")
    state = tmp_path / "state.json"
    target = tmp_path / "local-public"
    remote = tmp_path / "OneDrive" / "TCG"
    a = plan()
    b = plan()
    b["state_after_publish"]["updated_at"] = "2026-10-24T00:00:00Z"
    with publication_lock(
        state_path=state, target_dir=target, onedrive_target=remote
    ) as lease:
        # Equivalent to the first (OneDrive) writer reserving release A.
        lease.assert_onedrive_target(remote)
        lease.assert_plan(a)
        with pytest.raises(PublicationLockedError, match="same release plan"):
            publish_tcg_live_bundle(
                bundle_dir=bundle, plan=b, state_path=state,
                target_dir=target, _lease=lease,
            )
        assert not state.exists()
        assert not (target / "manifest.json").exists()
        lease.mark_clean()
    assert not publication_lock_path(state).exists()


def test_f08r1_local_rejects_unbound_or_unverifiable_plan(tmp_path):
    bundle = fake_bundle(tmp_path / "bundle")
    state, target = tmp_path / "state.json", tmp_path / "public"
    missing = plan()
    del missing["state_before_publish"]
    with publication_lock(state_path=state, target_dir=target) as lease:
        with pytest.raises(ValueError, match="freshness-bound plan"):
            publish_tcg_live_bundle(
                bundle_dir=bundle, plan=missing, state_path=state,
                target_dir=target, _lease=lease,
            )
        assert not state.exists()
        assert not target.exists()
        lease.mark_clean()


@pytest.mark.parametrize(
    "field,bad", (("game", "POCKET"), ("format", "expanded"))
)
def test_f08r3_local_rejects_semantically_wrong_manifest_before_write(
    tmp_path, field, bad
):
    bundle = fake_bundle(tmp_path / "bundle")
    manifest_file = bundle / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest[field] = bad
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    state, target = tmp_path / "state.json", tmp_path / "public"
    with publication_lock(state_path=state, target_dir=target) as guard:
        with pytest.raises(ValueError, match="snapshot game/format/release"):
            publish_tcg_live_bundle(
                bundle_dir=bundle, plan=plan(), state_path=state,
                target_dir=target, _lease=guard,
            )
        guard.mark_clean()
    assert not state.exists()
    assert not target.exists()


def test_f08r3_local_uses_same_validated_bytes_after_source_mutation(
    tmp_path, monkeypatch
):
    import scripts.tcg_live_publication as publisher

    bundle = fake_bundle(tmp_path / "bundle")
    original_ranking = (bundle / "ranking.csv").read_bytes()
    original_manifest = (bundle / "manifest.json").read_bytes()
    real_freeze = publisher.validated_tcg_snapshot

    def mutated_after_snapshot(source, published_plan):
        validated = real_freeze(source, published_plan)
        (source / "ranking.csv").write_bytes(b"broken after validation")
        (source / "manifest.json").write_bytes(b'{"game":"POCKET"}')
        return validated

    monkeypatch.setattr(publisher, "validated_tcg_snapshot", mutated_after_snapshot)
    state, target = tmp_path / "state.json", tmp_path / "public"
    with publication_lock(state_path=state, target_dir=target) as guard:
        publish_tcg_live_bundle(
            bundle_dir=bundle, plan=plan(),
            state_path=state, target_dir=target, _lease=guard,
        )
        guard.mark_clean()
    assert (target / "ranking.csv").read_bytes() == original_ranking
    assert (target / "manifest.json").read_bytes() == original_manifest
