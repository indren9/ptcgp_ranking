from __future__ import annotations

import base64
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import scripts.automatic_completed_meta_rollover as rollover
from scripts.automatic_completed_meta_rollover import (
    PUBLICATION_ALLOWLIST,
    ReplayEvidence,
    RolloverHooks,
    RolloverLock,
    derive_release_window,
    run_rollover_transaction,
)
from scripts.latest_completed_meta import CatalogEntry, README_END, README_START


NOW = "2026-09-03T12:00:00+00:00"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
RANKING = (
    "Rank,Deck,Score_%,MAS_%,LB_%,BT_%,SE_%,N_eff,Opp_used,Opp_total,Coverage_%\n"
    "1,Deck Alpha,60.0000,55.0000,50.0000,65.0000,2.0000,100,2,2,100.0000\n"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entries() -> list[CatalogEntry]:
    return [
        CatalogEntry("X1", "Set One"),
        CatalogEntry("X2", "Set Two"),
        CatalogEntry("X3", "Set Three"),
    ]


def _state(code: str = "X1", name: str = "Set One") -> dict:
    return {"published_completed_set": {"code": code, "name": name}}


def _write_release_catalog(path: Path) -> Path:
    payload = {
        "catalog_version": "fixture-catalog-v1",
        "source": "fixture",
        "releases": [
            {
                "code": "X1",
                "name": "Set One",
                "release_datetime": "2026-06-01T01:00:00Z",
                "next_release_datetime": "2026-07-01T01:00:00Z",
                "is_current": False,
            },
            {
                "code": "X2",
                "name": "Set Two",
                "release_datetime": "2026-07-01T01:00:00Z",
                "next_release_datetime": "2026-08-01T01:00:00Z",
                "is_current": False,
            },
            {
                "code": "X3",
                "name": "Set Three",
                "release_datetime": "2026-08-01T01:00:00Z",
                "next_release_datetime": None,
                "is_current": True,
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_bundle(path: Path, *, contains_personal_data: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "fragment.md").write_text("## Latest completed meta\n", encoding="utf-8")
    (path / "heatmap.png").write_bytes(PNG_1X1)
    (path / "ranking.csv").write_text(RANKING, encoding="utf-8")
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "set": {"code": "X2", "name": "Set Two"},
                "source": {
                    "acquisition": "Tournament API",
                    "contains_personal_data": contains_personal_data,
                },
                "outputs": {
                    "ranking": {"sha256": _sha(path / "ranking.csv")},
                    "heatmap": {"sha256": _sha(path / "heatmap.png")},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_live_manifest(
    path: Path,
    window,
    *,
    source: str = "Limitless Tournament API",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "run_id": "live-x2",
        "source": source,
        "scope": {
            "game": "POCKET",
            "format": "STANDARD",
            "set_code": "X2",
            "set_name": "Set Two",
            "start": window.start,
            "end": window.end,
            "catalog_version": window.catalog_version,
        },
        "selection": {
            "tournament_ids": ["t-1"],
            "included_count": 1,
            "failures": [],
        },
        "raw": {
            "snapshot_refs": [
                {
                    "payload_type": "details",
                    "tournament_id": "t-1",
                    "snapshot_id": "s-1",
                    "sha256": "a" * 64,
                    "relative_path": "tournaments/t-1/snapshots/s-1/details.json",
                }
            ]
        },
        "normalized": {"hashes": {"participants": "n1", "pairings": "n2"}},
        "aggregation": {"comparable_matches": 10},
        "contracts": {
            "top_meta_decklist": {"sha256": "c1"},
            "matchup_raw": {"sha256": "c2"},
            "dense_score": {"sha256": "c3"},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_replay(tmp_path: Path, live_path: Path, *, normalized_hash: str | None = None) -> ReplayEvidence:
    live = json.loads(live_path.read_text(encoding="utf-8"))
    offline = json.loads(json.dumps(live))
    offline["run_id"] = "offline-x2"
    if normalized_hash is not None:
        offline["normalized"]["hashes"]["participants"] = normalized_hash
    manifest = tmp_path / "offline" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(offline), encoding="utf-8")
    diagnostics = tmp_path / "offline" / "diagnostics.json"
    diagnostics.write_text(
        json.dumps(
            {
                "execution_mode": "offline",
                "replay_run_id": live["run_id"],
                "network_calls": 0,
            }
        ),
        encoding="utf-8",
    )
    source_run = tmp_path / "source-run"
    source_run.mkdir(exist_ok=True)
    return ReplayEvidence(source_run, manifest, diagnostics)


def _public_paths(tmp_path: Path):
    readme = tmp_path / "README.md"
    state = tmp_path / ".github" / "latest-completed-meta-state.json"
    target = tmp_path / "public" / "latest-meta"
    readme.write_text(f"before\n{README_START}\nold\n{README_END}\nafter\n", encoding="utf-8")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(_state()), encoding="utf-8")
    target.mkdir(parents=True, exist_ok=True)
    (target / "ranking.csv").write_bytes(b"old-ranking")
    (target / "heatmap.png").write_bytes(b"old-heatmap")
    (target / "manifest.json").write_bytes(b"old-manifest")
    return readme, state, target


def _snapshot(readme: Path, state: Path, target: Path) -> dict[str, bytes]:
    return {
        "readme": readme.read_bytes(),
        "state": state.read_bytes(),
        "ranking": (target / "ranking.csv").read_bytes(),
        "heatmap": (target / "heatmap.png").read_bytes(),
        "manifest": (target / "manifest.json").read_bytes(),
    }


def _success_hooks(tmp_path: Path, *, restore=None, producer_observer=None) -> tuple[RolloverHooks, list[str]]:
    calls: list[str] = []

    def acquire(plan, window):
        calls.append("acquire")
        return _write_live_manifest(tmp_path / "live" / "manifest.json", window)

    def persist(run_id, manifest_path):
        calls.append("persist")
        assert run_id == "live-x2"
        assert manifest_path.is_file()

    def replay(run_id, plan, window):
        calls.append("replay")
        return _write_replay(tmp_path, tmp_path / "live" / "manifest.json")

    def produce(plan, source_run, manifest_path):
        calls.append("produce")
        if producer_observer is not None:
            producer_observer()
        return _write_bundle(tmp_path / "bundle")

    return (
        RolloverHooks(
            acquire_live=acquire,
            persist_raw=persist,
            replay_offline=replay,
            produce_bundle=produce,
            restore_prior_raw=restore,
        ),
        calls,
    )


def _run(tmp_path: Path, hooks: RolloverHooks, *, state_payload=None):
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    return (
        run_rollover_transaction(
            entries=_entries(),
            state=_state() if state_payload is None else state_payload,
            release_catalog_path=release_catalog,
            hooks=hooks,
            work_dir=tmp_path / "work",
            readme_path=readme,
            state_path=state_path,
            target_dir=target,
            generated_at=NOW,
        ),
        readme,
        state_path,
        target,
    )


# 1
def test_no_new_current_set_is_noop_and_calls_no_stages(tmp_path: Path):
    def explode(*args, **kwargs):
        raise AssertionError("NOOP must not execute rollover stages")

    hooks = RolloverHooks(explode, explode, explode, explode)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    result = run_rollover_transaction(
        entries=_entries(),
        state=_state("X2", "Set Two"),
        release_catalog_path=release_catalog,
        hooks=hooks,
        work_dir=tmp_path / "work",
        readme_path=readme,
        state_path=state_path,
        target_dir=target,
        generated_at=NOW,
    )
    assert result.plan["action"] == "noop"
    assert result.published is False


# 2
def test_new_current_set_derives_penultimate_completed_set(tmp_path: Path):
    hooks, _ = _success_hooks(tmp_path)
    result, *_ = _run(tmp_path, hooks)
    assert result.plan["current_set"]["code"] == "X3"
    assert result.plan["completed_set"]["code"] == "X2"


# 3
def test_acquisition_failure_causes_no_publication(tmp_path: Path):
    def acquire_fail(plan, window):
        raise RuntimeError("acquisition failed")

    hooks = RolloverHooks(acquire_fail, lambda *_: None, lambda *_: None, lambda *_: None)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    before = _snapshot(readme, state_path, target)
    with pytest.raises(RuntimeError, match="acquisition failed"):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )
    assert _snapshot(readme, state_path, target) == before


# 4
def test_producer_failure_causes_no_publication(tmp_path: Path):
    hooks, calls = _success_hooks(tmp_path)

    def producer_fail(plan, source_run, manifest_path):
        calls.append("produce-fail")
        raise RuntimeError("producer failed")

    hooks = RolloverHooks(hooks.acquire_live, hooks.persist_raw, hooks.replay_offline, producer_fail)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    before = _snapshot(readme, state_path, target)
    with pytest.raises(RuntimeError, match="producer failed"):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )
    assert _snapshot(readme, state_path, target) == before


# 5
def test_bundle_validation_failure_causes_no_publication(tmp_path: Path):
    hooks, _ = _success_hooks(tmp_path)

    def invalid_bundle(plan, source_run, manifest_path):
        return _write_bundle(tmp_path / "bundle", contains_personal_data=True)

    hooks = RolloverHooks(hooks.acquire_live, hooks.persist_raw, hooks.replay_offline, invalid_bundle)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    before = _snapshot(readme, state_path, target)
    with pytest.raises(ValueError, match="contains_personal_data=false"):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )
    assert _snapshot(readme, state_path, target) == before


# 6
def test_state_updates_only_after_all_stages_succeed(tmp_path: Path):
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)

    def observe_state():
        assert json.loads(state_path.read_text(encoding="utf-8"))["published_completed_set"]["code"] == "X1"

    hooks, calls = _success_hooks(tmp_path, producer_observer=observe_state)
    result = run_rollover_transaction(
        entries=_entries(), state=_state(), release_catalog_path=release_catalog,
        hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
        state_path=state_path, target_dir=target, generated_at=NOW,
    )
    assert result.published is True
    assert calls == ["acquire", "persist", "replay", "produce"]
    assert json.loads(state_path.read_text(encoding="utf-8"))["published_completed_set"]["code"] == "X2"


# 7
def test_public_files_remain_byte_identical_on_prepublication_failure(tmp_path: Path):
    hooks, _ = _success_hooks(tmp_path)

    def producer_fail(*args):
        raise ValueError("stop before publication")

    hooks = RolloverHooks(hooks.acquire_live, hooks.persist_raw, hooks.replay_offline, producer_fail)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    before = _snapshot(readme, state_path, target)
    with pytest.raises(ValueError):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )
    assert _snapshot(readme, state_path, target) == before


