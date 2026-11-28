import ast, base64, json
from pathlib import Path
import pytest
from pipelines.pocket_qr import encode, decode, write_png, NativeDB, DB_SHA, QRGap, choose, top_rows

def test_original_golden_vectors():
    fixture=Path(__file__).resolve().parent/'fixtures/pocket_qr_prototype_vectors.json'
    data=json.loads(fixture.read_bytes())
    assert data['prototype_sha256']=="0303440b1b212b36ffd01ab1b1416277525b998d6072e43ff766f8513e7ef23e"
    assert len(data['vectors'])==4
    for v in data['vectors']:
        pk,tr,e=v['pokemon_ids'],v['trainer_ids'],v['energy_ids']
        assert encode(pk,tr,e)==v['payload']
        assert decode(v['payload'])==(pk,tr,e)

@pytest.mark.parametrize("energy",[[2],[5,3],[4,1,7]])
def test_energy_png(tmp_path,energy):
    pk=[i for i in range(1,11) for _ in range(2)];tr=[]
    code=encode(pk,tr,energy);assert decode(code)==(pk,tr,energy)
    assert len(write_png(tmp_path/'test.png',code))==64

@pytest.mark.parametrize("energy",[[],[2,2],[2,10],[1,2,3,4]])
def test_bad_energy(energy):
    with pytest.raises(QRGap):encode(list(range(1,21)),[],energy)

def test_malformed():
    with pytest.raises(QRGap):decode("AAAA")
    with pytest.raises(QRGap):encode([1]*20,[],[1])

def test_db():
    db=NativeDB(Path(__file__).resolve().parents[1]/'data/reference/pocket_qr_cards.json')
    assert db.digest==DB_SHA
    assert db.entry(dict(set="A1",number="1",name="Bulbasaur"),"pokemon")==10
    assert db.entry(dict(set="P-A",number="1",name="Potion"),"trainer")==10
    with pytest.raises(QRGap,match="CARD_NAME_MISMATCH"):db.entry(dict(set="A1",number="1",name="wrong"),"pokemon")
    with pytest.raises(QRGap,match="CARD_GROUP_MISMATCH"):db.entry(dict(set="A1",number="1",name="Bulbasaur"),"trainer")
    with pytest.raises(QRGap,match="CARD_UNRESOLVED"):db.entry(dict(set="ZZ",number="1",name="Bulbasaur"),"pokemon")

def test_rank_policy():
    class DB:
        def validate(self,dl):
            if dl is None:raise QRGap("MISSING_DECKLIST")
            return [],[],[1],"ok"
    def row(n,p,tid,dl=True):return dict(participants=n,placing=p,tournament_id=tid,player_id="p",slot="1",decklist=dl)
    a,b,c,d=row(300,1,"a",None),row(100,2,"b"),row(50,1,"c"),row(100,None,"d")
    s=choose([d,c,a,b],DB())
    assert s["selected"]==b and s["higher_or_tie_rejected"]==[{**a,"reason":"MISSING_DECKLIST"}]
    assert s["unknown_finish"]==[{**d,"reason":"INVALID_FINISH"}]
    assert choose([b,a,c,d],DB())==s
