"""Only the changed reuse/authorization boundary; no model or scientific recomputation."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forestry_agent.app import create_app
from forestry_agent.config import Config, ModelProfile
from forestry_agent.imagery_catalog import input_identity
from forestry_agent.resources import file_hash

IMAGE = 'sha256:' + 'd'*64


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
        'products':[{'name':product.name,'path':str(product),'sha256':file_hash(product)}],
        'declaration':{},'summary':{'width':11},'provenance':{'job_id':'prior-success'},
        'limitations':['fixture only']}]}))
    config = Config(workspace_root=tmp_path/'runtime', imagery_result_catalog=catalog,
        orthomosaic_image=IMAGE, models={'small':ModelProfile(repository='fixture',served_name='fixture')})
    app = create_app(config, enable_agent=False)
    with TestClient(app,base_url='http://127.0.0.1',headers={'origin':'http://127.0.0.1'}) as client:
        client.get('/session')
        ref = client.post('/resources/register',json={'request_id':'source','path':str(source),'read_only':True}).json()['resource_id']
        task = client.post('/tasks',json={'request_id':'task','objective':'reuse granted flight','input_refs':[ref]}).json()['task_id']
        yield client, app, task, ref, source, product


def submit(case):
    client,_,task,ref,*_ = case
    return client.post(f'/tasks/{task}/imagery',json={'request_id':'reuse','expected_spec_revision':1,
        'operation':'orthomosaic','input_refs':{'flight':ref}}).json()


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
    response = submit(catalog_case)
    assert not response.get('reused')
    assert app.state.engine.store.task(task)['artifact_ids'] == []
    assert app.state.engine.jobs.list(task)['jobs'] == []
