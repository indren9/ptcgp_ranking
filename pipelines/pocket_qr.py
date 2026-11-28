"""Pocket QR export R1. Extracted binary layout from verified original prototype.
No acquisition, MARS scoring, or publication side effects at import.
"""
from pathlib import Path
from collections import Counter, defaultdict
import base64, csv, hashlib, json, re, tempfile, os, unicodedata
from .qr_candidate_validation import (QRGap, validate_decklist, validate_filenames, ranked_filename,
    LEGACY_FILENAME_POLICY, RANK_FILENAME_POLICY)
from .qr_selection_r1 import extract, ratio, tie, canon, sha

DB_COMMIT="75dbced2cf909dd300134dbb23a031e22f3e8fe7"
DB_SHA="aa013af8e34ab472f4c2fba5d2e483ff911dd5cb4961422cbab24f34a9a8a362"
PROTOTYPE_SHA="0303440b1b212b36ffd01ab1b1416277525b998d6072e43ff766f8513e7ef23e"
ENERGY={k:i for i,k in enumerate("grass fire water lightning psychic fighting darkness metal".split(),1)}
POLICY="SELECTION_R1"
def normalize_name(s):
    return unicodedata.normalize("NFC",s).strip().casefold().replace("\u2019","'")
def encode(pk,tr,energies):
    if len(pk)+len(tr)!=20 or not 1<=len(energies)<=3 or len(set(energies))!=len(energies):
        raise QRGap("INVALID_PAYLOAD")
    if any(type(e) is not int or e not in ENERGY.values() for e in energies):
        raise QRGap("INVALID_ENERGY_ID")
    for ids in (pk,tr):
        if any(type(i) is not int or not 0<i<6777216 for i in ids) or max(Counter(ids).values(),default=0)>2:
            raise QRGap("INVALID_ENTITY_COUNT")
    return base64.b64encode(bytes([len(tr)])+b''.join((i+10000000).to_bytes(3,'big') for i in tr)+bytes([len(pk)])+b''.join(i.to_bytes(3,'big') for i in pk)+bytes([len(energies),*energies])).decode('ascii')
def decode(s):
    try:
        b=base64.b64decode(s,validate=True); pos=0; sections=[]
        for offset in (10000000,0):
            n=b[pos];pos+=1
            if pos+3*n>len(b): raise ValueError("truncated cards")
            sections.append([int.from_bytes(b[pos+3*k:pos+3*k+3],'big')-offset for k in range(n)])
            pos+=3*n
        n=b[pos];pos+=1; energies=list(b[pos:pos+n]);pos+=n
        if pos!=len(b) or len(energies)!=n: raise ValueError("trailing/truncated payload")
        pk,tr=sections[1],sections[0]
        if encode(pk,tr,energies)!=s: raise ValueError("noncanonical payload")
        return pk,tr,energies
    except (IndexError,ValueError,TypeError) as e: raise QRGap("INVALID_PAYLOAD") from e

class NativeDB:
    def __init__(self,path,expected_sha=DB_SHA):
        data=Path(path).read_bytes()
        if sha(data)!=expected_sha: raise ValueError("native database hash mismatch")
        self.rows=defaultdict(list)
        self.digest=expected_sha
        for row in json.loads(data):
            self.rows[(row["set"],str(row["number"]))].append(row)
    def entry(self,c,group):
        code={"P-A":"PROMO-A","P-B":"PROMO-B","P-C":"PROMO-C"}.get(c["set"],c["set"])
        rows=self.rows.get((code,c["number"]),[])
        if len(rows)!=1: raise QRGap("CARD_UNRESOLVED")
        row=rows[0]
        if not isinstance(c.get("name"),str) or normalize_name(c["name"])!=normalize_name(row["name"]):
            raise QRGap("CARD_NAME_MISMATCH")
        m=re.fullmatch(r"c(PK|TR)_\d+_(\d{6})_\d+_.+\.webp",row.get("image",""))
        if not m or m[1]!=("PK" if group=="pokemon" else "TR"):
            raise QRGap("CARD_GROUP_MISMATCH")
        return int(m[2])
    def validate(self,dl):
        _,energy=validate_decklist(dl,lambda s,n:s+":"+n)
        groups=[]
        for group in ("pokemon","trainer"):
            ids=[]
            for c in dl.get(group,[]):
                ids.extend([self.entry(c,group)]*c["count"])
            groups.append(sorted(ids))
        pk,tr=groups; e=[ENERGY[x] for x in energy]
        # Enforce native entity duplication limit, including alternate prints.
        payload=encode(pk,tr,e)
        return pk,tr,e,payload

