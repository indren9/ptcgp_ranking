"""MARS P2 WP3: Pocket-style TCG private set layout (offline fixtures only)."""
from __future__ import annotations

from datetime import UTC, datetime
import base64
import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.latest_completed_meta import PUBLIC_RANKING_COLUMNS
from storage import tcg_private_set_layout as route

ROOT=Path(r"C:\dev\Pokemon\ptcgp-p2-combined-wp2-ir02-r1-20261126")
CATALOG=ROOT/"data/reference/ptcg_live_windows.json"
RUN=datetime(2026,11,26,9,tzinfo=UTC)
PNG=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==")

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def bundle(path, window, label="Synthetic Deck"):
    path.mkdir(parents=True,exist_ok=True)
    with (path/"ranking.csv").open("w",encoding="utf-8",newline="") as f:
        writer=csv.writer(f)
        writer.writerow(PUBLIC_RANKING_COLUMNS)
        writer.writerow([1,label,70,70,70,70,70,9,2,2,100])
    (path/"heatmap.png").write_bytes(PNG)
    (path/"fragment.md").write_text("## Synthetic offline only\n",encoding="utf-8")
    manifest={
        "schema_version":1,"game":"PTCG","format":"standard",
        "set":{"code":window["code"],"name":window["name"]},
        "snapshot":{"collection_window":{"start":window["start"],"end":window["end"]}},
        "source":{"provider":"Limitless TCG","acquisition":"Tournament API",
                  "contains_personal_data":False,
                  "tournament_api":{"catalog_version":window["catalog_version"]}},
        "outputs":{"ranking":{"sha256":sha(path/"ranking.csv")},
                   "heatmap":{"sha256":sha(path/"heatmap.png")}},
    }
    (path/"manifest.json").write_text(json.dumps(manifest,sort_keys=True),encoding="utf-8")
    return path

def wanted(root):
    return route.plan_set_publication(catalog_path=CATALOG,root=root,acquisition_started_at=RUN)

def older(window):
    return {**window,"code":"CRI","name":"Chaos Rising Live window",
            "start":"2026-05-21T17:00:00Z","end":"2026-07-16T17:00:00Z"}

def historic(root,window,source):
    target=route.set_path(root,window)
    target.mkdir(parents=True,exist_ok=False)
    for kind,legacy in (("ranking","ranking.csv"),("heatmap","heatmap.png")):
        dest=target/route.FILES[kind]
        dest.parent.mkdir(parents=True)
        dest.write_bytes((source/legacy).read_bytes())
    m=json.loads((source/"manifest.json").read_text(encoding="utf-8"))
    for key in ("ranking","heatmap"):
        m["outputs"][key]["path"]=route.FILES[key]
    path=target/route.FILES["manifest"]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(m,sort_keys=True),encoding="utf-8")
    return target

