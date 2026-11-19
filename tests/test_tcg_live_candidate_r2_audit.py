"""C.5 R2 audit: synthetic private bytes, no credentials and no network."""
from copy import deepcopy
import ast
import io
import json
from pathlib import Path
import socket
import subprocess

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts import tcg_live_candidate_r2_audit as audit
from scripts.tcg_live_candidate_receipt import (
    REQUIRED_OUTPUTS, candidate_identity, candidate_workspace, save_verified_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
AUDIT_SHA = 'a' * 40
PRIVATE = 'PRIVATE_TOKEN_https://private-r2.example/secret-deck'


def encoded(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


class FakeStore:
    def __init__(self):
        self.data, self.metadata, self.calls = {}, {}, []

    def add(self, key, data):
        self.data[key] = data
        self.metadata[key] = {'sha256': audit.sha(data), 'size': str(len(data))}

    def head(self, key):
        self.calls.append(('HEAD', key))
        return len(self.data[key]), self.metadata[key]

    def get_bytes(self, key, limit):
        self.calls.append(('GET', key))
        return self.data[key]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network or write attempted')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    from sources.limitless.tournament_api import object_store
    monkeypatch.setattr(object_store.S3ObjectStoreBackend, 'put_bytes', forbidden)
    monkeypatch.setattr(object_store, 'persist_canonical_raw_run', forbidden)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    """Build a 13-file receipt-schema-2 archive with actual CSV and RAW bytes."""
    work = tmp_path / 'original'
    work.mkdir()
    config = tmp_path / 'config.yaml'
    config.write_bytes((ROOT / 'config/tcg.yaml').read_bytes())
    catalog = tmp_path / 'catalog.json'
    catalog.write_bytes((ROOT / 'data/reference/ptcg_live_windows.json').read_bytes())
    cat = json.loads(catalog.read_bytes())
    entry = next(x for x in cat['releases'] if x['code'] == '30C')
    window = dict(code='30C', name=entry['name'], start=entry['release_datetime'],
                  end=entry['next_release_datetime'], catalog_version=cat['catalog_version'])
    identity = candidate_identity(window=window, config_path=config, catalog_path=catalog, revision=audit.CANDIDATE_SHA)
    transaction = candidate_workspace(work_root=work, identity=identity)
    source = transaction / 'source-output/30C'
    source.mkdir(parents=True)
    live_id, offline_id = 'live-synthetic', 'offline-synthetic'
    raw_key = f'limitless-api/v1/runs/{live_id}/manifest.json'
    raw_payloads, refs = {}, []
    for kind in ('details', 'standings', 'pairings'):
        relative = f'tournaments/t1/snapshots/s1/{kind}.json'
        value = {'private': PRIVATE, 'kind': kind}
        raw_payloads[relative] = encoded(value)
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        refs.append(dict(relative_path=relative, sha256=audit.sha(canonical), payload_type=kind,
                         tournament_id='t1', snapshot_id='s1'))
    raw_payloads['tournaments/t1/snapshots/s1/metadata.json'] = encoded(dict(
        schema_version='1', snapshot_id='s1', tournament_id='t1',
        payload_hashes={r['payload_type']: r['sha256'] for r in refs}))
    live = dict(schema_version='1', run_id=live_id, created_at=window['end'],
        acquisition_started_at=window['end'], source='Limitless Tournament API',
        software={'git_revision': audit.CANDIDATE_SHA},
        scope=dict(policy_id='ptcg_tcgl_expansion_window_v1', game='PTCG', format='STANDARD',
                   set_code='30C', start=window['start'], end=window['end'], catalog_version=window['catalog_version']),
        selection=dict(tournament_ids=['t1'], included_count=1, failures=[]), raw={'snapshot_refs': refs},
        normalized={'hashes': {'synthetic': 'b' * 64}}, aggregation={'comparable_matches': 6},
        contracts={}, rate_limit_observations=[])
    replay = deepcopy(live)
    replay['run_id'] = offline_id
    diagnostics = dict(run_id=offline_id, replay_run_id=live_id, execution_mode='offline', network_calls=0)
    live_path = transaction / f'raw-live/runs/{live_id}/manifest.json'
    replay_path = transaction / f'raw-replay/runs/{offline_id}/manifest.json'
    diag_path = replay_path.with_name('diagnostics.json')
    for path, value in ((live_path, live), (replay_path, replay), (diag_path, diagnostics)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded(value))
    score = pd.DataFrame([['A', 'B', 3, 2, 1, 6], ['B', 'A', 2, 3, 1, 6]],
                         columns=['Deck A', 'Deck B', 'W', 'L', 'T', 'N'])
    outputs = {key: source / (key + ('.json' if key == 'run_manifest' else '.csv')) for key in REQUIRED_OUTPUTS}
    score.to_csv(outputs['score_flat'], index=False)
    pd.DataFrame([[np.nan, 60.0], [40.0, np.nan]], index=['A', 'B'], columns=['A', 'B']).to_csv(outputs['wr_matrix'])
    pd.DataFrame([[np.nan, 5.0], [5.0, np.nan]], index=['A', 'B'], columns=['A', 'B']).to_csv(outputs['n_dir_matrix'])
    ranking = pd.DataFrame({'Deck': ['A', 'B'], 'Score_%': [60., 40.],
        'MAS_%': [60., 40.], 'SE_%': [1., 1.], 'LB_%': [58.04, 38.04],
        'N_eff': [5, 5], 'Opp_used': [1, 1], 'Opp_total': [1, 1], 'Coverage_%': [100., 100.]}, index=[1, 2])
    ranking.to_csv(outputs['mars_ranking'])
    run = dict(set={'code': '30C'}, outputs={k: str(v) for k, v in outputs.items()},
               diagnostics=dict(tournament_api_execution_mode='offline', tournament_api_network_calls=0,
                                tournament_api_run_id=offline_id, tournament_api_replay_run_id=live_id, mars_rows=2))
    outputs['run_manifest'].write_bytes(encoded(run))
    receipt_path = save_verified_candidate(work_root=work, identity=identity, live_manifest=live_path,
        replay_manifest=replay_path, replay_diagnostics=diag_path, outputs=outputs, source_run=source,
        live_run_id=live_id, offline_run_id=offline_id, object_manifest_key=raw_key, mars_rows=2)
    receipt = json.loads(receipt_path.read_bytes())
    effective = transaction / 'tcg-live-offline.yaml'
    cfg = yaml.safe_load(config.read_bytes())
    cfg['source']['tournament_api'].update(execution_mode='offline', replay_run_id=live_id,
        raw_store_root=str(transaction / 'raw-replay'), cache_root=str(transaction / 'cache-offline'),
        cache_ttl_min=0, release_catalog=str(ROOT / cfg['source']['tournament_api']['release_catalog']),
        window={'mode': 'code', 'code': '30C'})
    cfg['saving'].update(output_profile='debug', include_time_when_changed=False, filename_prefix_with_set=False)
    cfg['paths']['output_dir'] = str(transaction / 'source-output')
    effective.write_text(yaml.safe_dump(cfg))
    run['config_summary'] = {k: cfg.get(k) or {} for k in ('source', 'top_meta', 'analysis', 'nan_filter', 'saving')}
    outputs['run_manifest'].write_bytes(encoded(run))
    # Refresh receipt after completing its preserved run manifest.
    receipt['files']['run_manifest']['sha256'] = audit.sha(outputs['run_manifest'].read_bytes())
    receipt_path.write_bytes(encoded(receipt))
    report = dict(receipt['report'], candidate_ready=True, candidate_reused=False,
                  published=False, latest_completed_window=window)
    payloads = {'metadata/config.yaml': config.read_bytes(), 'metadata/catalog.json': catalog.read_bytes(),
                'metadata/report.json': encoded(report), 'work/' + receipt_path.relative_to(work).as_posix(): receipt_path.read_bytes(),
                'work/' + effective.relative_to(work).as_posix(): effective.read_bytes()}
    payloads.update({'work/' + row['path']: (work / row['path']).read_bytes() for row in receipt['files'].values()})
    store = FakeStore()
    def rows_for(payloads, prefix=None):
        rows = []
        for relative, data in sorted(payloads.items()):
            digest = audit.sha(data)
            key = prefix + '/' + relative if prefix else f'limitless-api/v1/objects/sha256/{digest[:2]}/{digest}'
            store.add(key, data)
            rows.append(dict(relative_path=relative, object_key=key, sha256=digest, size=len(data)))
        return rows
    manifest = dict(schema_version=1, complete=True, published=False, github_repository='indren9/ptcgp_ranking',
        github_run_id=audit.CANDIDATE_RUN, github_run_attempt=1, software_sha=audit.CANDIDATE_SHA,
        identity=identity, original_work_root=str(work), original_repo_root=str(ROOT),
        receipt_relative_path='work/' + receipt_path.relative_to(work).as_posix(),
        effective_config_relative_path='work/' + effective.relative_to(work).as_posix(),
        canonical_raw_manifest_key=raw_key, canonical_live_run_id=live_id, offline_run_id=offline_id,
        config_sha256=audit.sha(config.read_bytes()), catalog_sha256=audit.sha(catalog.read_bytes()),
        files=rows_for(payloads, audit.PREFIX))
    raw_payloads[f'runs/{live_id}/manifest.json'] = live_path.read_bytes()
    raw_payloads[f'runs/{live_id}/diagnostics.json'] = encoded(dict(discovery_rows=1, discovery_duplicate_rows=0, discovery_oldest=None))
    raw_payloads[f'runs/{live_id}/raw_refs.json'] = encoded(dict(schema_version='1', run_id=live_id,
        tournament_ids=['t1'], raw_refs=refs))
    raw_index = dict(schema_version='1', run_id=live_id, source='Limitless Tournament API',
                     source_manifest_sha256=audit.sha(live_path.read_bytes()), files=rows_for(raw_payloads))
    store.add(raw_key, encoded(raw_index))
    anchors = {'receipt': receipt_path, 'config': config, 'catalog': catalog,
               **{k: outputs[k] for k in audit.EXPECTED_HASHES if k in outputs}}
    monkeypatch.setattr(audit, 'EXPECTED_HASHES', {k: audit.sha(p.read_bytes()) for k, p in anchors.items()})
    def seal():
        store.add(audit.PREFIX + '/manifest.json', encoded(manifest))
        monkeypatch.setattr(audit, 'MANIFEST_SHA', audit.sha(encoded(manifest)))
    seal()
    return dict(store=store, manifest=manifest, seal=seal, payloads=payloads, raw_key=raw_key,
                raw_index=raw_index, receipt=receipt, outputs=outputs, root=tmp_path,
                rows_for=rows_for)


def run(candidate):
    return audit.audit(candidate['store'], audit_sha=AUDIT_SHA, temp_parent=candidate['root'])


def rewrite(candidate, relative, value):
    data = encoded(value)
    row = next(row for row in candidate['manifest']['files'] if row['relative_path'] == relative)
    candidate['store'].add(row['object_key'], data)
    row.update(sha256=audit.sha(data), size=len(data))
    candidate['seal']()


def test_complete_independent_audit(candidate, capsys):
    result = run(candidate)
    assert result['status'] == 1
    assert result['candidate_files'] == 13 and result['raw_files'] == 7
    assert result['candidate_software_sha'] != result['audit_software_sha']
    assert {method for method, _ in candidate['store'].calls} == {'HEAD', 'GET'}
    assert not list(candidate['root'].glob('c5-r2-audit-*'))
    assert PRIVATE not in json.dumps(result)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('field,value', [('complete', False), ('published', True), ('schema_version', 2),
    ('github_run_id', 1), ('github_run_attempt', 2), ('github_repository', 'other/repo'), ('software_sha', 'a' * 40)])
def test_manifest_identity_fail_closed(candidate, field, value):
    candidate['manifest'][field] = value
    candidate['seal']()
    assert run(candidate)['status'] == 2


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'work//x', 'work/./x', 'work/../x',
                                  'work\\x', 'C:/x', 'work/%2e%2e/x', 'work/x\x00', 'work/x/'])
