"""Explicit session erasure, restricted to quiescent, application-owned data."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import UUID

from .contracts import Control, Failure
from .store import encode, now


class DeleteSession(Control):
    confirm_delete: Literal[True]


class Sessions:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store
        self.root = engine.resources.workspace.resolve()

    def _plan(self, db, task_id, state):
        resources = {r['id']:json.loads(r['payload']) for r in db.execute('SELECT id,payload FROM resources')}
        refs, shared = set(state['input_refs']), set()
        for row in db.execute('SELECT task_id,payload FROM specs'):
            (refs if row['task_id']==task_id else shared).update(json.loads(row['payload']).get('input_refs',[]))
        refs.update(ref for ref,value in resources.items() if value.get('owner_task_id')==task_id)
        for row in db.execute('SELECT payload FROM knowledge_imports'):
            shared.add(json.loads(row['payload'])['citation']['resource_id'])
        # Derived children share their parent's source; count them on both sides.
        for _ in range(len(resources)+1):
            old = (len(refs),len(shared))
            for ref,value in resources.items():
                if value.get('parent_resource_id') in refs:refs.add(ref)
                if value.get('parent_resource_id') in shared:shared.add(ref)
            if old==(len(refs),len(shared)):break
        removed = refs-shared
        paths = [self.root/'tasks'/task_id, self.root/'.forestry/model-inputs'/task_id,
                 self.root/'.forestry/tool-results'/task_id,self.root/'.forestry/image-previews'/task_id]
        paths.extend(self.root/'.forestry/tool-results'/row[0] for row in
                     db.execute('SELECT id FROM assignments WHERE task_id=?',(task_id,)))
        for ref in removed:
            value = resources.get(ref,{})
            if value.get('upload_draft_id') and value.get('owner_task_id')==task_id:
                location = Path(value['locator'])
                if not location.is_relative_to(self.root/'uploads'):
                    raise Failure('PERMISSION_DENIED','上传副本目录已改变，不能自动删除',409)
                paths.append(location.parent)
        # Protect aliases to owned results/uploads, even when separately registered.
        for ref,value in resources.items():
            if ref in removed:continue
            source=Path(value['locator'])
            if any(source.is_relative_to(path) for path in paths):
                if ref in shared:
                    raise Failure('SESSION_SHARED_DATA','此会话的文件仍被其他会话或知识库引用，请先移除对应引用再删除',409)
                removed.add(ref)  # An unused alias to this session's own output is erased too.
        hashes = {value.get('upload_sha256') for ref,value in resources.items() if ref in removed}
        hashes.update(json.loads(row[0]).get('sha256') for row in
                      db.execute('SELECT payload FROM artifacts WHERE task_id=?',(task_id,)))
        # Any remaining registration with the same image bytes protects its preview.
        retained_hashes={v.get('upload_sha256') for ref,v in resources.items() if ref not in removed}
        for row in db.execute('SELECT payload FROM artifacts WHERE task_id!=?',(task_id,)):
            retained_hashes.add(json.loads(row[0]).get('sha256'))
        for row in db.execute("SELECT payload FROM events WHERE task_id=? AND kind='action.observed'",(task_id,)):
            def collect(value):
                if isinstance(value,dict):
                    if isinstance(value.get('source_sha256'),str):hashes.add(value['source_sha256'])
                    for child in value.values():collect(child)
                elif isinstance(value,list):
                    for child in value:collect(child)
            collect(json.loads(row[0]))
        # Global preview names are hashes. Unknown aliases retain their caches conservatively.
        for sha in hashes-retained_hashes-{None}:
            if len(sha)==64 and all(c in '0123456789abcdef' for c in sha):
                paths.append(self.root/'.forestry/image-previews'/sha)
        turns=[r[0] for r in db.execute('SELECT id FROM turns WHERE task_id=?',(task_id,))]
        runs=[r[0] for r in db.execute('SELECT id FROM agent_runs WHERE turn_id IN '
             '(SELECT id FROM turns WHERE task_id=?)',(task_id,))]
        return {'task_id':task_id,'resource_ids':sorted(removed),'paths':[str(p) for p in paths],
                'turn_ids':turns,'run_ids':runs,'shared_sources':len(refs&shared)}

    def _remove_path(self, raw):
        path=Path(raw)
        # Verify the resolved absolute recursive target before every removal. Never
        # follow an uploaded-source or runtime root that has been replaced by a link.
        roots=[self.root/'tasks',self.root/'uploads',self.root/'.forestry/model-inputs',
               self.root/'.forestry/tool-results',self.root/'.forestry/image-previews']
        if (not path.is_absolute() or not any(path!=r and path.is_relative_to(r) for r in roots)
                or path.resolve()!=path or not path.resolve().is_relative_to(self.root)):
            raise Failure('PERMISSION_DENIED','会话清理路径发生重定向，已停止删除',409)
        if path.is_dir():shutil.rmtree(path)
        elif path.exists():path.unlink()

    def _erase_rows(self, db, plan):
        task_id=plan['task_id']
        resource_events=[]
        for ref in plan['resource_ids']:
            row=db.execute('SELECT event_id FROM resources WHERE id=?',(ref,)).fetchone()
            if row:resource_events.append(row[0])
            for chunk in db.execute('SELECT id FROM knowledge_chunks WHERE resource_id=?',(ref,)).fetchall():
                db.execute('DELETE FROM knowledge_fts WHERE chunk_id=?',(chunk[0],))
            db.execute('DELETE FROM knowledge_chunks WHERE resource_id=?',(ref,))
            db.execute('DELETE FROM resources WHERE id=?',(ref,))
        # Receipts may include the entire old task or resource. Erase those too,
        # preventing task-create or upload request replay from resurrecting it.
        identifiers=[task_id,*plan['resource_ids'],*plan['turn_ids'],*plan['run_ids']]
        for row in db.execute('SELECT id,response FROM requests').fetchall():
            if any(identifier in row['response'] for identifier in identifiers):
                db.execute('DELETE FROM requests WHERE id=?',(row['id'],))
        event_ids=set(resource_events)
        event_ids.update(r[0] for r in db.execute('SELECT user_event_id FROM turn_requests WHERE turn_id IN '
                         '(SELECT id FROM turns WHERE task_id=?)',(task_id,)))
        event_ids.update(r[0] for r in db.execute('SELECT user_event_id FROM approvals WHERE task_id=?',(task_id,)) if r[0])
        state=json.loads(db.execute('SELECT state FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
        event_ids.add(state['user_event_id'])
        db.execute('DELETE FROM attempts WHERE action_id IN (SELECT id FROM actions WHERE task_id=?)',(task_id,))
        db.execute('DELETE FROM turn_requests WHERE turn_id IN (SELECT id FROM turns WHERE task_id=?)',(task_id,))
        db.execute('DELETE FROM agent_runs WHERE turn_id IN (SELECT id FROM turns WHERE task_id=?)',(task_id,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='native_plan_items'").fetchone():
            db.execute('DELETE FROM native_plan_items WHERE json_extract(session,\'$[0]\')=?',(task_id,))
        for table in ('progress_observations','approvals','contexts','deliveries','assignments',
                      'jobs','artifacts','actions','events','specs','turns'):
            db.execute(f'DELETE FROM {table} WHERE task_id=?',(task_id,))
        db.execute('DELETE FROM tasks WHERE id=?',(task_id,))
        # Remove human input/control payloads without touching unrelated global grants.
        event_ids.update(r['id'] for r in db.execute('SELECT id,payload FROM user_events')
                         if any(identifier in r['payload'] for identifier in identifiers))
        for event_id in event_ids:
            # Explicit global memories and knowledge imports have an independent lifetime.
            linked=[('resources','event_id'),('memories','event_id'),('memory_versions','event_id'),
                    ('knowledge_imports','event_id'),('turn_requests','user_event_id'),('approvals','user_event_id')]
            if not any(db.execute(f'SELECT 1 FROM {table} WHERE {column}=?',(event_id,)).fetchone()
                       for table,column in linked):
                db.execute('DELETE FROM user_events WHERE id=?',(event_id,))
        native=self.root/'.forestry/model-steps.sqlite'
        if native.exists():
            db.execute('ATTACH DATABASE ? AS native_steps',(str(native),))
            ids=set(plan['run_ids']+plan['turn_ids'])
            ids.update(r[0] for r in db.execute('SELECT run_id FROM native_steps.runs WHERE conversation_id=?',(task_id,)))
            while True:
                children={r['run_id'] for r in db.execute('SELECT run_id,parent_run_id FROM native_steps.runs')
                          if r['parent_run_id'] in ids}
                if children<=ids:break
                ids.update(children)
            media=set()
            for run_id in ids:
                for row in db.execute('SELECT messages FROM native_steps.snapshots WHERE run_id=?',(run_id,)):
                    media.update(re.findall(r'media\+sha256://([0-9a-f]{64})',row[0]))
                for table in ('snapshot_idempotency_keys','tool_effects','snapshots','events','runs'):
                    db.execute(f'DELETE FROM native_steps.{table} WHERE run_id=?',(run_id,))
            # Media blobs are shared by hash; retain every blob referenced by any
            # surviving snapshot, including text spill blobs and other sessions' images.
            for sha in media:
                db.execute("DELETE FROM native_steps.media WHERE sha256=? AND NOT EXISTS "
                           "(SELECT 1 FROM native_steps.snapshots WHERE instr(messages,?)>0)",
                           (sha,'media+sha256://'+sha))

    def delete(self, task_id, payload):
        try:UUID(task_id)
        except ValueError:raise Failure('NOT_FOUND','会话不存在',404) from None
        with self.engine.jobs.cleanup_lock, self.store.lock:
            with self.store.transaction() as db:
                pending=db.execute('SELECT payload FROM session_deletions WHERE task_id=?',(task_id,)).fetchone()
                if pending and json.loads(pending[0]).get('done'):return {'task_id':task_id,'deleted':True}
                if pending:
                    plan=json.loads(pending[0])
                else:
                    state=self.store.read_task(db,task_id)
                    if state['state_version']!=payload.expected_state_version:
                        raise Failure('VERSION_CONFLICT','会话已变化，请刷新后重新确认删除',409)
                    active=self.engine.queue.get(task_id)
                    if ((active and not active.done()) or state['status']=='publishing' or
                        db.execute("SELECT 1 FROM jobs WHERE task_id=? AND status NOT IN ('succeeded','failed','cancelled','timed_out')",(task_id,)).fetchone() or
                        db.execute("SELECT 1 FROM assignments WHERE task_id=? AND status='running'",(task_id,)).fetchone() or
                        db.execute("SELECT 1 FROM actions WHERE task_id=? AND status='pending'",(task_id,)).fetchone()):
                        raise Failure('SESSION_BUSY','会话仍在执行或有未结清作业，请先停止并等待作业结束后删除',409)
                    if any(not run.done() for other,run in self.engine.queue.items() if other!=task_id):
                        # Native media insertion precedes snapshot insertion. Do not
                        # collect a shared blob while another run is between them.
                        raise Failure('SESSION_BUSY','其他会话正在使用模型记录，请在该轮回复或停止后再删除',409)
                    plan=self._plan(db,task_id,state)
                    db.execute('INSERT INTO session_deletions VALUES(?,?)',(task_id,encode(plan)))
                    state['status']='deleting'
                    db.execute('UPDATE tasks SET status=?,state=? WHERE id=?',('deleting',encode(state),task_id))
            try:
                with self.store.transaction() as db:
                    jobs=[json.loads(row[0]) for row in db.execute('SELECT payload FROM jobs WHERE task_id=?',(task_id,))]
                for job in jobs:self.engine.jobs.cleanup_terminal(job,discard=True)
                for path in plan['paths']:self._remove_path(path)
                with self.store.transaction() as db:
                    self._erase_rows(db,plan)
                    db.execute('UPDATE session_deletions SET payload=? WHERE task_id=?',
                               (encode({'done':True,'at':now()}),task_id))
            except (OSError,sqlite3.Error,KeyError,ValueError) as error:
                raise Failure('SESSION_CLEANUP_PENDING','部分文件无法清理；会话已冻结，释放占用后再次点击删除即可继续',409) from error
            self.engine.queue.pop(task_id,None)
            self.engine.tokens.pop(task_id,None)
            self.engine.model_features.pop(task_id,None)
            return {'task_id':task_id,'deleted':True}

    def recover(self):
        with self.store.transaction() as db:
            pending=[r[0] for r in db.execute("SELECT task_id FROM session_deletions WHERE json_extract(payload,'$.done') IS NULL")]
        for task_id in pending:
            try:self.delete(task_id,DeleteSession(request_id='recover-delete',expected_state_version=1,confirm_delete=True))
            except Failure:pass  # Keep the frozen marker so a later explicit retry can finish.
