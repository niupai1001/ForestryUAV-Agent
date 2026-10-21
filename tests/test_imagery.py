"""Only newly changed imagery boundaries; no model, point cloud or historical replay."""
import ast
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from forestry_agent.app import create_app
from forestry_agent.config import Config, ModelProfile
from forestry_agent.imagery_schema import InversionDeclaration, LutDeclaration
from forestry_agent.vision import Vision

IMAGE = "sha256:" + "d" * 64


def test_tiff_message_is_reference_without_auto_visual_decode():
    class NoPixelRead(Vision):
        def prepare(self, *args, **kwargs):
            raise AssertionError("TIFF auto-decoded")
    content = NoPixelRead(None).message_input('task', '拼接这批影像', [
        {'resource_id':str(i), 'name':f'{i}.TIF', 'kind':'file'} for i in range(80)],
        enabled=True, original_prompt='拼接这批影像')
    assert isinstance(content, str) and len(content) < 1600
    assert 'process_imagery' in content and '"resource_id":"79"' not in content


def test_imagery_jobs_are_frozen_scoped_and_equivalent_requests_reuse(tmp_path):
    config = Config(workspace_root=tmp_path/'runtime', imagery_image=IMAGE,
        models={'small':ModelProfile(repository='test', served_name='test')})
    app = create_app(config, enable_agent=False)
    with TestClient(app, base_url='http://127.0.0.1', headers={'origin':'http://127.0.0.1'}) as client:
        client.get('/session')
        # Metadata job fixture only; no claim that these bytes are a valid raster.
        path = tmp_path/'source.tif'
        path.write_bytes(b'boundary fixture')
        ref = client.post('/resources/register', json={'request_id':'register', 'path':str(path), 'read_only':True}).json()['resource_id']
        task = client.post('/tasks', json={'request_id':'create', 'objective':'检查授权正射文件', 'input_refs':[ref],
            'requested_method_id':'imagery.prosail_legacy_nn.v1',
            'constraints':{'allowed_capabilities':['code.execute', 'job.observe']}}).json()['task_id']
        config.imagery_image = 'sha256:' + 'e'*64
        url = f'/tasks/{task}/imagery'
        payload = {'request_id':'inspect', 'operation':'inspect', 'expected_spec_revision':1, 'input_refs':{'raster':ref}}
        first = client.post(url, json=payload).json()
        assert first['kind'] == 'completed', first
        assert first['job']['image_reference'] == IMAGE
        job = app.state.engine.jobs.read(first['job']['job_id'])
        compile(job['code'], 'imagery-rendered', 'exec')
        rendered = ast.literal_eval(job['code'].splitlines()[0].partition('=')[2].strip())
        assert rendered['inputs']['raster']['path'] == '/inputs/'+ref
        assert client.post(url,json={**payload,'request_id':'other'}).json()['job']['job_id'] == first['job']['job_id']
        other = client.post('/resources/register',json={'request_id':str(uuid4()),'path':str(path),'read_only':True}).json()['resource_id']
        denied = client.post(url,json={**payload,'request_id':'denied','input_refs':{'raster':other}}).json()
        assert denied['error']['code'] == 'PERMISSION_DENIED'
        assert len(app.state.engine.jobs.list(task)['jobs']) == 1


def test_inversion_requires_physical_declaration_and_unique_band_mapping():
    with pytest.raises(ValidationError):
        InversionDeclaration.model_validate({'reflectance':'raw_DN'})
    with pytest.raises(ValidationError):
        InversionDeclaration(reflectance='calibrated_fraction_0_1',calibration_source='fixture',
            band_mapping_source='fixture',band_names=['Red','NIR'],raster_bands=[1,1])
    with pytest.raises(ValidationError):
        LutDeclaration.model_validate({'ranges':{'lai':[0,7]}})


def test_one_flight_folder_expands_to_readonly_mounts_without_model_file_list(tmp_path):
    import json
    config = Config(workspace_root=tmp_path/'runtime', orthomosaic_image=IMAGE,
        models={'small':ModelProfile(repository='test',served_name='test')})
    app = create_app(config,enable_agent=False)
    flight = tmp_path/'flight'
    flight.mkdir()
    for i in range(4):
        (flight/f'{i}.tif').write_bytes(b'boundary fixture; not a real flight')
    declaration = tmp_path/'odm.json'
    declaration.write_text(json.dumps({'processing_mode':'multispectral','source_note':'explicit fixture'}))
    with TestClient(app,base_url='http://127.0.0.1',headers={'origin':'http://127.0.0.1'}) as client:
        client.get('/session')
        refs = [client.post('/resources/register',json={'request_id':str(uuid4()),'path':str(p),'read_only':True}).json()['resource_id']
                for p in (flight,declaration)]
        task = client.post('/tasks',json={'request_id':'folder','objective':'拼接已发送文件夹','input_refs':refs,
            'requested_method_id':'imagery.odm.v1','constraints':{'allowed_capabilities':['code.execute']}}).json()['task_id']
        response = client.post(f'/tasks/{task}/imagery',json={'request_id':'odm','expected_spec_revision':1,
            'operation':'orthomosaic','input_refs':{'flight':refs[0]},'declaration_ref':refs[1]}).json()
        assert response['kind'] == 'completed',response
        job = app.state.engine.jobs.read(response['job']['job_id'])
        assert job['runtime_profile'] == 'orthomosaic' and job['image_reference'] == IMAGE
        assert len(job['mounts']) == 5
        assert all(Path(m['source']).is_file() for m in job['mounts'])
        compile(job['code'],'odm-legacy-injected','exec')
