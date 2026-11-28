"""Actual API acquisition replay -> MARS pipeline -> private publisher."""
import json
from pathlib import Path
import pytest
import yaml
from pipelines import deck_ranking as pipeline, pocket_qr as qr
from pipelines.limitless_api_acquisition import run_limitless_api_acquisition
from storage.onedrive_publisher import publish_pocket_result, PublicationError
from test_b9_production_integration import FakeClient, REPO_ROOT, CATALOG_PATH, STARTED, NOW, _write_config

def cards():
    db=qr.NativeDB(REPO_ROOT/"data/reference/pocket_qr_cards.json")
    out=[]; seen=set()
    for rows in db.rows.values():
        if len(rows)!=1: continue
        r=rows[0]
        if r["set"]!="A1" or not r.get("image","").startswith("cPK_"): continue
        c=dict(set=r["set"],number=str(r["number"]),name=r["name"],count=2)
        n=db.entry(c,"pokemon")
        if n in seen:continue
        seen.add(n);out.append(c)
        if len(out)==10:return out
    raise AssertionError("fixture cards missing")

class QRClient(FakeClient):
    def __init__(self,energy,swap=False):
        super().__init__()
        self.energy=energy
        self.swap=swap
    def get_tournament_standings(self,tid,**kwargs):
        a,b=("id-1","Deck One"),("id-2","Deck Two")
        if self.swap:a,b=b,a
        rows=self._standings(a,b)
        for row in rows:
            row["decklist"]={"pokemon":cards(),"trainer":[],"energy":self.energy}
        return rows
    def get_tournament_pairings(self,tid,**kwargs):
        return [{"phase":1,"round":1,"table":1,"player1":"p1","player2":"p2","winner":"p1"}]

def test_every_run_actual_pipeline_and_private_publisher(tmp_path,monkeypatch):
    raw=tmp_path/"raw"
    root=tmp_path/"outputs"
    target=tmp_path/"private"
    def seed(rid,energy,swap=False):
        run_limitless_api_acquisition(game="POCKET",format="STANDARD",set_mode="code",set_code="B3b",
            acquisition_started_at=STARTED,execution_mode="live",raw_store_root=raw/rid,release_catalog=CATALOG_PATH,
            client=QRClient(energy,swap),run_id=rid,software_git_revision="fixture",reuse_latest_raw=False,now_fn=lambda:NOW)
    def run(rid,enabled=True):
        p=_write_config(tmp_path,acquisition="tournament_api",api={"execution_mode":"offline","replay_run_id":rid,
            "raw_store_root":str(raw/rid),"cache_root":str(tmp_path/"cache"),"release_catalog":str(CATALOG_PATH)})
        c=yaml.safe_load(p.read_text(encoding="utf-8"))
        c["saving"]["output_profile"]="user"
        c["qr_decklists"]={"enabled":enabled,"database":str(REPO_ROOT/"data/reference/pocket_qr_cards.json")}
        c["mars"]={"BOOT_N":2,"SEED":42}
        p.write_text(yaml.safe_dump(c),encoding="utf-8")
        return pipeline.run_deck_ranking(base_dir=REPO_ROOT,config_path=p,output_dir=root,
            run_scrape=True,run_core=True,run_mars=True,run_heatmap=True,run_report=True,configure_logs=False)
    calls=[]
    orig=qr.write_png
    def counted(path,payload):
        calls.append(payload)
        return orig(path,payload)
    monkeypatch.setattr(qr,"write_png",counted)
    def read(res):
        assert res.diagnostics["tournament_api_network_calls"]==0
        return json.loads(res.outputs["run_manifest"].read_bytes())
    def publish(res):
        p=publish_pocket_result(res,meta_root=target)
        assert p.status=="PASS"
        return Path(p.target_root)
    seed("seed1",["Fire"])
    a=run("seed1")
    m=read(a);assert m["diagnostics"]["qr_status"]=="PASS",m["diagnostics"]
    rank=a.outputs["mars_ranking"].read_bytes(); old=a.outputs["qr_manifest"].read_bytes()
    n=len(calls);assert n==2
    dest=publish(a);active=dest/"decklists/qr"
    assert len(list(active.glob("*.png")))==2
    assert sorted(p.name[:4] for p in active.glob("*.png"))==["01__","02__"]
    b=run("seed1")
    assert b.outputs["mars_ranking"].read_bytes()==rank
    assert len(calls)==2*n
    publish(b)
    seed("seed2",["Water","Fire"])
    c=run("seed2")
    assert c.outputs["mars_ranking"].read_bytes()==rank
    assert len(calls)==3*n
    j=json.loads(c.outputs["qr_manifest"].read_bytes())
    assert all(x["energy_ids"]==[3,2] for x in j["entries"])
    assert j["proof"]["run_id"]!=json.loads(old)["proof"]["run_id"]
    publish(c)
    seed("seed3",["Grass"],swap=True)
    d=run("seed3")
    assert d.outputs["mars_ranking"].read_bytes()!=rank
    publish(d)
    first=json.loads(old)["entries"]
    swapped=json.loads(d.outputs["qr_manifest"].read_bytes())["entries"]
    assert [x["archetype"] for x in swapped]==list(reversed([x["archetype"] for x in first]))
    assert all(x["filename"].startswith(f'{x["rank"]:02d}__') for x in swapped)
    seed("seed4",[])
    gap=run("seed4")
    assert read(gap)["diagnostics"]["qr_status"]=="GAP"
    publish(gap)
    assert {p.name for p in active.iterdir()}=={"manifest.json"}
    e=run("seed1");publish(e)
    def fail(*a,**k):raise RuntimeError("codec fixture failure")
    monkeypatch.setattr(qr,"write_png",fail)
    error=run("seed1")
    assert error.outputs["mars_ranking"].read_bytes()==rank
    assert read(error)["diagnostics"]["qr_status"]=="ERROR"
    assert "qr_manifest" not in error.outputs and not (root/"POCKET/standard/B3b__Everyday_Wonders/decklists/qr").exists()
    publish(error);assert not active.exists()
    monkeypatch.setattr(qr,"write_png",counted)
    f=run("seed1");publish(f)
    disabled=run("seed1",enabled=False)
    assert read(disabled)["diagnostics"]["qr_status"]=="DISABLED"
    publish(disabled);assert not active.exists()
    assert list((active.parent/"qr_history").glob("*/qr/manifest.json"))