def test_unsafe_inventory_paths(candidate, path):
    candidate['manifest']['files'][0]['relative_path'] = path
    candidate['seal']()
    assert run(candidate)['status'] == 2


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'wrong-key', 'bad-sha', 'bad-size', 'parent-file'])
def test_inventory_invalid(candidate, mutation):
    rows = candidate['manifest']['files']
    if mutation == 'duplicate': rows[-1] = deepcopy(rows[0])
    if mutation == 'missing': rows.pop()
    if mutation == 'wrong-key': rows[0]['object_key'] = 'other/prefix'
    if mutation == 'bad-sha': rows[0]['sha256'] = 'f' * 64
    if mutation == 'bad-size': rows[0]['size'] += 1
    if mutation == 'parent-file':
        rows[0].update(relative_path='work', object_key=audit.PREFIX + '/work')
    candidate['seal']()
    assert run(candidate)['status'] == 2


@pytest.mark.parametrize('where', ['manifest', 'candidate', 'raw-index', 'raw-object'])
@pytest.mark.parametrize('mutation', ['missing', 'corrupt', 'metadata', 'size', 'partial', 'timeout', 'network'])
def test_remote_failures_sanitized(candidate, monkeypatch, capsys, where, mutation):
    store = candidate['store']
    key = {'manifest': audit.PREFIX + '/manifest.json', 'candidate': candidate['manifest']['files'][0]['object_key'],
           'raw-index': candidate['raw_key'], 'raw-object': candidate['raw_index']['files'][0]['object_key']}[where]
    if mutation == 'missing': del store.data[key]
    elif mutation == 'corrupt': store.data[key] += b'x'
    elif mutation == 'metadata': store.metadata[key]['sha256'] = 'f' * 64
    elif mutation == 'size': store.metadata[key]['size'] = '999'
    else:
        get = store.get_bytes
        def broken(requested, limit):
            if requested == key:
                print(PRIVATE)
                if mutation == 'partial': return get(requested, limit)[:-1]
                if mutation == 'timeout': raise TimeoutError(PRIVATE)
                raise ConnectionError(PRIVATE)
            return get(requested, limit)
        monkeypatch.setattr(store, 'get_bytes', broken)
    result = run(candidate)
    assert result['status'] == 2
    assert set(result) == {'schema_version', 'status', 'published', 'candidate_run_id',
                           'candidate_attempt', 'candidate_software_sha'}
    assert PRIVATE not in json.dumps(result)
    assert capsys.readouterr() == ('', '')
    assert not list(candidate['root'].glob('c5-r2-audit-*'))