# 8
def test_current_and_completed_sets_are_not_hard_coded(tmp_path: Path):
    source = inspect.getsource(rollover)
    assert "B4a" not in source
    assert '"B4"' not in source
    hooks, _ = _success_hooks(tmp_path)
    result, *_ = _run(tmp_path, hooks)
    assert result.plan["completed_set"]["code"] == "X2"


# 9
def test_exact_release_window_is_completed_release_to_current_release(tmp_path: Path):
    from scripts.latest_completed_meta import build_publication_plan

    plan = build_publication_plan(_entries(), _state(), generated_at=NOW)
    window = derive_release_window(plan, _write_release_catalog(tmp_path / "releases.json"))
    assert window.start == "2026-07-01T01:00:00Z"
    assert window.end == "2026-08-01T01:00:00Z"
    assert window.catalog_version == "fixture-catalog-v1"


# 10
def test_legacy_html_manifest_is_rejected_without_fallback(tmp_path: Path):
    calls: list[str] = []

    def acquire(plan, window):
        calls.append("acquire")
        return _write_live_manifest(
            tmp_path / "live" / "manifest.json",
            window,
            source="Legacy HTML aggregate pages",
        )

    def persist(*args):
        calls.append("persist")

    hooks = RolloverHooks(acquire, persist, lambda *_: None, lambda *_: None)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    with pytest.raises(ValueError, match="Tournament API"):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )
    assert calls == ["acquire"]


