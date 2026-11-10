"""Read old job/result records; never execute recipes or rewrite their identities."""
import argparse
import ast
import hashlib
import json
import sqlite3
from pathlib import Path

from forestry_agent.resources import file_hash, native_path


def audit(catalog_path):
    catalog = json.loads(catalog_path.read_text(encoding='utf-8-sig'))
    reports = []
    for entry in catalog['entries']:
        source = entry['provenance']
        database = Path(source['workspace'])/'.forestry/state.sqlite'
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as db:
            db.execute('PRAGMA query_only=ON')

            def read_job(job_id):
                row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
                assert row, 'Missing original job'
                job = json.loads(row[0])
                assert hashlib.sha256(job['code'].encode()).hexdigest() == job['code_sha256']
                return job

            def request(job):
                return ast.literal_eval(ast.parse(job['code']).body[0].value)

            job = read_job(source['job_id'])
            assert job['status'] == 'succeeded' and job['task_id'] == source['task_id']
            assert job['image_reference'] == entry['image']
            original_request = request(job)
            assert original_request['operation'] == entry['operation']
            assert original_request['declaration'] == entry['declaration']
            records = [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM artifacts WHERE task_id=?', (job['task_id'],))]
            artifacts = {a['artifact_id']:a for a in records if a['artifact_id'] in job['artifact_ids']}
            result_artifact = next(a for a in artifacts.values() if a['name'] == 'result.json')
            result_path = native_path(Path(result_artifact['locator']))
            assert file_hash(result_path) == result_artifact['sha256'], 'Original result record changed'
            result = json.loads(result_path.read_text(encoding='utf-8'))
            assert result['image'] == entry['image'] and result['result'] == entry['summary']
            for product in entry['products']:
                assert any(a['locator'] == product['path'] and a['sha256'] == product['sha256']
                           for a in artifacts.values()), 'Catalog product not from original job'
            ancestor = job
            visited = {job['job_id']}
            while request(ancestor).get('resume_job_id'):
                previous = request(ancestor)['resume_job_id']
                assert previous not in visited, 'Resume ancestry cycle'
                visited.add(previous)
                ancestor = read_job(previous)
                assert ancestor['task_id'] == job['task_id']
            reports.append({'operation':entry['operation'], 'source_job_id':job['job_id'],
                'source_task_id':job['task_id'], 'original_code_sha256':job['code_sha256'],
                'data_origin_job_id':ancestor['job_id'], 'resume_chain_jobs':sorted(visited),
                'source_result_sha256':result_artifact['sha256'], 'image_digest':entry['image'],
                'declaration':entry['declaration'], 'input_identity':entry['input_identity'],
                'recorded_lut_sha256':result['result'].get('lut_sha256'),
                'products':[{k:p[k] for k in ('name','sha256')} for p in entry['products']],
                'verification':'original code/result hashes and catalog provenance checked; scientific products not recomputed',
                'unknowns':['separate historical algorithm/preprocessing version identities',
                            'independent calibration validation', 'field LAI accuracy'],
                'science_run_identity':result.get('science_run_identity')})
    return {'catalog_sha256':file_hash(catalog_path), 'entries':reports,
            'policy':'retain original identities; explicit legacy adoption discloses unknowns; no catalog rewrite'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Audit output already exists; inspect it rather than rerunning unchanged inputs')
    report = audit(args.catalog)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'entries':len(report['entries']), 'output':str(args.output)}))
