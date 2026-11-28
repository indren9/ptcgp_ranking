"""QR selection R1, isolated candidate. No production writes or encoder."""
from fractions import Fraction
from collections import defaultdict
import hashlib, json
from pathlib import Path
from .qr_candidate_validation import QRGap

def canon(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(b): return hashlib.sha256(b).hexdigest()
def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def check_ref(root,ref):
    p=(root/ref['relative_path']).resolve()
    if not p.is_relative_to(root.resolve()): raise ValueError('unsafe raw ref')
    v=load(p)
    if sha(canon(v))!=ref['sha256']: raise ValueError('raw hash mismatch')
    return v
def ratio(row):
    n,p=row['participants'],row['placing']
    if type(n) is not int or type(p) is not int or not 1<=p<=n:
        raise QRGap('INVALID_FINISH')
    return Fraction(n,p)
def tie(row):
    return (row['tournament_id'],row['player_id'],row['slot'])
def extract(root,run_id,fmt,setcode,top):
    if Path(run_id).name!=run_id: raise ValueError('invalid run id')
    run=root/'runs'/run_id
    man=load(run/'manifest.json'); refs=load(run/'raw_refs.json')
    if man['run_id']!=run_id or refs['run_id']!=run_id: raise ValueError('run id mismatch')
    scope=man['scope']
    if (scope['game'],scope['format'].casefold(),scope['set_code'])!=('POCKET',fmt,setcode):
        raise ValueError('scope mismatch')
    if sorted(map(canon,man['raw']['snapshot_refs']))!=sorted(map(canon,refs['raw_refs'])): raise ValueError('ref mismatch')
    ids=refs['tournament_ids']
    if set(ids)!=set(man['selection']['tournament_ids']) or len(set(ids))!=len(ids):
        raise ValueError('tournament selection mismatch')
    pairs=defaultdict(dict)
    for ref in refs['raw_refs']:
        kind=ref['payload_type']
        if kind not in ('details','standings'): continue
        tid=ref['tournament_id']
        if kind in pairs[tid]: raise ValueError('duplicate raw ref')
        pairs[tid][kind]=ref
    if set(pairs)!=set(ids) or any(set(x)!= {'details','standings'} for x in pairs.values()):
        raise ValueError('missing raw evidence')
    candidates={x:[] for x in top}
    for tid,parts in sorted(pairs.items()):
        d=check_ref(root,parts['details']); standings=check_ref(root,parts['standings'])
        if d['id']!=tid or d['game']!='POCKET': raise ValueError('tournament identity mismatch')
        n=d.get('players')
        for row in standings:
            if isinstance(row.get('decks'),dict):
                decks=row['decks']; lists=row.get('decklists') or {}
            elif isinstance(row.get('deck'),dict):
                decks={'single':row['deck']}; lists={'single':row.get('decklist')}
            else: continue
            if not isinstance(lists,dict): raise ValueError('invalid list mapping')
            for slot,deck in decks.items():
                if not isinstance(deck,dict) or deck.get('id') not in candidates: continue
                player=row.get('player')
                if not isinstance(player,(str,int)) or not str(player): raise ValueError('missing player identity')
                candidates[deck['id']].append(dict(tournament_id=tid,player_id=str(player),slot=str(slot),
                    participants=n,placing=row.get('placing'),decklist=lists.get(slot),
                    standings_sha256=parts['standings']['sha256'],details_sha256=parts['details']['sha256']))
    return candidates,dict(scope=scope,run_id=run_id,verified_refs=2*len(ids),
        raw_refs_sha256=sha((run/'raw_refs.json').read_bytes()),acquisition_manifest_sha256=sha((run/'manifest.json').read_bytes()))