@pytest.mark.parametrize('mutation', ['schema', 'identity', 'path', 'source-run', 'lineage', 'network'])
def test_receipt_invalid_without_repair(candidate, mutation):
    receipt = candidate['receipt']
    if mutation == 'schema': receipt['schema_version'] = 1
    if mutation == 'identity': receipt['identity']['software_revision'] = 'wrong'
    if mutation == 'path': receipt['files']['score_flat']['path'] = '../escape'
    if mutation == 'source-run': receipt['source_run'] = '../escape'
    if mutation == 'lineage': receipt['report']['offline_run_id'] = receipt['report']['canonical_live_run_id']
    if mutation == 'network': receipt['report']['offline_network_calls'] = 1
    rewrite(candidate, candidate['manifest']['receipt_relative_path'], receipt)
    assert run(candidate)['status'] == 2


@pytest.mark.parametrize('mutation', ['raw-traversal', 'raw-wrong-key', 'raw-missing', 'raw-source-hash'])
def test_raw_index_invalid(candidate, mutation):
    index = candidate['raw_index']
    if mutation == 'raw-traversal': index['files'][0]['relative_path'] = '../escape'
    if mutation == 'raw-wrong-key': index['files'][0]['object_key'] = 'some/other/key'
    if mutation == 'raw-missing': index['files'].pop()
    if mutation == 'raw-source-hash': index['source_manifest_sha256'] = 'f' * 64
    candidate['store'].add(candidate['raw_key'], encoded(index))
    assert run(candidate)['status'] == 2


