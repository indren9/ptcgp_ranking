"""Pocket-only private publication contract for QR bundles."""
from pathlib import Path
import csv,json,os,tempfile
from pipelines.pocket_qr import POLICY,DB_SHA,PROTOTYPE_SHA,decode,top_rows
from pipelines.qr_candidate_validation import LEGACY_FILENAME_POLICY, RANK_FILENAME_POLICY
from .onedrive_publisher import PublicationError, PublicationArtifact, sha256_file

QR_MANIFEST_PATH="decklists/qr/manifest.json"

def validate_bundle(qr_dir, *, ranking, format_code, set_code, manifest_hash, run_id, identity, require_rank_prefix=False):
    root=Path(qr_dir)
    path=root/"manifest.json"
    if path.is_symlink() or not path.is_file() or sha256_file(path)!=manifest_hash:
        raise PublicationError("QR manifest hash mismatch")
    j=json.loads(path.read_bytes())
    if (j.get("schema"),j.get("policy"),j.get("game"),j.get("format"),j.get("set_code"))!=("pocket_qr_v1",POLICY,"POCKET",format_code,set_code):
        raise PublicationError("QR manifest identity mismatch")
    if j.get("ranking_sha256")!=sha256_file(ranking) or j.get("proof",{}).get("run_id")!=run_id:
        raise PublicationError("QR ranking/RAW binding mismatch")
    if j.get("database_sha256")!=DB_SHA or j.get("prototype_sha256")!=PROTOTYPE_SHA:
        raise PublicationError("QR codec/database provenance mismatch")
    filename_policy=j.get("filename_policy",LEGACY_FILENAME_POLICY)
    if filename_policy not in {LEGACY_FILENAME_POLICY,RANK_FILENAME_POLICY}:
        raise PublicationError("Unknown QR filename policy")
    if require_rank_prefix and filename_policy!=RANK_FILENAME_POLICY:
        raise PublicationError("New QR publication requires rank prefix")
    top=top_rows(ranking,identity,filename_policy); entries=j.get("entries",[])
    if len(entries)!=len(top): raise PublicationError("QR entry count mismatch")
    expected={"manifest.json"}
    artifacts=[]
    for rank,((archetype,name,filename),entry) in enumerate(zip(top,entries),1):
        if (entry.get("rank"),entry.get("archetype"),entry.get("display_name"))!=(rank,archetype,name):
            raise PublicationError("QR rank or identity mismatch")
        if entry.get("status")=="GAP":
            if entry.get("selected") is not None or entry.get("filename") or entry.get("sha256") or not entry.get("gap"):
                raise PublicationError("Invalid QR GAP")
            continue
        if entry.get("status")!="QR_ROUNDTRIP_PASS" or entry.get("filename")!=filename:
            raise PublicationError("Unexpected QR entry")
        if decode(entry["payload"])!=(entry["pokemon_ids"],entry["trainer_ids"],entry["energy_ids"]):
            raise PublicationError("QR payload mismatch")
        p=root/filename
        if p.is_symlink() or not p.is_file() or sha256_file(p)!=entry.get("sha256"):
            raise PublicationError("QR PNG hash mismatch")
        expected.add(filename)
        artifacts.append(PublicationArtifact(p,"decklists/qr/"+filename,"png"))
    if {p.name for p in root.iterdir()}!=expected:
        raise PublicationError("Unexpected QR directory contents")
    artifacts.append(PublicationArtifact(path,QR_MANIFEST_PATH,"qr_manifest"))
    return artifacts

def prepare_qr_publication(result,source_run,manifest,format_code,set_code):
    source=result.outputs.get("qr_manifest")
    diag=manifest.get("diagnostics",{})
    expected=diag.get("qr_manifest_sha256")
    status=diag.get("qr_status")
    if source is None and expected is None and status not in {"PASS","GAP"}:
        return []
    path=source_run/"decklists"/"qr"/"manifest.json"
    if status not in {"PASS","GAP"} or not source or Path(source).resolve()!=path.resolve() or not expected:
        raise PublicationError("Missing/inconsistent QR output")
    if manifest.get("outputs",{}).get("qr_manifest")!=str(source):
        raise PublicationError("Run manifest QR path mismatch")
    return validate_bundle(path.parent,ranking=Path(result.outputs["mars_ranking"]),format_code=format_code,set_code=set_code,
        manifest_hash=expected,run_id=diag["tournament_api_run_id"],identity=diag["deck_identity_map"],require_rank_prefix=True)