def test_nonranking_and_tcg_not_invoked(tmp_path,monkeypatch):
    # Actual dispatch, with ranking stage isolated for TCG.
    from domain.expansions import Expansion
    import pandas as pd
    exp=Expansion(code="CRI",name="Chaos Rising")
    url="https://example.com/decks?game=PTCG&format=standard"
    p=tmp_path/"tcg.yaml"
    p.write_text(yaml.safe_dump({"source":{"game":"PTCG","acquisition":"legacy_html"},"qr_decklists":{"enabled":True}}),encoding="utf-8")
    monkeypatch.setattr(pipeline,"resolve_expansion_and_url_from_config",lambda *a,**k:(exp,url,[]))
    monkeypatch.setattr(pipeline,"_run_mars_stage",lambda **k:({"mars_ranking":pd.DataFrame({"Deck":["x"]})},{},{}))
    def forbidden(*a,**k):raise AssertionError("QR hook called")
    monkeypatch.setattr(qr,"stage_for_result",forbidden)
    res=pipeline.run_deck_ranking(base_dir=tmp_path,config_path=p,run_scrape=False,run_core=False,run_mars=True,configure_logs=False)
    assert "qr_status" not in res.diagnostics
    p.write_text(yaml.safe_dump({"source":{"game":"POCKET","acquisition":"legacy_html"},"qr_decklists":{"enabled":True}}),encoding="utf-8")
    monkeypatch.setattr(pipeline,"resolve_expansion_and_url_from_config",lambda *a,**k:(Expansion(code="C1",name="Dual Fusion"),url.replace("PTCG","POCKET"),[]))
    res=pipeline.run_deck_ranking(base_dir=tmp_path,config_path=p,run_scrape=False,run_core=False,run_mars=False,configure_logs=False)
    assert "qr_status" not in res.diagnostics