def test_pocket_style_paths_exact_and_real_publish(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    plan=wanted(root)
    source=bundle(tmp_path/"source",plan["latest_completed_window"])
    response=route.publish_set_bundle(bundle_dir=source,plan=plan,root=root)
    path=Path(response.target_root)
    assert response.status=="PASS"
    assert path.parent==root/"standard"
    assert path.name.startswith("30C__")
    assert all((path/name).is_file() for name in route.FILES.values())
    assert not (root/"ranking.csv").exists()
    assert route.latest_completed(root)[0]==path
    assert wanted(root)["action"]=="noop"
    assert response.new_sha256["ranking"]==sha(path/route.FILES["ranking"])

def test_prev_set_retained_and_latest_by_end(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    latest=wanted(root)["latest_completed_window"]
    old=older(latest)
    source_old=bundle(tmp_path/"old",old,label="Older")
    previous=historic(root,old,source_old)
    prior_sha={x:sha(previous/y) for x,y in route.FILES.items()}
    plan=wanted(root)
    assert plan["action"]=="publish_required"
    src=bundle(tmp_path/"new",plan["latest_completed_window"])
    done=route.publish_set_bundle(bundle_dir=src,plan=plan,root=root)
    assert route.latest_completed(root)[0]==Path(done.target_root)
    assert {x:sha(previous/y) for x,y in route.FILES.items()}==prior_sha
    assert wanted(root)["action"]=="noop"

def test_source_names_preserve_pocket_convention(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    plan=wanted(root)
    src=bundle(tmp_path/"bundle",plan["latest_completed_window"])
    response=route.publish_set_bundle(bundle_dir=src,plan=plan,root=root)
    target=Path(response.target_root)
    m=json.loads((target/route.FILES["manifest"]).read_text(encoding="utf-8"))
    assert m["outputs"]["ranking"]["path"]=="rankings/mars/mars_ranking_latest.csv"
    assert m["outputs"]["heatmap"]["path"]=="matrices/heatmaps/wr_heatmap_latest.png"
    assert not (target/"ranking.csv").exists()
    assert not (target/"heatmap.png").exists()

def test_old_flat_bundle_does_not_impersonate_new_layout(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    flat=bundle(root,older(wanted(tmp_path/"other")["latest_completed_window"]))
    assert (flat/"manifest.json").is_file()
    assert route.latest_completed(root) is None
    assert wanted(root)["action"]=="publish_required"

def test_stale_prior_manifest_rejected_under_lock(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    last=wanted(root)["latest_completed_window"]
    old=older(last)
    previous=historic(root,old,bundle(tmp_path/"old",old))
    plan=wanted(root)
    manifest=previous/route.FILES["manifest"]
    with manifest.open("a",encoding="utf-8") as file:
        file.write("\n")
    src=bundle(tmp_path/"new",plan["latest_completed_window"])
    with pytest.raises(route.LayoutError,match="previous set manifest changed"):
        route.publish_set_bundle(bundle_dir=src,plan=plan,root=root)
    assert not route.set_path(root,last).exists()

def test_precommit_failure_is_not_visible_to_reader(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    latest=wanted(root)["latest_completed_window"]
    old=older(latest)
    prior=historic(root,old,bundle(tmp_path/"old",old))
    plan=wanted(root)
    src=bundle(tmp_path/"new",latest)
    def fail(phase, _name):
        if phase=="before_commit":
            raise RuntimeError("simulated failure before single directory rename")
    with pytest.raises(RuntimeError,match="simulated failure"):
        route.publish_set_bundle(bundle_dir=src,plan=plan,root=root,failure_injector=fail)
    assert not route.set_path(root,latest).exists()
    with pytest.raises(route.LayoutError,match="incomplete staging"):
        route.latest_completed(root)
    assert all((prior/name).exists() for name in route.FILES.values())

def test_lock_exclusive_and_no_pocket_side_effects(tmp_path):
    base=tmp_path/"Pokemon"
    pocket=base/"PTCGP"/"Meta"/"POCKET"/"standard"/"C1__Dual_Fusion"
    pocket.mkdir(parents=True)
    sentinel=pocket/"keep.txt"
    sentinel.write_text("unchanged")
    oldsha=sha(sentinel)
    root=base/"TCG"/"Ranking"
    plan=wanted(root)
    src=bundle(tmp_path/"source",plan["latest_completed_window"])
    root.mkdir(parents=True)
    lock=root/".p2-writer-lock"
    lock.write_text("reserved")
    with pytest.raises(route.LayoutError,match="another writer"):
        route.publish_set_bundle(bundle_dir=src,plan=plan,root=root)
    assert lock.exists()
    assert sha(sentinel)==oldsha
    assert not route.set_path(root,plan["latest_completed_window"]).exists()

def test_corrupt_previous_fails_closed(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    latest=wanted(root)["latest_completed_window"]
    old=older(latest)
    previous=historic(root,old,bundle(tmp_path/"old",old))
    (previous/route.FILES["ranking"]).write_bytes(b"tampered")
    with pytest.raises(route.LayoutError,match="hash or output path mismatch"):
        wanted(root)

def test_same_code_lineage_change_is_rejected(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    latest=wanted(root)["latest_completed_window"]
    drift={**latest,"end":"2026-10-05T17:00:00Z"}
    historic(root,drift,bundle(tmp_path/"old",drift))
    with pytest.raises(route.LayoutError,match="same completed code"):
        wanted(root)

def test_set_name_traversal_and_tmp_rejected(tmp_path):
    root=tmp_path/"TCG"/"Ranking"
    for code in ("TMP-BAD", "../ESCAPE", ""):
        with pytest.raises(route.LayoutError):
            route.set_path(root,{"code":code,"name":"Synthetic"})

def test_no_real_onedrive_or_public_state(tmp_path):
    root=tmp_path/"isolated"/"TCG"/"Ranking"
    plan=wanted(root)
    src=bundle(tmp_path/"source",plan["latest_completed_window"])
    res=route.publish_set_bundle(bundle_dir=src,plan=plan,root=root)
    assert "OneDrive" not in res.target_root
    assert "public" not in str(Path(res.target_root).parts).lower()


def test_private_job_uses_verified_set_layout_not_public_state(tmp_path, monkeypatch):
    from scripts import tcg_live_latest_completed_job as job
    root=tmp_path/"TCG"/"Ranking"
    current=wanted(root)["latest_completed_window"]
    historic(root,current,bundle(tmp_path/"completed",current))
    bad_state=tmp_path/"broken-public-state.json"
    bad_state.write_text("{broken",encoding="utf-8")
    original=route.ranking_root
    monkeypatch.setattr(route,"ranking_root",lambda inp=None: root if inp is None else original(inp))
    result=job.run_job(
        repo_root=ROOT,work_root=tmp_path/"job-scratch",
        state_path=bad_state, acquisition_started_at=RUN,
        publish_private=True,
        backend_factory=lambda:pytest.fail("NOOP may not fetch TCG API"),
    )
    assert result["action"]=="noop"
    assert result["network_calls"]==0
    assert result["published"] is False


def test_default_ranking_root_is_tcg_not_pocket():
    root=route.ranking_root()
    assert root.parts[-3:]==("Pokemon","TCG","Ranking")
    assert "PTCGP" not in root.parts[-3:]
