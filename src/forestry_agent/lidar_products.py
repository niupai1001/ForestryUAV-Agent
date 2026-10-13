"""Select complete scientific batches by producer and recorded byte lineage."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from .contracts import Failure
from .resources import file_hash, version


def artifacts(engine, task_id):
    engine.store.task(task_id)
    with engine.store.transaction() as db:
        return [json.loads(row[0]) for row in db.execute(
            'SELECT payload FROM artifacts WHERE task_id=? ORDER BY rowid', (task_id,))]


def record(engine, artifact, task_id):
    if artifact['size_bytes'] > 131072:
        raise Failure('INPUT_LIMIT', '科学产品记录超过128 KiB', 422)
    verified = engine.resources.artifact(artifact['artifact_id'], task_id)
    try:
        data = json.loads(Path(verified['locator']).read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as error:
        raise Failure('VERIFICATION_FAILED', '科学产品记录不是有效JSON', 422) from error
    if not isinstance(data, dict):
        raise Failure('VERIFICATION_FAILED', '科学产品记录须为JSON对象', 422)
    return data


def production_job(engine, artifact, task_id, method):
    with engine.store.transaction() as db:
        row = db.execute('SELECT payload FROM jobs WHERE task_id=? AND action_id=?',
                         (task_id, artifact['producer_action_id'])).fetchone()
    if not row:
        return None
    job = json.loads(row[0])
    image = engine.store.task(task_id)['config_snapshot'].get('science_image')
    if job['status'] != 'succeeded' or not image or job['image_reference'] != image:
        return None
    try:
        first = ast.parse(job['code'].splitlines()[0]).body[0]
        if not isinstance(first, ast.Assign) or not isinstance(first.targets[0], ast.Name) or first.targets[0].id != 'REQUEST':
            return None
        request = ast.literal_eval(first.value)
        if request.get('method_id') != method or request.get('task_id') != task_id or request.get('science_image') != image:
            return None
    except (ValueError, SyntaxError, TypeError, IndexError, AttributeError):
        return None
    if artifact['artifact_id'] not in job['artifact_ids']:
        return None
    return job, request


def stage_batch(engine, task_id, stage, *, manifest_sha=None):
    all_files = artifacts(engine, task_id)
    for manifest in reversed(all_files):
        if manifest['name'] != 'stage-result.json' or (manifest_sha and manifest['sha256'] != manifest_sha):
            continue
        production = production_job(engine, manifest, task_id, 'lidar.p2r_lmf_dalponte.v1')
        if not production:
            continue
        job, request = production
        data = record(engine, manifest, task_id)
        if data.get('stage') != stage:
            continue
        if (data.get('method_id') != request['method_id'] or data.get('task_id') != task_id
                or data.get('science_image') != job['image_reference'] or request.get('stage') != stage
                or data.get('parameters') != request.get('parameters') or not isinstance(data.get('outputs'), dict)
                or not data['outputs'] or not isinstance(data.get('input_sha256'), dict)):
            raise Failure('VERIFICATION_FAILED', '科学阶段记录与生产作业不匹配', 422)
        required_outputs = {'chm': {'chm-p2r.tif', 'canopy-support.tif'},
                            'seeds': {'accepted-seeds.gpkg', 'tree-id-mapping.csv'},
                            'instances': {'segmented.laz', 'instance-quality.json', 'trees.csv', 'crown-labels.tif'}}[stage]
        required_inputs = {'chm': {'working', 'terrain_quality', 'terrain_support'},
                           'seeds': {'chm', 'chm_record'},
                           'instances': {'working', 'original', 'hag', 'chm', 'chm_record', 'seeds', 'seed_record'}}[stage]
        if (not required_outputs <= data['outputs'].keys() or not required_inputs <= data['input_sha256'].keys()
                or not isinstance(data.get('source_sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', data['source_sha256'])
                or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                       for value in data['input_sha256'].values())):
            raise Failure('VERIFICATION_FAILED', '科学阶段记录缺少产品或输入校验和', 422)
        files = {a['name']: a for a in all_files if a['producer_action_id'] == manifest['producer_action_id']
                 and a['artifact_id'] in job['artifact_ids']}
        if any(name not in files or not isinstance(value, dict) or files[name]['sha256'] != value.get('sha256')
               for name, value in data['outputs'].items()):
            raise Failure('VERIFICATION_FAILED', '最新科学批次不完整，不混用其他版本文件', 422)
        return manifest, data, files
    raise Failure('PRECONDITION_MISSING', f'没有可衔接的完整{stage}产品批次，请先完成该阶段', 422)


def automatic_inputs(engine, task_id, stage):
    if stage == 'seeds':
        _, _, chm = stage_batch(engine, task_id, 'chm')
        return {'chm': chm['chm-p2r.tif']['artifact_id'], 'chm_record': chm['stage-result.json']['artifact_id']}
    if stage == 'chm':
        all_files = artifacts(engine, task_id)
        for quality in reversed(all_files):
            if quality['name'] != 'terrain-quality.json':
                continue
            if not production_job(engine, quality, task_id, 'lidar.smrf_idw.v1'):
                continue
            facts = record(engine, quality, task_id)
            if (not isinstance(facts.get('normalized_working'), dict)
                    or facts['normalized_working'].get('z_semantics') != 'height_above_ground'
                    or facts['normalized_working'].get('unit') != 'm'):
                raise Failure('VERIFICATION_FAILED', '地形批次未明确工作云高度语义', 422)
            files = {a['name']: a for a in all_files if a['producer_action_id'] == quality['producer_action_id']}
            if all(name in files for name in ('normalized-working.las', 'terrain-support.tif')):
                return {role: files[name]['artifact_id'] for role, name in {
                    'working': 'normalized-working.las', 'terrain_quality': 'terrain-quality.json',
                    'terrain_support': 'terrain-support.tif'}.items()}
        raise Failure('PRECONDITION_MISSING', '没有完整同批次地形产品，请提供明确输入或先完成地形阶段', 422)
    if stage != 'instances':
        raise Failure('SCHEMA_INVALID', '未知冠层阶段')
    _, seed_record, seeds = stage_batch(engine, task_id, 'seeds')
    _, chm_record, chm = stage_batch(engine, task_id, 'chm', manifest_sha=seed_record['input_sha256']['chm_record'])
    all_files = artifacts(engine, task_id)
    working = next((a for a in reversed(all_files) if a['sha256'] == chm_record['input_sha256']['working']), None)
    if not working:
        raise Failure('PRECONDITION_MISSING', '工作云须为同会话产品；外部来源请明确提供输入映射', 422)
    terrain = {a['name']: a for a in all_files if a['producer_action_id'] == working['producer_action_id']}
    if 'hag.las' not in terrain:
        raise Failure('PRECONDITION_MISSING', '工作云批次缺少HAG，不自动混用其他批次', 422)
    original = next((a['artifact_id'] for a in all_files if a['sha256'] == chm_record['source_sha256']), None)
    if not original:
        for ref in engine.store.task(task_id)['input_refs']:
            path = engine.resources.resolve(ref, task_id=task_id)
            if path.is_file() and path.suffix.lower() in {'.las', '.laz'} and file_hash(path) == chm_record['source_sha256']:
                original = ref
                break
    if not original:
        raise Failure('PRECONDITION_MISSING', '原始点云未授权或字节不匹配，不自动换源', 422)
    return {'working': working['artifact_id'], 'original': original, 'hag': terrain['hag.las']['artifact_id'],
            'chm': chm['chm-p2r.tif']['artifact_id'], 'chm_record': chm['stage-result.json']['artifact_id'],
            'seeds': seeds['accepted-seeds.gpkg']['artifact_id'], 'seed_record': seeds['stage-result.json']['artifact_id']}


def product_set(engine, task_id):
    try:
        manifest, data, files = stage_batch(engine, task_id, 'instances')
    except Failure as error:
        if error.code == 'PRECONDITION_MISSING':
            return {'status': 'no_complete_instances', 'files': []}
        raise
    # Add upstream products only through the exact checksums in this manifest.
    for stage, key in [('chm', 'chm_record'), ('seeds', 'seed_record')]:
        try:
            _, _, upstream = stage_batch(engine, task_id, stage, manifest_sha=data['input_sha256'][key])
            for name, artifact in upstream.items():
                if name not in files and name not in {'stage-result.json', 'request.json'}:
                    files[name] = artifact
        except Failure as error:
            if error.code != 'PRECONDITION_MISSING':
                raise
    # Existing audit previews belong to a separate producer. Bind its successful
    # Job mounts to this exact immutable set, including recorded file versions.
    required = {'segmented.laz', 'instance-quality.json', 'trees.csv', 'crown-labels.tif', 'chm-p2r.tif'}
    for audit in reversed(artifacts(engine, task_id)):
        if audit['name'] != '冠层产品读回检查.json' or not required <= set(files):
            continue
        with engine.store.transaction() as db:
            row = db.execute('SELECT payload FROM jobs WHERE task_id=? AND action_id=? AND status=?',
                (task_id, audit['producer_action_id'], 'succeeded')).fetchone()
        if not row:
            continue
        job = json.loads(row[0])
        mounts = {m['source']: m for m in job['mounts']}
        if all(a['locator'] in mounts and mounts[a['locator']]['version'] == version(Path(a['locator']))
               and mounts[a['locator']].get('sha256', a['sha256']) == a['sha256'] for a in (files[n] for n in required)):
            if record(engine, audit, task_id).get('status') != 'passed':
                continue
            for a in artifacts(engine, task_id):
                if a['producer_action_id'] == audit['producer_action_id'] and a['name'] in {
                        '冠层产品读回检查.json', '冠层高度预览.png', '冠层实例预览.png'}:
                    files[a['name']] = a
            break
    return {'status': data.get('status', 'partial'), 'product_set_id': manifest['artifact_id'],
            'producer_action_id': manifest['producer_action_id'], 'files': [
                {**a, 'preview_url': f"/artifacts/{a['artifact_id']}/content",
                 'download_url': f"/artifacts/{a['artifact_id']}/content?download=true"}
                for a in files.values()]}