def choose(candidates,db):
    ordered=[]; unknown=[]; rejected=[]
    for r in candidates:
        try: ordered.append((ratio(r),tie(r),r))
        except QRGap: unknown.append({**r,"reason":"INVALID_FINISH"})
    ordered.sort(key=lambda t:(-t[0],t[1]))
    if len({key for _,key,_ in ordered})!=len(ordered): raise ValueError("duplicate candidate identity")
    for score,_,r in ordered:
        try: pk,tr,e,payload=db.validate(r["decklist"])
        except QRGap as exc:
            rejected.append({**r,"reason":str(exc)});continue
        return dict(selected=r,ratio=[score.numerator,score.denominator],pokemon_ids=pk,
            trainer_ids=tr,energy_ids=e,payload=payload,higher_or_tie_rejected=rejected,
            unknown_finish=unknown,candidate_count=len(candidates))
    return dict(selected=None,gap="NO_VALID_LIST" if candidates else "NO_CANDIDATES",
        higher_or_tie_rejected=rejected,unknown_finish=unknown,candidate_count=len(candidates))

def write_png(path,payload):
    import qrcode,cv2
    from PIL import Image
    import numpy as np
    # Keep the original automatically chosen mask whenever it roundtrips.
    # If OpenCV cannot read it, try standard QR masks in deterministic order.
    # Never accept a different nonempty payload or bypass pixel validation.
    expected=decode(payload)
    for mask in (None,*range(8)):
        qr=qrcode.QRCode(version=9,error_correction=qrcode.constants.ERROR_CORRECT_H,
                         box_size=10,border=4,mask_pattern=mask)
        qr.add_data(payload);qr.make(fit=False)
        qr.make_image(fill_color="black",back_color="white").save(path)
        with Image.open(path) as im: pixels=np.asarray(im.convert("L"))
        read,_,_=cv2.QRCodeDetector().detectAndDecode(pixels)
        if not read:
            continue
        if read!=payload or decode(read)!=expected:
            raise ValueError("PNG decode mismatch")
        return sha(path.read_bytes())
    raise ValueError("PNG decode mismatch")