@pytest.mark.parametrize('limit,value', [('MAX_OBJECT', 1), ('MAX_TOTAL', 1), ('MAX_RAW_FILES', 1), ('MAX_SECONDS', 0)])
def test_resource_limits_fail_closed(candidate, monkeypatch, limit, value):
    monkeypatch.setattr(audit, limit, value)
    assert run(candidate)['status'] == 2


def test_reject_duplicate_json_and_nonfinite():
    for data in (b'{"x": 1, "x": 2}', b'{"x": NaN}', b'{"x": Infinity}'):
        with pytest.raises(audit.AuditFailure): audit.read_json(data)


@pytest.mark.parametrize('field', ['score_flat', 'wr_matrix', 'n_dir_matrix', 'mars_ranking'])
def test_contracts_fail_for_bad_preserved_output(candidate, field):
    files = candidate['outputs']
    if field == 'score_flat':
        df = pd.read_csv(files[field]); df.loc[0, 'N'] += 1; df.to_csv(files[field], index=False)
    else:
        df = pd.read_csv(files[field], index_col=0)
        if field == 'mars_ranking': df.loc[2, 'Deck'] = 'A'
        else: df.iloc[0, 0] = 0
        df.to_csv(files[field])
    with pytest.raises(audit.AuditFailure): audit.check_contracts(files, {'mars_rows': 2})