# 11
def test_publication_allowlist_excludes_raw_cache_outputs_and_player_data(tmp_path: Path):
    lowered = "\n".join(sorted(PUBLICATION_ALLOWLIST)).casefold()
    assert "data/raw" not in lowered
    assert "cache/" not in lowered
    assert "outputs/" not in lowered
    hooks, _ = _success_hooks(tmp_path)
    _, readme, _, target = _run(tmp_path, hooks)
    public_text = "\n".join(
        [
            readme.read_text(encoding="utf-8"),
            (target / "ranking.csv").read_text(encoding="utf-8"),
            (target / "manifest.json").read_text(encoding="utf-8"),
        ]
    )
    assert "private-player" not in public_text
    assert json.loads((target / "manifest.json").read_text())["source"]["contains_personal_data"] is False


# 12
def test_replay_must_be_deterministic_and_zero_network(tmp_path: Path):
    hooks, _ = _success_hooks(tmp_path)

    def mismatched_replay(run_id, plan, window):
        return _write_replay(
            tmp_path,
            tmp_path / "live" / "manifest.json",
            normalized_hash="different",
        )

    hooks = RolloverHooks(hooks.acquire_live, hooks.persist_raw, mismatched_replay, hooks.produce_bundle)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    with pytest.raises(ValueError, match="normalized hashes differ"):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )


