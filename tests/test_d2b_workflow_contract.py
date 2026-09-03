from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import scripts.automatic_completed_meta_rollover as rollover
from scripts.automatic_completed_meta_rollover import RolloverHooks
from scripts.automatic_completed_meta_rollover_job import run_job
from scripts.latest_completed_meta import CatalogEntry


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "latest-completed-meta.yml"
CATALOG_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "update-expansion-catalog.yml"
)


def _workflow() -> dict:
    return yaml.load(
        WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_workflow_uses_workflow_run_and_manual_dispatch():
    data = _workflow()
    triggers = data["on"]
    assert set(triggers) == {"workflow_run", "workflow_dispatch"}
    assert triggers["workflow_run"]["workflows"] == [
        "Update public expansion catalog"
    ]
    assert triggers["workflow_run"]["types"] == ["completed"]


def test_manual_mode_defaults_to_safe_shadow():
    data = _workflow()
    mode = data["on"]["workflow_dispatch"]["inputs"]["mode"]
    assert mode["default"] == "shadow"
    assert mode["options"] == ["shadow", "production"]


def test_workflow_has_serial_non_cancelling_concurrency():
    data = _workflow()
    assert data["concurrency"]["group"] == "latest-completed-meta-rollover"
    assert data["concurrency"]["cancel-in-progress"] == "false"


def test_permissions_are_minimal_by_job():
    data = _workflow()
    assert data["permissions"] == {"contents": "read"}
    assert data["jobs"]["shadow"]["permissions"] == {"contents": "read"}
    assert data["jobs"]["production"]["permissions"] == {
        "contents": "read"
    }


def test_production_requires_successful_catalog_workflow_on_main():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "github.ref == 'refs/heads/main'" in text


def test_production_checkout_is_main_and_captures_base_sha():
    data = _workflow()
    production = data["jobs"]["production"]["steps"]
    checkout = next(
        step for step in production
        if step.get("name") == "Check out current main"
    )
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["fetch-depth"] == "0"
    assert checkout["with"]["token"] == "${{ steps.app-token.outputs.token }}"
    app_token = next(
        step for step in production
        if step.get("name") == "Create publisher token"
    )
    assert app_token["uses"] == "actions/create-github-app-token@v3"
    assert app_token["with"]["app-id"] == (
        "${{ vars.MARS_PUBLISHER_APP_ID }}"
    )
    assert app_token["with"]["private-key"] == (
        "${{ secrets.MARS_PUBLISHER_PRIVATE_KEY }}"
    )
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "git rev-parse HEAD" in text


def test_workflow_has_exact_publication_allowlist():
    text = WORKFLOW.read_text(encoding="utf-8")
    expected = {
        "README.md",
        ".github/latest-completed-meta-state.json",
        "public/latest-meta/ranking.csv",
        "public/latest-meta/heatmap.png",
        "public/latest-meta/manifest.json",
    }
    assert expected == set(rollover.PUBLICATION_ALLOWLIST)
    for path in expected:
        assert path in text


def test_workflow_rejects_tracked_raw_cache_and_outputs():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "git ls-files data/raw cache outputs" in text
    assert "Raw/cache/output data must never be tracked." in text


def test_workflow_uses_no_actions_cache_or_artifact_for_raw():
    text = WORKFLOW.read_text(encoding="utf-8").casefold()
    assert "actions/cache" not in text
    assert "upload-artifact" not in text
    assert "download-artifact" not in text


def test_workflow_uses_only_vendor_neutral_s3_secret_contract():
    text = WORKFLOW.read_text(encoding="utf-8")
    required = {
        "MARS_RAW_S3_ENDPOINT",
        "MARS_RAW_S3_BUCKET",
        "MARS_RAW_S3_REGION",
        "MARS_RAW_S3_ACCESS_KEY_ID",
        "MARS_RAW_S3_SECRET_ACCESS_KEY",
    }
    for name in required:
        assert f"secrets.{name}" in text
    lowered = text.casefold()
    assert "cloudflare" not in lowered
    assert "backblaze" not in lowered
    assert "amazon.com" not in lowered


def test_workflow_has_stale_head_guard_and_no_rebase():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "git fetch origin main --quiet" in text
    assert 'remote_sha="$(git rev-parse origin/main)"' in text
    assert '"$remote_sha" != "$BASE_SHA"' in text
    assert "git rebase" not in text
    assert "git merge" not in text


def test_workflow_runs_full_regression_before_commit():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'pytest -q --basetemp="$RUNNER_TEMP/pytest"' in text
    assert text.index("Run full regression before publication") < text.index(
        "Commit and push one atomic publication"
    )


def test_workflow_has_no_push_trigger_loop():
    data = _workflow()
    assert "push" not in data["on"]
    catalog = yaml.load(
        CATALOG_WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert set(catalog["on"]) == {"schedule", "workflow_dispatch"}


def test_shadow_job_runs_real_d2b4_shadow_script():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python -m scripts.d2b_shadow_rollover" in text


def test_production_job_runs_dedicated_python_adapter():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert (
        "python -m scripts.automatic_completed_meta_rollover_job"
        in text
    )
    assert "--allow-public-write" in text


def test_workflow_never_mentions_legacy_html():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "legacy_html" not in text
    assert "Legacy HTML" not in text


def test_object_store_outage_is_fail_closed_before_acquisition(
    tmp_path: Path,
):
    calls: list[str] = []

    def outage(plan, window):
        raise OSError("private object store unavailable")

    def acquire(*args):
        calls.append("acquire")
        raise AssertionError("acquisition must not run after store outage")

    hooks = RolloverHooks(
        acquire_live=acquire,
        persist_raw=lambda *_: None,
        replay_offline=lambda *_: None,
        produce_bundle=lambda *_: None,
        restore_prior_raw=outage,
    )

    entries = [
        CatalogEntry("X1", "Set One"),
        CatalogEntry("X2", "Set Two"),
        CatalogEntry("X3", "Set Three"),
    ]
    releases = {
        "catalog_version": "fixture-v1",
        "source": "fixture",
        "releases": [
            {
                "code": "X1",
                "name": "Set One",
                "release_datetime": "2026-06-01T00:00:00Z",
                "next_release_datetime": "2026-07-01T00:00:00Z",
                "is_current": False,
            },
            {
                "code": "X2",
                "name": "Set Two",
                "release_datetime": "2026-07-01T00:00:00Z",
                "next_release_datetime": "2026-08-01T00:00:00Z",
                "is_current": False,
            },
            {
                "code": "X3",
                "name": "Set Three",
                "release_datetime": "2026-08-01T00:00:00Z",
                "next_release_datetime": None,
                "is_current": True,
            },
        ],
    }
    release_path = tmp_path / "releases.json"
    release_path.write_text(json.dumps(releases), encoding="utf-8")

    with pytest.raises(OSError, match="object store unavailable"):
        rollover.run_rollover_transaction(
            entries=entries,
            state={
                "published_completed_set": {
                    "code": "X1",
                    "name": "Set One",
                }
            },
            release_catalog_path=release_path,
            hooks=hooks,
            work_dir=tmp_path / "work",
            readme_path=tmp_path / "README.md",
            state_path=tmp_path / "state.json",
            target_dir=tmp_path / "public",
        )
    assert calls == []


def test_production_job_noop_never_requires_object_store_secrets(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    (repo / "public").mkdir(parents=True)
    (repo / ".github").mkdir()
    (repo / "public" / "expansions_pocket_standard.csv").write_text(
        "code,name\nX1,Set One\nX2,Set Two\n",
        encoding="utf-8",
    )
    (repo / ".github" / "latest-completed-meta-state.json").write_text(
        json.dumps(
            {
                "published_completed_set": {
                    "code": "X1",
                    "name": "Set One",
                }
            }
        ),
        encoding="utf-8",
    )

    def explode_backend():
        raise AssertionError("NOOP must not load S3 configuration")

    report = run_job(
        repo_root=repo,
        work_root=tmp_path / "work",
        generated_at="2026-09-03T12:00:00+00:00",
        backend_factory=explode_backend,
    )
    assert report["action"] == "noop"
    assert report["published"] is False