class FakeClient:
    def __init__(self, data=b'abc', declared=3, status=200, partial=False):
        self.body = io.BytesIO(data)
        self.declared, self.status, self.partial = declared, status, partial
        self.calls = []
    def head_object(self, **kwargs):
        self.calls.append('HEAD')
        return dict(ResponseMetadata={'HTTPStatusCode': self.status}, ContentLength=self.declared, Metadata={})
    def get_object(self, **kwargs):
        self.calls.append('GET')
        result = dict(ResponseMetadata={'HTTPStatusCode': self.status}, ContentLength=self.declared, Body=self.body)
        if self.partial: result['ContentRange'] = 'bytes 0-2/6'
        return result


@pytest.mark.parametrize('data,size,status,partial,ok', [(b'abc', 3, 200, False, True),
    (b'ab', 3, 200, False, False), (b'abcd', 3, 200, False, False),
    (b'abc', 3, 206, True, False), (b'abc', 3, 200, True, False), (b'abc', 4, 200, False, False)])
def test_transport_bounded_closes_body(data, size, status, partial, ok):
    client = FakeClient(data, size, status, partial)
    transport = audit.ReadOnlyR2(client, 'private')
    if ok: assert transport.get_bytes('safe/key', 3) == data
    else:
        with pytest.raises(audit.AuditFailure): transport.get_bytes('safe/key', 3)
    assert client.body.closed and client.calls == ['GET']


