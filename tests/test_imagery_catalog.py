"""Only the changed reuse/authorization boundary; no model or scientific recomputation."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forestry_agent.app import create_app
from forestry_agent.config import Config, ModelProfile
from forestry_agent.imagery_catalog import input_identity
from forestry_agent.resources import file_hash
from forestry_agent.science_identity import science_run_identity
from forestry_agent.imagery_schema import validate_declaration

IMAGE = 'sha256:' + 'd'*64
DECLARATION = {'processing_mode': 'multispectral', 'source_note': 'fixture metadata'}


@pytest.fixture
def catalog_case(tmp_path):
    source = tmp_path/'flight'
    source.mkdir()
    for i in range(4):
        (source/f'{i}.tif').write_bytes(f'fixture-{i}'.encode())
    product = tmp_path/'orthophoto.tif'
    product.write_bytes(b'verified output fixture, not a real raster')
    catalog = tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'entries':[{
        'operation':'orthomosaic', 'image':IMAGE, 'input_identity':input_identity(source),
        'science_run_identity': science_run_identity('orthomosaic', {'flight':input_identity(source)},
            validate_declaration('orthomosaic', DECLARATION), IMAGE),
        'products':[{'name':product.name,'path':str(product),'sha256':file_hash(product)}],
        'declaration':DECLARATION,'summary':{'width':11},'provenance':{'job_id':'prior-success'},
        'limitations':['fixture only']}]}))
    config = Config(workspace_root=tmp_path/'runtime', imagery_result_catalog=catalog,
        orthomosaic_image=IMAGE, imagery_image=IMAGE,
        models={'small':ModelProfile(repository='fixture',served_name='fixture')})
    app = create_app(config, enable_agent=False)
    with TestClient(app,base_url='http://127.0.0.1',headers={'origin':'http://127.0.0.1'}) as client:
        client.get('/session')
        ref = client.post('/resources/register',json={'request_id':'source','path':str(source),'read_only':True}).json()['resource_id']
        declaration = tmp_path/'declaration.json'
        declaration.write_text(json.dumps(DECLARATION))
        declared_ref = client.post('/resources/register', json={'request_id':'declaration',
            'path':str(declaration),'read_only':True}).json()['resource_id']
        task = client.post('/tasks',json={'request_id':'task','objective':'reuse granted flight','input_refs':[ref,declared_ref]}).json()['task_id']
        yield client, app, task, ref, source, product


def submit(case, *, declared=True):
    client,app,task,ref,*_ = case
    payload = {'request_id':'reuse','expected_spec_revision':1,
        'operation':'orthomosaic','input_refs':{'flight':ref}}
    if declared:
        payload['declaration_ref'] = app.state.engine.store.task(task)['input_refs'][1]
    return client.post(f'/tasks/{task}/imagery',json=payload).json()


def test_missing_declaration_returns_candidates_without_import_or_job(catalog_case):
    _,app,task,*_ = catalog_case
    response = submit(catalog_case, declared=False)
    assert response['kind'] == 'reuse_candidates'
    assert not response.get('reused') and not response.get('artifact_ids')
    candidate = response['candidates'][0]
    assert candidate['declaration'] == DECLARATION
    assert candidate['method_match'] == 'unknown_without_current_declaration'
    assert app.state.engine.store.task(task)['artifact_ids'] == []
    assert app.state.engine.jobs.list(task)['jobs'] == []


@pytest.mark.parametrize('change', ['legacy', 'algorithm', 'preprocessing', 'calibration'])
def test_unknown_or_changed_science_identity_is_only_a_candidate(catalog_case, change):
    _,app,task,*_ = catalog_case
    path = app.state.engine.config.imagery_result_catalog
    catalog = json.loads(path.read_text())
    entry = catalog['entries'][0]
    if change == 'legacy':
        entry.pop('science_run_identity')
    else:
        field = {'algorithm':'algorithm_version', 'preprocessing':'preprocessing_version',
                 'calibration':'calibration_provenance'}[change]
        entry['science_run_identity'][field] = 'changed'
    path.write_text(json.dumps(catalog))
    response = submit(catalog_case)
    assert response['kind'] == 'reuse_candidates' and not response.get('reused')
    assert response['candidates'][0]['method_match'] == (
        'unknown_legacy_identity' if change == 'legacy' else 'identity_mismatch')
    assert app.state.engine.store.task(task)['artifact_ids'] == []
    assert app.state.engine.jobs.list(task)['jobs'] == []


@pytest.mark.parametrize('scenario', ['adopt','stale','method_mismatch','known_identity_mismatch',
    'missing_note','changed_input','damaged_product'])
def test_explicit_legacy_adoption_keeps_unknowns_and_never_falls_back_to_compute(catalog_case, scenario):
    client,app,task,ref,source,product = catalog_case
    engine = app.state.engine
    path = engine.config.imagery_result_catalog
    catalog = json.loads(path.read_text())
    entry = catalog['entries'][0]
    if scenario == 'known_identity_mismatch':
        entry['science_run_identity']['algorithm_version'] = 'different'
    else:
        entry.pop('science_run_identity')
    if scenario == 'method_mismatch':
        entry['declaration'] = {**DECLARATION, 'processing_mode':'rgb'}
    path.write_text(json.dumps(catalog))
    candidate = submit(catalog_case, declared=False)['candidates'][0]
    if scenario == 'stale':
        entry['limitations'].append('changed catalog evidence')
        path.write_text(json.dumps(catalog))
    elif scenario == 'changed_input':
        (source/'0.tif').write_bytes(b'changed input')
    elif scenario == 'damaged_product':
        product.write_bytes(b'damaged product')
    payload = {'request_id':'adopt', 'expected_spec_revision':1, 'operation':'orthomosaic',
        'input_refs':{'flight':ref}, 'declaration_ref':engine.store.task(task)['input_refs'][1],
        'reuse_candidate_id':candidate['candidate_id']}
    if scenario != 'missing_note':
        payload['adoption_note'] = '采用已有探索性正射方案交付当前数据；原算法和预处理版本未知，不声称等价或现场精度。'
    response = client.post(f'/tasks/{task}/imagery',json=payload)
    result = response.json()
    assert engine.jobs.list(task)['jobs'] == []
    if scenario == 'adopt':
        assert result['reused'] and result['adoption']['candidate_id'] == candidate['candidate_id']
        assert 'identity_incomplete' in result['science_status']
        assert result['adoption']['unknown_identity_fields']
        provenance = client.get(f"/artifacts/{result['provenance_artifact_id']}/content").json()
        assert provenance['science_run_identity'] is None
        assert provenance['requested_science_run_identity'] is not None
        assert provenance['adoption']['note'] == payload['adoption_note']
        assert provenance['limitations'][-1].startswith('明确采用旧候选')
        # Replay reuses the committed receipt, rather than importing another copy.
        assert client.post(f'/tasks/{task}/imagery',json=payload).json()['artifact_ids'] == result['artifact_ids']
        assert len(engine.store.task(task)['artifact_ids']) == 2
    else:
        assert not result.get('reused') and engine.store.task(task)['artifact_ids'] == []
        if scenario == 'known_identity_mismatch':
            assert result['kind'] == 'reuse_candidates'
        else:
            code = {'missing_note':'SCHEMA_INVALID', 'damaged_product':'VERIFICATION_FAILED'}.get(
                scenario, 'VERSION_CONFLICT')
            assert result.get('error', {}).get('code') == code, result


def test_native_tool_can_discover_then_explicitly_adopt_legacy_candidate(catalog_case):
    import asyncio
    from itertools import count
    from types import SimpleNamespace

    from forestry_agent.agent import Deps
    from forestry_agent.runtime.job_tools import register_job_tools
    from forestry_agent.runtime.tools import ToolRegistry

    _,app,task,ref,*_ = catalog_case
    engine = app.state.engine
    catalog_path = engine.config.imagery_result_catalog
    catalog = json.loads(catalog_path.read_text())
    catalog['entries'][0].pop('science_run_identity')
    catalog_path.write_text(json.dumps(catalog))
    registry = ToolRegistry()
    calls = count()
    cleanup = register_job_tools(registry, engine=engine, jobs=engine.jobs, roles=engine.roles,
        store=engine.store, journal=lambda ctx,kind,args,operation: engine.perform(
            ctx.deps, 'native-'+str(next(calls)), kind, args, operation))
    process = next(spec.func for spec in registry.specs() if spec.name == 'process_imagery')
    ctx = SimpleNamespace(deps=Deps(engine,task,'operator-imagery',1))

    async def flow():
        candidates = await process(ctx, 'orthomosaic', {'flight':ref})
        assert candidates['kind'] == 'reuse_candidates'
        assert engine.store.task(task)['artifact_ids'] == []
        return await process(ctx, 'orthomosaic', {'flight':ref},
            declaration_ref=engine.store.task(task)['input_refs'][1],
            reuse_candidate_id=candidates['candidates'][0]['candidate_id'],
            adoption_note='基于旧声明采用已完成的相同航次探索性方案；保留算法/预处理未知及无独立精度验证限制。')

    try:
        result = asyncio.run(flow())
    finally:
        cleanup()
    assert result['reused'] and result['adoption']['selection'] == 'explicit_legacy_candidate'
    assert engine.jobs.list(task)['jobs'] == []


@pytest.mark.parametrize('lut_state', ['same','changed','missing','legacy_same','legacy_changed'])
def test_inversion_import_requires_original_lut_identity(catalog_case, lut_state):
    client,app,_,_,source,product = catalog_case
    declaration = {'reflectance':'calibrated_fraction_0_1', 'calibration_source':'camera metadata',
        'band_mapping_source':'sensor metadata', 'band_names':['Red','NIR'], 'raster_bands':[1,2]}
    lut = source.parent/'lut.npz'
    lut.write_bytes(b'original priors and geometry fixture')
    declared = source.parent/'invert-declaration.json'
    declared.write_text(json.dumps(declaration))
    catalog = {'entries':[{'operation':'invert','image':IMAGE,
        'input_identity':input_identity(product), 'declaration':declaration,
        'science_run_identity':science_run_identity('invert',
            {'raster':input_identity(product),'lut':input_identity(lut)},
            validate_declaration('invert',declaration),IMAGE),
        'products':[{'name':product.name,'path':str(product),'sha256':file_hash(product)}],
        'summary':{'fixture':True,'lut_sha256':file_hash(lut)},
        'provenance':{'job_id':'original'},'limitations':['fixture only']} ]}
    if lut_state.startswith('legacy_'):
        catalog['entries'][0].pop('science_run_identity')
    app.state.engine.config.imagery_result_catalog.write_text(json.dumps(catalog))
    if lut_state in {'changed','legacy_changed'}:
        lut.write_bytes(b'changed LUT priors or solar geometry')
    refs = {}
    for role,path in {'raster':product,'lut':lut,'declaration':declared}.items():
        refs[role] = client.post('/resources/register',json={'request_id':'invert-'+role,
            'path':str(path),'read_only':True}).json()['resource_id']
    task = client.post('/tasks',json={'request_id':'invert-task','objective':'reuse inversion',
        'input_refs':list(refs.values())}).json()['task_id']
    inputs = {'raster':refs['raster']}
    if lut_state != 'missing':
        inputs['lut'] = refs['lut']
    payload = {'request_id':'invert',
        'expected_spec_revision':1,'operation':'invert','input_refs':inputs,
        'declaration_ref':refs['declaration']}
    if lut_state.startswith('legacy_'):
        from forestry_agent.imagery_catalog import candidate_id
        payload.update(reuse_candidate_id=candidate_id(catalog['entries'][0]),
            adoption_note='采用有原LUT哈希证据的旧反演方案，保留原算法及预处理未知、无独立校准/现场精度的限制。')
    response = client.post(f'/tasks/{task}/imagery',json=payload).json()
    assert app.state.engine.jobs.list(task)['jobs'] == []
    if lut_state in {'same','legacy_same'}:
        assert response['reused'] and len(response['artifact_ids']) == 2
        record = client.get(f"/artifacts/{response['provenance_artifact_id']}/content").json()
        assert record['science_run_identity'] == catalog['entries'][0].get('science_run_identity')
    elif lut_state == 'legacy_changed':
        assert response['error']['code'] == 'VERSION_CONFLICT'
        assert app.state.engine.store.task(task)['artifact_ids'] == []
    else:
        assert response['kind'] == 'reuse_candidates' and not response.get('reused')
        assert response['status'] == 'identity_verification_required'
        assert response['candidates'][0]['method_match'] == (
            'unknown_without_lut' if lut_state == 'missing' else 'identity_mismatch')
        assert app.state.engine.store.task(task)['artifact_ids'] == []


@pytest.mark.parametrize('field,value', [('radiometric_calibration','camera+sun'),
    ('orthophoto_resolution_cm', 10), ('processing_mode','rgb')])
def test_changed_method_declaration_never_reuses(catalog_case, field, value):
    from forestry_agent.imagery import ImageryRequest
    from forestry_agent.imagery_catalog import reuse_verified
    from types import SimpleNamespace

    client,app,_,ref,source,_ = catalog_case
    declaration = source.parent/'changed-declaration.json'
    declaration.write_text(json.dumps({**DECLARATION,field:value}))
    declared_ref = client.post('/resources/register',json={'request_id':'changed-declaration',
        'path':str(declaration),'read_only':True}).json()['resource_id']
    task = client.post('/tasks',json={'request_id':'changed-task','objective':'changed method',
        'input_refs':[ref,declared_ref]}).json()['task_id']
    engine = app.state.engine
    request = ImageryRequest(request_id='changed', expected_spec_revision=1,
        operation='orthomosaic', input_refs={'flight':ref},
        declaration_ref=declared_ref)
    assert reuse_verified(engine, SimpleNamespace(task_id=task), 'changed', request) is None
    assert engine.store.task(task)['artifact_ids'] == []
    assert engine.jobs.list(task)['jobs'] == []


def test_reuse_is_session_owned_and_not_a_fake_job(catalog_case):
    c,app,task,_,_,_ = catalog_case
    response = submit(catalog_case)
    assert response['reused'] is True and 'job' not in response
    assert response['summary']['width'] == 11
    assert app.state.engine.jobs.list(task)['jobs'] == []
    assert len(response['artifact_ids']) == 2
    artifact = app.state.engine.resources.artifact(response['artifact_ids'][0],task)
    assert Path(artifact['locator']).is_relative_to(app.state.engine.resources.workspace/'tasks'/task)
    # A different session cannot obtain this output with an arbitrary reference.
    other = c.post('/tasks',json={'request_id':'other','objective':'no grant'}).json()['task_id']
    denied = c.post(f'/tasks/{other}/imagery',json={'request_id':'denied','expected_spec_revision':1,
        'operation':'orthomosaic','input_refs':{'flight':catalog_case[3]}}).json()
    assert denied['error']['code'] == 'PERMISSION_DENIED'


@pytest.mark.parametrize('change',['bytes','additional_file','damaged_product'])
def test_changed_input_or_product_never_returns_a_cached_success(catalog_case,change):
    _,app,task,_,source,product = catalog_case
    if change == 'bytes':
        (source/'0.tif').write_bytes(b'changed')
    elif change == 'additional_file':
        (source/'extra.tif').write_bytes(b'new flight capture')
    else:
        product.write_bytes(b'corrupt')
    response = submit(catalog_case, declared=False)
    assert not response.get('reused')
    assert app.state.engine.store.task(task)['artifact_ids'] == []
    assert app.state.engine.jobs.list(task)['jobs'] == []
