"""Recipe identity contracts without running raster or PROSAIL computation."""
import ast
import json
from types import SimpleNamespace

import pytest

from forestry_agent.imagery import ImageryRequest, prepare_imagery
from forestry_agent.imagery_schema import validate_declaration
from forestry_agent.science_identity import science_run_identity

IMAGE = 'sha256:' + 'd'*64
INVERSION = {'reflectance':'calibrated_fraction_0_1', 'calibration_source':'camera fixture',
             'band_mapping_source':'sensor metadata', 'band_names':['Red','NIR'], 'raster_bands':[1,2]}


def test_optional_adoption_fields_preserve_old_request_serialization():
    request = ImageryRequest(request_id='old', expected_spec_revision=1, operation='inspect',
        input_refs={'raster':'ref'})
    assert request.model_dump() == {'request_id':'old','expected_spec_revision':1,'operation':'inspect',
        'input_refs':{'raster':'ref'},'declaration_ref':None,'seconds':900}
    assert 'adoption_note' not in request.model_dump(exclude_none=True)
    assert request.model_dump_json() == json.dumps(request.model_dump(), separators=(',', ':'))


def test_inversion_identity_binds_lut_bytes_and_calibration():
    declaration = validate_declaration('invert', INVERSION)
    data = {'raster':{'kind':'file','sha256':'raster'}, 'lut':{'kind':'file','sha256':'lut-priors-geometry'}}
    identity = science_run_identity('invert', data, declaration, IMAGE)
    changed_lut = {**data, 'lut':{'kind':'file','sha256':'changed-priors-or-geometry'}}
    assert science_run_identity('invert', changed_lut, declaration, IMAGE) != identity
    changed_calibration = {**declaration, 'calibration_source':'other calibration'}
    assert science_run_identity('invert', data, changed_calibration, IMAGE) != identity
    assert science_run_identity('invert', dict(reversed(list(data.items()))), declaration, IMAGE) == identity


@pytest.mark.parametrize('operation', ['invert','build_lut','orthomosaic'])
def test_prepared_recipe_records_effective_science_identity(tmp_path, operation):
    from forestry_agent.imagery_schema import LEGACY_RANGES
    declarations = {
        'invert':INVERSION,
        'orthomosaic':{'processing_mode':'multispectral','source_note':'flight metadata'},
        'build_lut':{'parameter_source':'legacy priors','geometry_source':'flight metadata',
            'sensor_response_source':'sensor sheet','ranges':LEGACY_RANGES,
            'bands':[{'name':'Red','center_nm':668,'half_width_nm':10},
                     {'name':'NIR','center_nm':840,'half_width_nm':20}],
            'solar_zenith':30,'view_zenith':0,'relative_azimuth':0},
    }
    refs = {'invert':{'raster':'raster','lut':'lut'}, 'build_lut':{},
            'orthomosaic':{'0':'a.tif','1':'b.tif','2':'c.tif'}}[operation]
    files = {}
    for ref in refs.values():
        files[ref] = tmp_path/ref
        files[ref].write_bytes(ref.encode())
    files['declaration'] = tmp_path/'declaration.json'
    files['declaration'].write_text(json.dumps(declarations[operation]))
    snapshot = {'imagery_image':IMAGE,'orthomosaic_image':IMAGE}
    engine = SimpleNamespace(store=SimpleNamespace(task=lambda _: {'spec_revision':1,'config_snapshot':snapshot}),
        config=SimpleNamespace(imagery_image=IMAGE,orthomosaic_image=IMAGE,max_code_cpus=1),
        resources=SimpleNamespace(resolve_input=lambda ref, task: files[ref]))
    code = prepare_imagery(engine, 'task', ImageryRequest(request_id='fixture', expected_spec_revision=1,
        operation=operation, input_refs=refs, declaration_ref='declaration'))
    payload = ast.literal_eval(ast.parse(code).body[0].value)
    identity = payload['science_run_identity']
    assert identity['effective_declaration'] == {
        k:v for k,v in json.loads(json.dumps(validate_declaration(operation, declarations[operation]))).items()
        if k != 'source_note'}
    assert identity['image_digest'] == IMAGE and len(identity['algorithm_version']) == 64
    if operation == 'invert':
        assert set(identity['data']) == {'raster','lut'}
    elif operation == 'orthomosaic':
        assert set(identity['data']['flight']['files']) == {'a.tif','b.tif','c.tif'}
    else:
        assert identity['data'] == {} and identity['effective_declaration']['solar_zenith'] == 30


def test_worker_result_preserves_request_identity_without_scientific_execution(tmp_path, monkeypatch):
    from forestry_agent import imagery_worker
    identity = science_run_identity('build_lut', {}, {'solar_zenith':30}, IMAGE)
    monkeypatch.setattr(imagery_worker, 'build_lut', lambda declaration, output: {'fixture':True})
    request = {'operation':'build_lut', 'inputs':{}, 'declaration':{}, 'cpu_threads':1,
               'image':IMAGE,'method_id':'fixture', 'science_run_identity':identity}
    imagery_worker.main(request, tmp_path/'output')
    result = json.loads((tmp_path/'output'/'result.json').read_text())
    assert result['science_run_identity'] == identity and result['identity_status'] == 'recorded'
