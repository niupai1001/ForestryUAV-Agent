"""Human message attachments and execution revisions within one conversation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .attachments import claim_uploads
from .contracts import Failure
from .store import encode

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}


def user_path_refs(resources, text, request_id):
    """Only the authenticated human's new text may grant a literal local path."""
    quoted = re.findall(r'["“`\']((?:[A-Za-z]:[\\/]|\\\\)[^"”`\'\r\n]+)["”`\']', text)
    candidates = quoted + re.findall(r'(?:[A-Za-z]:[\\/]|\\\\)[^"“”`\r\n，；。！？<>|?*]+', text)
    refs = []
    for candidate in dict.fromkeys(candidates):
        candidate = re.split(r'\s+(?:并|然后|请|告诉|总结|分析|读取|帮我)', candidate)[0].strip().rstrip('，,；;。')
        path = Path(candidate)
        if not path.is_absolute() or not path.exists():
            continue
        identity = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
        source = resources.register({'request_id': request_id + ':path:' + identity, 'path': str(path),
                                     'classification': 'private', 'read_only': True})
        refs.append(source['resource_id'])
    return list(dict.fromkeys(refs))


def attachment_records(db, refs):
    records = []
    for ref in dict.fromkeys(refs):
        row = db.execute('SELECT payload FROM resources WHERE id=?', (ref,)).fetchone()
        if not row:
            raise Failure('PERMISSION_DENIED', '所发送的文件尚未选择', 403)
        source = json.loads(row['payload'])
        records.append({'resource_id': ref, 'name': source['name'], 'kind': source['kind'],
                        'preview_url': f'/resources/{ref}/image' if source['kind'] == 'file'
                        and Path(source['name']).suffix.lower() in IMAGE_SUFFIXES else None})
    return records


def advance_revision(store, engine, state, db, refs=None):
    task_id = state['task_id']
    previous = json.loads(db.execute('SELECT payload FROM specs WHERE task_id=? AND revision=?',
                                    (task_id, state['spec_revision'])).fetchone()[0])
    if state.get('delivery'):
        state.setdefault('delivery_history', []).append(state.pop('delivery'))
    prior = db.execute("SELECT id FROM turns WHERE task_id=? AND status='completed' AND id NOT IN "
        "(SELECT json_extract(payload,'$.run_id') FROM assignments) ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
    if prior:
        state['history_bridge_turn_id'] = prior['id']
    state['spec_revision'] += 1
    if refs is not None:
        claim_uploads(db, refs, task_id)
        if state['config_snapshot'].get('context_classification') == 'public':
            for ref in refs:
                source = json.loads(db.execute('SELECT payload FROM resources WHERE id=?', (ref,)).fetchone()[0])
                if source['classification'] != 'public':
                    raise Failure('PERMISSION_DENIED', '旧公开会话不能接收私密附件，请新建会话', 403)
        state['input_refs'] = refs
    db.execute('INSERT INTO specs VALUES(?,?,?)', (task_id, state['spec_revision'], encode({**previous, 'input_refs':state['input_refs']})))
    db.execute("UPDATE turns SET revision=? WHERE task_id=? AND status='queued'", (state['spec_revision'], task_id))
    engine.roles.cancel_obsolete(task_id, db, current_revision=state['spec_revision'])
    state.pop('attachment_pause',None)
    state.update(acceptance=None, blockers=[], pause_requested=False, cancellation_requested=False)