def test_transport_and_audit_have_no_write_or_acquisition_calls():
    tree = ast.parse(Path(audit.__file__).read_text())
    prohibited = {'put_object', 'delete_object', 'put_bytes', 'list_objects', 'list_objects_v2',
                  'run_job', 'run_deck_ranking', 'acquire_tournament_api', 'persist_canonical_raw_run',
                  'restore_canonical_raw_run', 'publish_bundle', 'system', 'Popen', 'run'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
            assert name not in prohibited
    assert not {'put_bytes', 'delete', 'list'} & set(vars(audit.ReadOnlyR2))


def workflow():
    return yaml.load((ROOT / '.github/workflows/tests.yml').read_text(), Loader=yaml.BaseLoader)


def test_workflow_modes_secret_isolation_and_retention():
    data = workflow()
    assert data['on']['workflow_dispatch']['inputs']['mode']['options'] == ['test_only', 'c5_30c', 'c5_artifact_audit']
    job = data['jobs']['c5_artifact_audit']
    assert 'needs' not in job
    guard = job['if']
    for clause in ["github.event_name == 'workflow_dispatch'", "inputs.mode == 'c5_artifact_audit'",
                   "github.run_attempt == 1", "inputs.expected_sha == github.sha",
                   "github.actor == 'indren9'", "github.triggering_actor == 'indren9'",
                   "github.repository == 'indren9/ptcgp_ranking'", 'inputs.audit_authorization ==']:
        assert clause in guard
    steps = job['steps']
    secret_steps = [s for s in steps if 'secrets.' in json.dumps(s)]
    assert len(secret_steps) == 1
    secrets = secret_steps[0]
    assert len(secrets['env']) == 5
    assert secrets['if'] == "steps.audit_guard.outputs.authorized == 'true'"
    assert 'scripts.tcg_live_candidate_r2_audit' in secrets['run']
    assert '>/dev/null 2>&1' in secrets['run']
    preflight = next(s for s in steps if s.get('id') == 'audit_guard')
    assert steps.index(preflight) < steps.index(secrets)
    for token in ['WORKFLOW_SHA', 'WORKFLOW_REF', 'GITHUB_REPOSITORY', 'GITHUB_REF', 'GITHUB_ACTOR',
                  'GITHUB_TRIGGERING_ACTOR', 'GITHUB_SHA', 'GITHUB_RUN_ATTEMPT', 'AUDIT_MODE', 'AUDIT_AUTHORIZATION']:
        assert token in preflight['run']
    assert 'c5_authorization' not in json.dumps(job)
    assert 'tcg_live_latest_completed_job' not in json.dumps(job)
    upload = steps[-1]['with']
    assert upload['retention-days'] == '7'
    assert upload['path'].endswith('/c5-audit-safe/audit.json')
    assert '*' not in upload['path']
    assert 'secrets.' not in json.dumps(data.get('env', {}))


def test_workflow_concurrency_separates_all_modes():
    concurrency = workflow()['concurrency']
    assert "inputs.mode == 'c5_artifact_audit' && 'c5-30c-artifact-audit'" in concurrency['group']
    assert "inputs.mode == 'c5_30c' && 'c5-30c-single-candidate'" in concurrency['group']
    assert 'c5_artifact_audit' in concurrency['cancel-in-progress']
    assert 'c5_30c' in concurrency['cancel-in-progress']
    # Original CI and LIVE job bodies must be byte-for-byte unchanged semantically.
    # Hashes of BaseLoader-parsed jobs at immutable CANDIDATE_SHA. The ordinary
    # Actions checkout is shallow, so this test must not require historical Git.
    original_job_hashes = {
        'pytest': '26ee3babcce1cf8a13b86a122c8ff87e425f675004ad5c054d4e859c9e28e218',
        'c5_30c': 'a6ad57f870e86b94ea0cc71f544b9a16876d6f54affeec3a1f36644301af432c',
    }
    for key, expected in original_job_hashes.items():
        assert audit.sha(json.dumps(workflow()['jobs'][key], sort_keys=True).encode()) == expected


def test_workflow_bash_guard_executes_and_rejects_bad_identity(tmp_path):
    import os
    preflight = next(s for s in workflow()['jobs']['c5_artifact_audit']['steps'] if s.get('id') == 'audit_guard')
    # Substitute only git observations, then execute actual shell conditions.
    script = preflight['run'].replace('$(git rev-parse HEAD)', '$GITHUB_SHA').replace('$(git status --porcelain)', '')
    env = dict(os.environ, EXPECTED_SHA=AUDIT_SHA, GITHUB_SHA=AUDIT_SHA, WORKFLOW_SHA=AUDIT_SHA,
        GITHUB_REPOSITORY='indren9/ptcgp_ranking', GITHUB_REF='refs/heads/fix/issue36-b1-tcg-live-boundary-candidate',
        GITHUB_ACTOR='indren9', GITHUB_TRIGGERING_ACTOR='indren9', GITHUB_EVENT_NAME='workflow_dispatch',
        GITHUB_RUN_ATTEMPT='1', AUDIT_MODE='c5_artifact_audit',
        AUDIT_AUTHORIZATION='APPROVED_ONE_C5_ARTIFACT_AUDIT_42523186837_1', GITHUB_OUTPUT=str(tmp_path / 'output'))
    env['WORKFLOW_REF'] = env['GITHUB_REPOSITORY'] + '/.github/workflows/tests.yml@' + env['GITHUB_REF']
    assert subprocess.run(['bash', '-c', script], env=env, capture_output=True).returncode == 0
    for key, value in [('GITHUB_EVENT_NAME', 'push'), ('GITHUB_EVENT_NAME', 'pull_request'),
        ('GITHUB_EVENT_NAME', 'schedule'), ('AUDIT_MODE', 'c5_30c'), ('GITHUB_RUN_ATTEMPT', '2'),
        ('EXPECTED_SHA', audit.CANDIDATE_SHA), ('WORKFLOW_SHA', 'f' * 40), ('WORKFLOW_REF', 'wrong'),
        ('GITHUB_ACTOR', 'other'), ('GITHUB_TRIGGERING_ACTOR', 'other'), ('GITHUB_REF', 'refs/heads/main'),
        ('GITHUB_REPOSITORY', 'other/repo'), ('AUDIT_AUTHORIZATION', 'APPROVED_R2_diagnostics/c5_30c_AND_ONE_LIVE')]:
        assert subprocess.run(['bash', '-c', script], env={**env, key: value}, capture_output=True).returncode != 0


def rebind_file(candidate, field, value, monkeypatch):
    """Reseal synthetic receipt too, so semantic failures pass checksum gates."""
    receipt = candidate['receipt']
    relative = 'work/' + receipt['files'][field]['path']
    rewrite(candidate, relative, value)
    receipt['files'][field]['sha256'] = audit.sha(encoded(value))
    rewrite(candidate, candidate['manifest']['receipt_relative_path'], receipt)
    monkeypatch.setattr(audit, 'EXPECTED_HASHES', {**audit.EXPECTED_HASHES, 'receipt': audit.sha(encoded(receipt))})


@pytest.mark.parametrize('field,mutation', [('replay_manifest', 'selection'), ('replay_manifest', 'scope'),
    ('replay_manifest', 'normalized'), ('replay_manifest', 'revision'), ('replay_manifest', 'raw'),
    ('replay_diagnostics', 'network'), ('replay_diagnostics', 'run'), ('replay_diagnostics', 'live'),
    ('replay_diagnostics', 'mode'), ('run_manifest', 'run'), ('run_manifest', 'path'), ('run_manifest', 'config')])
def test_semantic_lineage_failures(candidate, monkeypatch, field, mutation):
    path = 'work/' + candidate['receipt']['files'][field]['path']
    data = json.loads(candidate['store'].data[audit.PREFIX + '/' + path])
    if mutation == 'selection': data['selection']['included_count'] = 2
    if mutation == 'scope': data['scope']['set_code'] = 'DLR'
    if mutation == 'normalized': data['normalized']['hashes'] = {}
    if mutation == 'revision': data['software']['git_revision'] = AUDIT_SHA
    if mutation == 'raw': data['raw']['snapshot_refs'] = []
    if mutation == 'network': data['network_calls'] = 1
    if mutation == 'run':
        if field == 'run_manifest': data['diagnostics']['tournament_api_run_id'] = 'wrong'
        else: data['run_id'] = 'wrong'
    if mutation == 'live': data['replay_run_id'] = 'wrong'
    if mutation == 'mode': data['execution_mode'] = 'live'
    if mutation == 'path': data['outputs']['score_flat'] = '/some/other/path'
    if mutation == 'config': data['config_summary']['source']['game'] = 'POCKET'
    rebind_file(candidate, field, data, monkeypatch)
    assert run(candidate)['status'] == 2


def test_raw_metadata_semantics_fail_even_with_valid_blob_hash(candidate):
    index = candidate['raw_index']
    row = next(r for r in index['files'] if r['relative_path'].endswith('metadata.json'))
    data = encoded(dict(schema_version='1', snapshot_id='wrong', tournament_id='t1', payload_hashes={}))
    digest = audit.sha(data)
    row.update(sha256=digest, size=len(data), object_key=f'limitless-api/v1/objects/sha256/{digest[:2]}/{digest}')
    candidate['store'].add(row['object_key'], data)
    candidate['store'].add(candidate['raw_key'], encoded(index))
    assert run(candidate)['status'] == 2


def test_changed_effective_config_is_rejected(candidate):
    relative = candidate['manifest']['effective_config_relative_path']
    cfg = yaml.safe_load(candidate['store'].data[audit.PREFIX + '/' + relative])
    cfg['mars']['Z_PENALTY'] = 0
    data = yaml.safe_dump(cfg).encode()
    row = next(r for r in candidate['manifest']['files'] if r['relative_path'] == relative)
    row.update(sha256=audit.sha(data), size=len(data))
    candidate['store'].add(row['object_key'], data)
    candidate['seal']()
    assert run(candidate)['status'] == 2


def test_manifest_hash_anchor_is_not_replaceable(candidate):
    data = encoded({**candidate['manifest'], 'extra': 'anything'})
    candidate['store'].add(audit.PREFIX + '/manifest.json', data)
    assert run(candidate)['status'] == 2


def test_main_fake_sdk_success_and_failure(candidate, monkeypatch, capsys, caplog):
    import boto3
    import logging
    class Client:
        def head_object(self, *, Bucket, Key):
            size, meta = candidate['store'].head(Key)
            return dict(ContentLength=size, Metadata=meta, ResponseMetadata={'HTTPStatusCode': 200})
        def get_object(self, *, Bucket, Key):
            logging.error(PRIVATE)
            data = candidate['store'].get_bytes(Key, audit.MAX_OBJECT)
            return dict(ContentLength=len(data), Body=io.BytesIO(data), ResponseMetadata={'HTTPStatusCode': 200})
    monkeypatch.setattr(boto3, 'client', lambda *a, **kw: Client())
    for name in ('ENDPOINT', 'BUCKET', 'REGION', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY'):
        monkeypatch.setenv('MARS_RAW_S3_' + name, 'synthetic')
    monkeypatch.setenv('GITHUB_SHA', AUDIT_SHA)
    monkeypatch.setenv('RUNNER_TEMP', str(candidate['root']))
    safe = candidate['root'] / 'c5-audit-safe'
    safe.mkdir()
    assert audit.main() == 0
    report = safe / 'audit.json'
    assert json.loads(report.read_text())['status'] == 1
    original = report.read_bytes()
    assert audit.main() == 1  # Exclusive evidence creation, no overwrite.
    assert report.read_bytes() == original
    report.unlink()
    del candidate['store'].data[audit.PREFIX + '/manifest.json']
    assert audit.main() == 1
    assert json.loads(report.read_text())['status'] == 2
    assert PRIVATE not in report.read_text()
    assert capsys.readouterr() == ('', '')
    assert PRIVATE not in caplog.text


def test_new_root_does_not_touch_existing_directories(candidate):
    existing = candidate['root'] / 'c5-r2-audit-existing'
    existing.mkdir()
    sentinel = existing / 'sentinel'
    sentinel.write_text('keep')
    assert run(candidate)['status'] == 1
    assert sentinel.read_text() == 'keep'


def test_syntax_of_all_workflow_shell_steps():
    for job in workflow()['jobs'].values():
        for step in job['steps']:
            if 'run' in step:
                result = subprocess.run(['bash', '-n'], input=step['run'], text=True, capture_output=True)
                assert result.returncode == 0, result.stderr