# 13
def test_duplicate_local_rollover_lock_is_rejected(tmp_path: Path):
    lock_path = tmp_path / "rollover.lock"
    with RolloverLock(lock_path):
        with pytest.raises(RuntimeError, match="already running"):
            with RolloverLock(lock_path):
                pass
    assert not lock_path.exists()


# 14
def test_repeated_run_after_success_is_idempotent_noop(tmp_path: Path):
    hooks, _ = _success_hooks(tmp_path)
    first, _, state_path, _ = _run(tmp_path, hooks)
    assert first.published is True
    published_state = json.loads(state_path.read_text(encoding="utf-8"))

    def explode(*args, **kwargs):
        raise AssertionError("idempotent NOOP must not execute stages")

    noop_hooks = RolloverHooks(explode, explode, explode, explode)
    second = run_rollover_transaction(
        entries=_entries(), state=published_state,
        release_catalog_path=tmp_path / "releases.json", hooks=noop_hooks,
        work_dir=tmp_path / "work-2", readme_path=tmp_path / "README.md",
        state_path=state_path, target_dir=tmp_path / "public" / "latest-meta",
        generated_at=NOW,
    )
    assert second.plan["action"] == "noop"
    assert second.published is False


# 15
def test_missing_prior_raw_restore_falls_back_only_to_fresh_canonical_live(tmp_path: Path):
    def missing(plan, window):
        raise FileNotFoundError("no prior canonical raw")

    hooks, calls = _success_hooks(tmp_path, restore=missing)
    result, *_ = _run(tmp_path, hooks)
    assert result.published is True
    assert result.restored_prior_raw is False
    assert result.restore_failure_type == "FileNotFoundError"
    assert calls[0] == "acquire"


# 16
def test_failed_prior_restore_is_safe_and_cold_live_can_continue(tmp_path: Path):
    def failed(plan, window):
        raise OSError("private object store temporarily unavailable")

    hooks, calls = _success_hooks(tmp_path, restore=failed)
    result, *_ = _run(tmp_path, hooks)
    assert result.published is True
    assert result.restored_prior_raw is False
    assert result.restore_failure_type == "OSError"
    assert "persist" in calls


# 17
def test_existing_latest_meta_is_rolled_back_if_publisher_raises_mid_write(tmp_path: Path, monkeypatch):
    hooks, _ = _success_hooks(tmp_path)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    before = _snapshot(readme, state_path, target)

    def broken_publish(**kwargs):
        kwargs["target_dir"].mkdir(parents=True, exist_ok=True)
        (kwargs["target_dir"] / "ranking.csv").write_bytes(b"partial-new")
        kwargs["state_path"].write_bytes(b"partial-state")
        raise OSError("simulated publication write failure")

    monkeypatch.setattr(rollover, "publish_bundle", broken_publish)
    with pytest.raises(OSError, match="publication write failure"):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )
    assert _snapshot(readme, state_path, target) == before


# 18 — added after the private-object-store decision was frozen.
def test_object_store_persistence_failure_blocks_publication(tmp_path: Path):
    hooks, calls = _success_hooks(tmp_path)

    def persist_fail(run_id, manifest_path):
        calls.append("persist-fail")
        raise OSError("private canonical raw persistence failed")

    hooks = RolloverHooks(hooks.acquire_live, persist_fail, hooks.replay_offline, hooks.produce_bundle)
    release_catalog = _write_release_catalog(tmp_path / "releases.json")
    readme, state_path, target = _public_paths(tmp_path)
    before = _snapshot(readme, state_path, target)
    with pytest.raises(OSError, match="raw persistence failed"):
        run_rollover_transaction(
            entries=_entries(), state=_state(), release_catalog_path=release_catalog,
            hooks=hooks, work_dir=tmp_path / "work", readme_path=readme,
            state_path=state_path, target_dir=target, generated_at=NOW,
        )
    assert _snapshot(readme, state_path, target) == before
    assert "replay" not in calls
