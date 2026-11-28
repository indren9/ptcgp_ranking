from pathlib import Path
import json
import pytest
from pipelines import pocket_qr as qr
from storage.onedrive_publisher import publish_pocket_result, PublicationError
from test_onedrive_publisher import _pocket_result

def test_regeneration_and_private_publication(tmp_path,monkeypatch):
    result,root=_pocket_result(tmp_path)
    database=Path(__file__).resolve().parents[1]/'data/reference/pocket_qr_cards.json'
    db=qr.NativeDB(database)
    cards=[]; seen=set()
    for rows in db.rows.values():
        if len(rows)!=1: continue
        row=rows[0]
        if row["set"]!="A1" or not row.get("image","").startswith("cPK_"): continue
        c=dict(set=row["set"],number=str(row["number"]),name=row["name"],count=2)
        n=db.entry(c,"pokemon")
        if n in seen:continue
        seen.add(n);cards.append(c)
        if len(cards)==10:break
    assert len(cards)==10
    dl=dict(pokemon=cards,trainer=[],energy=["Fire","Water"])
    def evidence(raw,run,fmt,setcode,ids):
        return {i:[dict(tournament_id="t",player_id="p",slot="1",participants=30,placing=1,decklist=dl)] for i in ids},dict(run_id=run)
    monkeypatch.setattr(qr,"extract",evidence)
    ranking=result.outputs["mars_ranking"]; run=result.outputs["run_manifest"]
    result.cfg["qr_decklists"]={"enabled":True}
    target=tmp_path/"canonical"
    def generate(deck,name,runid):
        # Existing valid fixture ranking columns, replace the deck ID only.
        original=ranking.read_text(encoding="utf-8")
        rows=original.splitlines(); cols=rows[0].split(","); values=rows[1].split(",")
        values[cols.index("Deck")]=deck
        ranking.write_text(rows[0]+"\n"+",".join(values)+"\n",encoding="utf-8")
        identity=[dict(deck_id=deck,deck_name=name)]
        path=qr.export_qr(ranking_path=ranking,identity=identity,raw_root=root,run_id=runid,fmt="standard",setcode="B4a",database=database,output_root=root)
        result.outputs["qr_manifest"]=path
        m=json.loads(run.read_bytes());m["outputs"]["qr_manifest"]=str(path)
        m["diagnostics"]=dict(qr_status="PASS",qr_manifest_sha256=qr.sha(path.read_bytes()),tournament_api_run_id=runid,deck_identity_map=identity)
        run.write_text(json.dumps(m),encoding="utf-8")
        return path
    path=generate("deck-a","Alpha","r1")
    a=path.read_bytes()
    first=publish_pocket_result(result,meta_root=target)
    private=Path(first.target_root)/"decklists/qr"
    assert {p.name for p in private.iterdir()}=={"01__Alpha.png","manifest.json"}
    assert generate("deck-a","Alpha","r1").read_bytes()==a
    assert not (path.parent.parent/"qr_history").exists()
    path=generate("deck-b","Beta","r2")
    assert path.read_bytes()!=a
    assert {p.name for p in path.parent.iterdir()}=={"01__Beta.png","manifest.json"}
    history=list((path.parent.parent/"qr_history").glob("*/qr/manifest.json"))
    assert len(history)==1 and history[0].read_bytes()==a
    def fail(phase,item):
        if phase=="after_temp_copy" and item.relative_path.startswith("decklists/qr/"):
            raise RuntimeError("injected QR copy failure")
    degraded=publish_pocket_result(result,meta_root=target,failure_injector=fail)
    assert degraded.status=="PASS" and not private.exists()
    committed=Path(degraded.target_root)/"run/run_manifest_latest.json"
    d=json.loads(committed.read_bytes());assert d["diagnostics"]["qr_status"]=="ERROR"
    assert "qr_manifest_sha256" not in d["diagnostics"] and "qr_manifest" not in d["outputs"]
    assert (Path(degraded.target_root)/"rankings/mars/mars_ranking_latest.csv").read_bytes()==ranking.read_bytes()
    second=publish_pocket_result(result,meta_root=target)
    assert second.published[-1]=="run/run_manifest_latest.json"
    assert {p.name for p in private.iterdir()}=={"01__Beta.png","manifest.json"}
    history=list((private.parent/"qr_history").glob("*/qr/manifest.json"))
    assert any(p.read_bytes()==a for p in history)
    assert not publish_pocket_result(result,meta_root=target).published
    path=generate("deck-c","Gamma","r3")
    def fail_core(phase,item):
        if phase=="after_canonical_replace" and item.kind=="ranking_csv":raise RuntimeError("injected core failure")
    with pytest.raises(PublicationError,match="injected core failure"):
        publish_pocket_result(result,meta_root=target,failure_injector=fail_core)
    assert not private.exists()  # old rank already replaced; never restore Beta
    assert publish_pocket_result(result,meta_root=target).status=="PASS"
    assert {p.name for p in private.iterdir()}=={"01__Gamma.png","manifest.json"}
    (path.parent/"01__Gamma.png").write_bytes(b"corrupt")
    degraded=publish_pocket_result(result,meta_root=target)
    assert degraded.status=="PASS" and not private.exists()
    assert json.loads(committed.read_bytes())["diagnostics"]["qr_status"]=="ERROR"
    assert json.loads(run.read_bytes())["diagnostics"]["qr_manifest_sha256"]==qr.sha(path.read_bytes())
