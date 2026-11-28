import json
from pathlib import Path
import pytest
from pipelines import pocket_qr as qr
from pipelines.qr_candidate_validation import ranked_filename, LEGACY_FILENAME_POLICY, RANK_FILENAME_POLICY
from storage.pocket_qr_publication import validate_bundle
from storage.onedrive_publisher import PublicationError

def test_names_and_safety(tmp_path):
    rank=tmp_path/"ranking.csv"
    rank.write_text("Rank,Deck\n1,a\n2,b\n3,c\n",encoding="utf-8")
    identity=[dict(deck_id=i,deck_name=n) for i,n in zip("abc",["One","Two/Three","Four"])]
    assert [f for _,_,f in qr.top_rows(rank,identity)]==["01__One.png","02__Two_Three.png","03__Four.png"]
    assert qr.top_rows(rank,identity,LEGACY_FILENAME_POLICY)[0][2]=="One.png"
    assert ranked_filename(10,"Ten")=="10__Ten.png"
    for bad in ("CON","  ","x"*256):
        with pytest.raises(qr.QRGap): ranked_filename(1,bad)
    for bad in (0,11,True):
        with pytest.raises(qr.QRGap): ranked_filename(bad,"ok")
    identity[1]["deck_name"]="one"
    with pytest.raises(qr.QRGap,match="FILENAME_COLLISION"):qr.top_rows(rank,identity)
    rank.write_text("Rank,Deck\n1,a\n3,b\n",encoding="utf-8")
    with pytest.raises(ValueError,match="ranking order"):qr.top_rows(rank,identity)

def test_legacy_readback_and_rank_gap_migration(tmp_path,monkeypatch):
    rank=tmp_path/"ranking.csv";rank.write_text("Rank,Deck\n1,a\n2,b\n3,c\n",encoding="utf-8")
    identity=[dict(deck_id=i,deck_name=n) for i,n in zip("abc",["Alpha","Beta","Gamma"])]
    code=qr.encode(list(range(1,21)),[],[1])
    def choose(pool,db):
        if not pool:return dict(selected=None,gap="NO_CANDIDATES")
        return dict(selected={"fixture":True},pokemon_ids=list(range(1,21)),trainer_ids=[],energy_ids=[1],payload=code)
    monkeypatch.setattr(qr,"choose",choose)
    monkeypatch.setattr(qr,"extract",lambda *a:({"a":[1],"b":[],"c":[1]},{"run_id":"r"}))
    db=Path(__file__).resolve().parents[1]/"data/reference/pocket_qr_cards.json"
    kw=dict(ranking_path=rank,identity=identity,raw_root=tmp_path,run_id="r",fmt="standard",setcode="C1",database=db,output_root=tmp_path)
    path=qr.export_qr(**kw)
    j=json.loads(path.read_bytes());assert j["filename_policy"]==RANK_FILENAME_POLICY
    assert [(x["rank"],x.get("filename")) for x in j["entries"]]==[(1,"01__Alpha.png"),(2,None),(3,"03__Gamma.png")]
    check=dict(ranking=rank,format_code="standard",set_code="C1",run_id="r",identity=identity)
    assert len(validate_bundle(path.parent,manifest_hash=qr.sha(path.read_bytes()),require_rank_prefix=True,**check))==3
    # Create a verified old-format fixture, not a production migration.
    j.pop("filename_policy")
    for e in j["entries"]:
        if "filename" in e:
            old=e["filename"];e["filename"]=old[4:];(path.parent/old).rename(path.parent/e["filename"])
    path.write_bytes(qr.canon(j)+b"\n");legacy=path.read_bytes()
    assert len(validate_bundle(path.parent,manifest_hash=qr.sha(legacy),**check))==3
    with pytest.raises(PublicationError,match="rank prefix"):
        validate_bundle(path.parent,manifest_hash=qr.sha(legacy),require_rank_prefix=True,**check)
    new=qr.export_qr(**kw)
    hist=list((path.parent.parent/"qr_history").glob("*/qr/manifest.json"))
    assert len(hist)==1 and hist[0].read_bytes()==legacy
    assert len(validate_bundle(hist[0].parent,manifest_hash=qr.sha(legacy),**check))==3
    assert {p.name for p in new.parent.iterdir()}=={"manifest.json","01__Alpha.png","03__Gamma.png"}
    bad=json.loads(new.read_bytes());bad["entries"][2]["filename"]="02__Gamma.png"
    new.write_bytes(qr.canon(bad)+b"\n")
    with pytest.raises(PublicationError,match="Unexpected QR entry"):
        validate_bundle(new.parent,manifest_hash=qr.sha(new.read_bytes()),**check)


def test_default_private_configs_and_public_rollover_isolation(tmp_path):
    import yaml
    from scripts.automatic_completed_meta_rollover_job import _write_production_config
    from scripts.d2b_shadow_rollover import _write_shadow_config
    repo=Path(__file__).resolve().parents[1]
    for path in ("config/pocket.yaml","config/pocket_wildcard.yaml"):
        assert yaml.safe_load((repo/path).read_bytes())["qr_decklists"]["enabled"] is True
    prod=tmp_path/"prod";prod.mkdir()
    shadow=tmp_path/"shadow";shadow.mkdir()
    common=dict(repo_root=repo,raw_root=tmp_path/"raw",release_catalog=tmp_path/"catalog.json",replay_run_id="fixture")
    a=_write_production_config(work_root=prod,completed_code="C1",**common)
    b=_write_shadow_config(shadow_root=shadow,**common)
    for cfg in (a,b):
        c=yaml.safe_load(cfg.read_bytes())
        assert c["qr_decklists"]["enabled"] is False
        assert c["source"]["tournament_api"]["execution_mode"]=="offline"
