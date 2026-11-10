"""Read an operator-published catalog of verified scientific products.

Reuse requires the entire granted flight (names and bytes), or the exact raster,
and the frozen method/image. Products are copied into this session's existing
artifact store, with provenance; no successful Job is invented.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import Failure
from .resources import file_hash

RASTER_SUFFIXES = {'.tif', '.tiff', '.jpg', '.jpeg', '.dng'}


def candidate_id(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


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
    if set(refs) not in ({key}, {'raster', 'lut'} if request.operation == 'invert' else {key}):
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
    declaration = None
    if request.declaration_ref:
        declaration_path = engine.resources.resolve_input(request.declaration_ref, deps.task_id)
        if declaration_path.stat().st_size > 32768:
            raise Failure('INPUT_LIMIT', '科学参数声明限32KiB', 422)
        from .imagery_schema import validate_declaration
        declaration = validate_declaration(request.operation,
            json.loads(declaration_path.read_text(encoding='utf-8-sig')))
    candidates = []
    for entry in data['entries']:
        selected_id = candidate_id(entry)
        if request.reuse_candidate_id and selected_id != request.reuse_candidate_id:
            continue
        if entry['operation'] != request.operation or entry['image'] != snapshot.get(image_key):
            continue
        identity = identity or input_identity(source)
        if identity != entry['input_identity']:
            continue
        if declaration is not None:
            stored_declaration = validate_declaration(request.operation, entry['declaration'])
            # Source prose is provenance, not a numerical processing parameter.
            if {k:v for k,v in declaration.items() if k != 'source_note'} != {
                    k:v for k,v in stored_declaration.items() if k != 'source_note'}:
                continue
        products = entry['products']
        # Verify everything before importing the first output. A corrupt catalog
        # is an explicit failure, never a silent fallback to expensive computation.
        for item in products:
            path = Path(item['path'])
            if path.is_symlink() or not path.is_file() or file_hash(path) != item['sha256']:
                raise Failure('VERIFICATION_FAILED', '已登记科学成果已变化，停止复用；未重新计算', 422)
        from .science_identity import science_run_identity
        expected_identity = None
        if declaration is not None and (request.operation != 'invert' or 'lut' in refs):
            bound_data = {key: identity}
            if request.operation == 'invert':
                bound_data['lut'] = input_identity(engine.resources.resolve_input(refs['lut'], deps.task_id))
            expected_identity = science_run_identity(request.operation, bound_data, declaration, entry['image'])
        stored_identity = entry.get('science_run_identity')
        adoption = None
        if request.reuse_candidate_id and stored_identity is None:
            if request.operation == 'invert' and 'lut' in refs:
                supplied_lut = input_identity(engine.resources.resolve_input(refs['lut'], deps.task_id))
                if supplied_lut.get('sha256') != entry['summary'].get('lut_sha256'):
                    raise Failure('VERSION_CONFLICT', '旧成果无法证明使用了指定LUT；不采用、不重新计算', 409)
            adoption = {'candidate_id': selected_id, 'note': request.adoption_note,
                        'selection': 'explicit_legacy_candidate',
                        'unknown_identity_fields': ['algorithm_version', 'preprocessing_version',
                            'independent_calibration_validation'] + (
                            ['lut_prior_geometry_provenance'] if request.operation == 'invert' else [])}
        if adoption is None and (expected_identity is None or stored_identity != expected_identity):
            candidates.append({'candidate_id': selected_id, 'operation': request.operation, 'method_id': method,
                'image': entry['image'], 'input_identity': identity,
                'declaration': entry['declaration'], 'source': entry['provenance'],
                'summary': entry['summary'], 'limitations': entry['limitations'],
                'products': [{'name': p['name'], 'sha256': p['sha256']} for p in products],
                'science_run_identity': stored_identity,
                'method_match': ('unknown_without_current_declaration' if declaration is None
                    else 'unknown_without_lut' if request.operation == 'invert' and 'lut' not in refs
                    else 'unknown_legacy_identity' if stored_identity is None else 'identity_mismatch')})
            continue
        limitations = list(entry['limitations'])
        if adoption:
            limitations.append('明确采用旧候选；完整科学身份未知，未证明与当前算法/预处理完全等价，未验证现场精度。')
        artifacts = [engine.resources.import_file(deps.task_id, action_id, Path(item['path']),
                     'verified-reuse/' + item['name']) for item in products]
        provenance = engine.resources.write(deps.task_id, action_id,
            request.operation + '-reuse-provenance.json', json.dumps({
                'operation': request.operation, 'reused': True, 'source': entry['provenance'],
                'input_identity': identity, 'products': [{'name':p['name'], 'sha256':p['sha256']} for p in products],
                'declaration': entry['declaration'],
                'science_run_identity': stored_identity,
                'requested_science_run_identity': expected_identity, 'adoption': adoption,
                'limitations': limitations}, ensure_ascii=False, indent=2))
        return {'kind':'completed', 'operation':request.operation, 'reused':True,
                'science_status': ('legacy_candidate_adopted_identity_incomplete_not_field_validated'
                    if adoption else 'verified_files_reused_not_field_validated'), 'adoption': adoption,
                'artifact_ids':[a['artifact_id'] for a in artifacts] + [provenance['artifact_id']],
                'artifacts':[{**{k:a[k] for k in ['artifact_id','name','sha256','media_type']},
                    'url':f"/artifacts/{a['artifact_id']}/content"} for a in artifacts],
                'provenance_artifact_id':provenance['artifact_id'], 'summary':entry['summary'],
                'limitations':limitations,
                'next':'成果已经封存到当前会话。交付时说明声明、来源、采用依据和身份未知项；不必再次计算。'}
    if candidates:
        return {'kind': 'reuse_candidates',
                'status': 'declaration_required' if declaration is None else 'identity_verification_required',
                'operation': request.operation, 'candidates': candidates,
                'next': '已有成果的数据与镜像匹配，但尚未确认本次方法要求。核对候选参数、来源和限制；'
                        '需完整声明及科学身份匹配，反演还需原LUT引用。旧成果缺失血缘须先核对原记录，不能猜测补齐。'
                        '缺完整身份的旧方案若符合当前目标，可传candidate_id为reuse_candidate_id及adoption_note明确采用；'
                        '必须保留未知项，不能据此声明当前算法等价或现场精度。已知身份不符不能用此入口绕过。'
                        '本次未导入产物、未启动计算，不得宣称任务已完成。'}
    return None
