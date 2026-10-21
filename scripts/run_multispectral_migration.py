"""Run the authorized real flight via the existing Jobs/API, with durable replay.

Requires a prepared, compatible ODM image pinned by --odm-image. It never pulls
images, generates another LUT, starts a model, or substitutes raw DN for reflectance.
"""
import argparse
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.jobs import TERMINAL

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workspace/imagery-migration-20261021"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odm-image', required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--resume-job', help='Same-task failed ODM job; reuse immutable reconstruction checkpoints')
    args = parser.parse_args()
    config = load_config(ROOT/'config/app.local.yaml')
    config.workspace_root = OUT/'real-flight-runtime'
    config.port = 8775
    config.orthomosaic_image = args.odm_image
    config.max_code_output_bytes = 8*1024**3
    if not config.imagery_image:
        raise RuntimeError('Configure the validated imagery image first')
    # The output guard stays active; budget includes staging, ODM work and final TIFFs.
    app = create_app(config, enable_agent=False)
    engine = app.state.engine
    numerical = json.loads((OUT/'numerical-validation.json').read_text(encoding='utf-8'))
    lut_runtime = OUT/'runtime'
    import sqlite3
    with sqlite3.connect((lut_runtime/'.forestry/state.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        lut = json.loads(db.execute('SELECT payload FROM artifacts WHERE id=?',(numerical['lut_artifact'],)).fetchone()[0])
    lut_path = Path(lut['locator'])
    from forestry_agent.resources import file_hash
    assert file_hash(lut_path) == lut['sha256'], 'Frozen LUT changed; do not regenerate silently'
    odm_declaration = OUT/'odm-declaration.json'
    content = json.dumps({'processing_mode':'multispectral','source_note':
        'User-authorized N flight; frozen flight-inspection.json:20 complete M3M four-band captures',
        'radiometric_calibration':'camera','dsm':True})
    if odm_declaration.exists():
        assert odm_declaration.read_text(encoding='utf-8') == content, 'Frozen declaration changed'
    else:
        odm_declaration.write_text(content,encoding='utf-8')
    with TestClient(app,base_url='http://127.0.0.1',headers={'origin':'http://127.0.0.1'}) as client:
        client.get('/session')

        def register(path, request_id):
            response = client.post('/resources/register',json={'request_id':request_id,'path':str(path),'read_only':True})
            response.raise_for_status()
            return response.json()['resource_id']

        flight_ref = register(args.input,'migration-real-flight-source-v1')
        declared_ref = register(odm_declaration,'migration-real-odm-declaration-v1')
        lut_ref = register(lut_path,'migration-frozen-lut-source-v1')
        response = client.post('/tasks',json={'request_id':'migration-real-flight-task-v1',
            'objective':'授权M3M多光谱拼接与旧PROSAIL LAI反演；探索性产品，不宣称现场准确性',
            'input_refs':[flight_ref,declared_ref,lut_ref],
            'constraints':{'allowed_capabilities':['code.execute','job.observe','fs.write_workspace']}})
        response.raise_for_status()
        task = response.json()['task_id']
        handle = OUT/'real-flight-handles.json'
        records = json.loads(handle.read_text(encoding='utf-8')) if handle.exists() else {'task_id':task,'model_requests':0}

        def submit(payload, stage):
            receipt = client.post(f'/tasks/{task}/imagery',json={**payload,'request_id':'migration-real-'+stage+'-v1',
                'expected_spec_revision':1,'seconds':14400}).json()
            assert receipt.get('kind') == 'completed',receipt
            job_id = receipt['job']['job_id']
            records[stage] = job_id
            handle.write_text(json.dumps(records,indent=2),encoding='utf-8')
            print(json.dumps({'stage':stage,'job_id':job_id}),flush=True)
            previous = None
            while True:
                engine.jobs.tick()
                job = engine.jobs.read(job_id)
                state = (job['status'],json.dumps(job.get('blocker')))
                if state != previous:
                    print(json.dumps({'stage':stage,'status':job['status'],'blocker':job.get('blocker')}),flush=True)
                    previous = state
                if job['status'] in TERMINAL:
                    break
                if job.get('blocker'):
                    raise RuntimeError('Job retained; resolve the recorded blocker, do not resubmit: '+json.dumps(job['blocker']))
                time.sleep(3)
            records[stage+'_status'] = job['status']
            handle.write_text(json.dumps(records,indent=2),encoding='utf-8')
            assert job['status'] == 'succeeded',engine.jobs.public(job)
            return [engine.resources.artifact(a,task) for a in job['artifact_ids']]

        odm_payload = {'operation':'orthomosaic','input_refs':{'flight':flight_ref},'declaration_ref':declared_ref}
        stage = 'odm'
        if args.resume_job:
            odm_payload['resume_job_id'] = args.resume_job
            stage = 'odm-resume-' + args.resume_job[:8]
        artifacts = submit(odm_payload, stage)
        result = next(a for a in artifacts if a['name'] == 'result.json')
        record = json.loads(Path(result['locator']).read_text(encoding='utf-8'))['result']
        # Match by published path to avoid confusing a DSM with the orthophoto.
        ortho = next(a for a in artifacts if Path(a['locator']).name == 'odm_orthophoto.tif')
        names = record['metadata']['bands']
        expected = ['Red','Green','RedEdge','NIR']
        if not all(name in names for name in expected):
            raise RuntimeError('Actual ODM band descriptions are incomplete; retain ortho, inspect mapping, do not guess')
        declared = OUT/'inversion-declaration.json'
        from forestry_agent.imagery_schema import InversionDeclaration
        d = InversionDeclaration(reflectance='calibrated_fraction_0_1',
            calibration_source='ODM camera radiometric normalization from M3M irradiance/vignetting metadata; not independently calibrated',
            band_mapping_source='Actual ODM result.json band descriptions, matched by name',band_names=expected,
            raster_bands=[names.index(n)+1 for n in expected],
            alpha_band=names.index('Alpha')+1 if 'Alpha' in names else (
                record['metadata'].get('colorinterp',[]).index('alpha')+1
                if 'alpha' in record['metadata'].get('colorinterp',[]) else None),ndvi_threshold=.2)
        declared.write_text(d.model_dump_json(indent=2),encoding='utf-8')
        # Use the same task's already-granted output as a declaration, avoiding spec mutation.
        # Writing an artifact is the existing fs.write_workspace mechanism.
        declaration_artifact = engine.perform(Deps(engine,task,'migration-declaration',1),
            'migration-inversion-declaration-v1','fs.write_workspace',
            {'relative_path':'inversion-declaration.json','content':d.model_dump_json(indent=2)},
            lambda action_id:engine.resources.write(task,action_id,'inversion-declaration.json',d.model_dump_json(indent=2)))
        assert declaration_artifact['kind'] == 'completed', declaration_artifact
        inverted = submit({'operation':'invert','input_refs':{'raster':ortho['artifact_id'],'lut':lut_ref},
            'declaration_ref':declaration_artifact['artifact_id']},'inversion')
        records['artifacts'] = [{'artifact_id':a['artifact_id'],'name':a['name'],'path':a['locator']} for a in [*artifacts,*inverted]]
        records['scope'] = 'Real multispectral orthomosaic + model-based LAI; no field accuracy claim'
        handle.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(records,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