def top_rows(ranking_path,identity,filename_policy=RANK_FILENAME_POLICY):
    with Path(ranking_path).open(encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
    top=rows[:10]
    if not top or [int(r["Rank"]) for r in top]!=list(range(1,len(top)+1)): raise ValueError("invalid ranking order")
    ids=[r["Deck"] for r in top]
    if len(set(ids))!=len(ids): raise ValueError("duplicate ranking ids")
    names={x["deck_id"]:x["deck_name"] for x in identity}
    if len(names)!=len(identity) or any(i not in names for i in ids): raise ValueError("missing/ambiguous identity map")
    # Keep legacy sanitization/collision rules, even when ranks distinguish names.
    safe_names=validate_filenames([names[i] for i in ids])
    if filename_policy==LEGACY_FILENAME_POLICY:
        filenames=safe_names
    elif filename_policy==RANK_FILENAME_POLICY:
        filenames=[ranked_filename(int(row["Rank"]),names[i]) for row,i in zip(top,ids)]
    else:
        raise QRGap("UNKNOWN_FILENAME_POLICY")
    return [(i,names[i],f) for i,f in zip(ids,filenames)]

def export_qr(*,ranking_path,identity,raw_root,run_id,fmt,setcode,database,output_root):
    ranking_path=Path(ranking_path); root=Path(output_root); dest=root/"decklists"/"qr"
    ranking_sha=sha(ranking_path.read_bytes())
    db=NativeDB(database)
    top=top_rows(ranking_path,identity)
    pool,proof=extract(Path(raw_root),run_id,fmt,setcode,[i for i,_,_ in top])
    parent=dest.parent; parent.mkdir(parents=True,exist_ok=True)
    # Stage every PNG, decode and validate before installing the directory.
    # Keep previous directories intact in a unique history directory; no deletion of prior artifacts.
    with tempfile.TemporaryDirectory(prefix=".qr-build-",dir=parent) as temp:
        temp=Path(temp); build=temp/"qr";build.mkdir()
        entries=[]
        for rank,(i,name,filename) in enumerate(top,1):
            r=choose(pool[i],db); r.update(rank=rank,archetype=i,display_name=name)
            if r["selected"] is not None:
                r.update(filename=filename,sha256=write_png(build/filename,r["payload"]),status="QR_ROUNDTRIP_PASS")
            else:r["status"]="GAP"
            entries.append(r)
        manifest=dict(schema="pocket_qr_v1",policy=POLICY,filename_policy=RANK_FILENAME_POLICY,tie_break=["tournament_id","player_id","slot"],
            ranking_sha256=ranking_sha,proof=proof,database_commit=DB_COMMIT,database_sha256=db.digest,
            prototype_sha256=PROTOTYPE_SHA,entries=entries,game="POCKET",format=fmt,set_code=setcode)
        (build/"manifest.json").write_bytes(canon(manifest)+b"\n")
        if sha(ranking_path.read_bytes())!=ranking_sha: raise ValueError("ranking changed during QR generation")
        # No-op only if manifest AND every file match; avoid relying on ranking hash alone.
        if dest.exists() and {p.name:sha(p.read_bytes()) for p in dest.iterdir() if p.is_file()}=={p.name:sha(p.read_bytes()) for p in build.iterdir()} and all(p.is_file() for p in dest.iterdir()):
            return dest/"manifest.json"
        archive_current(dest)
        os.replace(build,dest)
    return dest/"manifest.json"


def archive_current(dest):
    """Move rather than delete; never restore a possibly stale bundle as current."""
    dest=Path(dest)
    if not dest.exists() and not dest.is_symlink(): return None
    if dest.is_symlink() or not dest.is_dir(): raise ValueError("unsafe QR directory")
    history=dest.parent/"qr_history"
    history.mkdir(exist_ok=True)
    archive=Path(tempfile.mkdtemp(prefix="previous-",dir=history))/"qr"
    os.replace(dest,archive)
    return archive


def stage_for_result(result,base):
    """Every ranking-producing Pocket run; QR failure never alters MARS results."""
    if tuple(result.paths.outputs.relative_to(result.paths.output_root).parts)[:1] != ("POCKET",): return
    import logging
    from storage.routing import base_for_expansion
    cfg=result.cfg; base=Path(base)
    runroot=base_for_expansion(result.paths.outputs,result.expansion)
    dest=runroot/"decklists"/"qr"
    result.outputs.pop("qr_manifest",None)
    result.diagnostics.pop("qr_manifest_sha256",None)
    result.diagnostics.pop("qr_error",None)
    result.diagnostics.pop("qr_archive_error",None)
    try:
        if not (cfg.get("qr_decklists") or {}).get("enabled",False):
            archive_current(dest)
            result.diagnostics["qr_status"]="DISABLED"
            return
        if result.diagnostics.get("acquisition_source")!="tournament_api": raise ValueError("QR requires canonical tournament API")
        scope=runroot.relative_to(result.paths.output_root).parts
        if len(scope)!=3 or scope[0]!="POCKET": raise ValueError("QR scope mismatch")
        api=(cfg["source"].get("tournament_api") or {})
        raw=Path(api.get("raw_store_root") or "data/raw/limitless_api")
        db=Path((cfg["qr_decklists"]).get("database") or "data/reference/pocket_qr_cards.json")
        m=export_qr(ranking_path=result.outputs["mars_ranking"],identity=result.diagnostics["deck_identity_map"],
            raw_root=base/raw,run_id=result.diagnostics["tournament_api_run_id"],fmt=scope[1],setcode=result.expansion.code,
            database=base/db,output_root=runroot)
        j=json.loads(m.read_bytes())
        result.outputs["qr_manifest"]=m
        result.diagnostics["qr_manifest_sha256"]=sha(m.read_bytes())
        result.diagnostics["qr_status"]="GAP" if any(x["status"]=="GAP" for x in j["entries"]) else "PASS"
    except Exception as exc:
        result.outputs.pop("qr_manifest",None)
        result.diagnostics.pop("qr_manifest_sha256",None)
        result.diagnostics["qr_status"]="ERROR"
        result.diagnostics["qr_error"]=type(exc).__name__
        logging.getLogger("ptcgp").warning("QR stage failed: %s",type(exc).__name__)
        try: archive_current(dest)
        except Exception as archive_exc:
            result.diagnostics["qr_archive_error"]=type(archive_exc).__name__
            logging.getLogger("ptcgp").warning("QR retirement failed; manifest invalidates current QR")
