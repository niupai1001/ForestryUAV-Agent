"""Read an operator-published catalog of verified scientific products.

Reuse requires the entire granted flight (names and bytes), or the exact raster,
and the frozen method/image. Products are copied into this session's existing
artifact store, with provenance; no successful Job is invented.
"""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import Failure
from .resources import file_hash

RASTER_SUFFIXES = {'.tif', '.tiff', '.jpg', '.jpeg', '.dng'}


def input_identity(path: Path) -> dict:
    if path.is_symlink():
        raise Failure('PERMISSION_DENIED', '科学输入不能是符号链接', 403)
    if path.is_file():
        return {'kind': 'file', 'sha256': file_hash(path)}
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in RASTER_SUFFIXES and p.is_file())
    if len(files) > 200:
        raise Failure('INPUT_LIMIT', '当前整航次入口限200张，不截取部分影像', 422)
    if any(p.is_symlink() for p in files):
        raise Failure('PERMISSION_DENIED', '航片目录含符号链接', 403)
    return {'kind': 'directory', 'files': {p.name: file_hash(p) for p in files}}


def reuse_verified(engine, deps, action_id, request):
    catalog = engine.config.imagery_result_catalog
    if not catalog or request.resume_job_id or request.operation not in {'orthomosaic', 'invert'}:
        return None
    refs = request.input_refs
    key = 'flight' if request.operation == 'orthomosaic' else 'raster'
    if set(refs) != {key}:
        return None
    source = engine.resources.resolve_input(refs[key], deps.task_id)
    data = json.loads(Path(catalog).read_text(encoding='utf-8-sig'))
    snapshot = engine.store.task(deps.task_id)['config_snapshot']
    state = engine.store.task(deps.task_id)
    method = 'imagery.odm.v1' if request.operation == 'orthomosaic' else 'imagery.prosail_legacy_nn.v1'
    if state.get('requested_method_id') not in (None, method):
        raise Failure('PRECONDITION_MISSING', '不替换用户指定的科学方法', 422)
    image_key = 'orthomosaic_image' if request.operation == 'orthomosaic' else 'imagery_image'
    identity = None
    for entry in data['entries']:
        if entry['operation'] != request.operation or entry['image'] != snapshot.get(image_key):
            continue
        identity = identity or input_identity(source)
        if identity != entry['input_identity']:
            continue
        if request.declaration_ref:
            declaration_path = engine.resources.resolve_input(request.declaration_ref, deps.task_id)
            if declaration_path.stat().st_size > 32768:
                raise Failure('INPUT_LIMIT', '科学参数声明限32KiB', 422)
            declaration = json.loads(declaration_path.read_text(encoding='utf-8-sig'))
            from .imagery_schema import validate_declaration
            declaration = validate_declaration(request.operation, declaration)
            # Source prose is provenance, not a numerical processing parameter.
            if {k:v for k,v in declaration.items() if k != 'source_note'} != {
                    k:v for k,v in entry['declaration'].items() if k != 'source_note'}:
                continue
        products = entry['products']
        # Verify everything before importing the first output. A corrupt catalog
        # is an explicit failure, never a silent fallback to expensive computation.
        for item in products:
            path = Path(item['path'])
            if path.is_symlink() or not path.is_file() or file_hash(path) != item['sha256']:
                raise Failure('VERIFICATION_FAILED', '已登记科学成果已变化，停止复用；未重新计算', 422)
        artifacts = [engine.resources.import_file(deps.task_id, action_id, Path(item['path']),
                     'verified-reuse/' + item['name']) for item in products]
        provenance = engine.resources.write(deps.task_id, action_id,
            request.operation + '-reuse-provenance.json', json.dumps({
                'operation': request.operation, 'reused': True, 'source': entry['provenance'],
                'input_identity': identity, 'products': [{'name':p['name'], 'sha256':p['sha256']} for p in products],
                'declaration': entry['declaration'], 'limitations': entry['limitations']}, ensure_ascii=False, indent=2))
        return {'kind':'completed', 'operation':request.operation, 'reused':True,
                'science_status':'verified_files_reused_not_field_validated',
                'artifact_ids':[a['artifact_id'] for a in artifacts] + [provenance['artifact_id']],
                'artifacts':[{**{k:a[k] for k in ['artifact_id','name','sha256','media_type']},
                    'url':f"/artifacts/{a['artifact_id']}/content"} for a in artifacts],
                'provenance_artifact_id':provenance['artifact_id'], 'summary':entry['summary'],
                'limitations':entry['limitations'],
                'next':'成果已经封存到当前会话。直接向用户交付这些真实产物；不必再次计算或重复读取输入。'}
    return None
