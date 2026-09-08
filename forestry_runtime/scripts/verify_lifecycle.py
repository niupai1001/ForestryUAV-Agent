"""HTTP acceptance using only newly-created test chats/files. Run in Open WebUI."""
import json
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

import httpx

from install_forestry_runtime import admin_client

STATE = Path('/tmp/forestry_lifecycle_acceptance.json')


def checked(response):
    response.raise_for_status()
    return response.json()


def main():
    mode = sys.argv[1]
    with admin_client() as web:
        web.timeout = 240
        user = checked(web.get('/api/v1/auths/'))['id']
        valves = checked(web.get('/api/v1/functions/id/forestry_runtime/valves'))
        runtime = httpx.Client(base_url=valves['RUNTIME_URL'], timeout=40,
            headers={'Authorization': 'Bearer ' + valves['API_KEY'], 'X-User-ID': user})

        def create_chat(label, file=None):
            message_id = str(uuid4())
            message = {'id': message_id, 'role': 'user', 'content': 'Lifecycle acceptance',
                       'parentId': None, 'childrenIds': []}
            if file:
                message['files'] = [{'type': 'file', 'id': file['id'], 'file': file, 'name': file['filename']}]
            chat = checked(web.post('/api/v1/chats/new', json={'chat': {
                'title': 'Lifecycle acceptance ' + label, 'models': ['forestry_runtime'],
                'history': {'messages': {message_id: message}, 'currentId': message_id},
                'messages': [message]}}))
            return chat['id']

        def upload(name):
            return checked(web.post('/api/v1/files/', params={'process': 'false'},
                                    files={'file': (name, b'LIFECYCLE-OK\n', 'text/plain')}))

        def register(chat_id, file):
            checked(runtime.post('/sessions', json={'chat_id': chat_id}))
            checked(runtime.post(f'/sessions/{chat_id}/sources', json={'file_ids': [file['id']]}))
            return checked(runtime.post('/assets', headers={'X-Chat-ID': chat_id},
                files={'file': (file['filename'], b'LIFECYCLE-OK\n', 'text/plain')}))

        def wait_for(predicate, label):
            deadline = time.monotonic() + 55
            while time.monotonic() < deadline:
                if predicate():
                    print('PASS:', label, flush=True)
                    return
                time.sleep(1)
            raise AssertionError(label)

        if mode == 'cleanup-fixtures':
            # Only fixtures created by this acceptance script, never user chats.
            count = 0
            for session in checked(runtime.get('/lifecycle/sessions'))['sessions']:
                response = web.get('/api/v1/chats/' + session['id'])
                if response.status_code != 200:
                    continue
                record = response.json()
                chat = record.get('chat') or {}
                messages = (chat.get('history') or {}).get('messages') or {}
                if (record.get('title', '').startswith('Lifecycle acceptance ')
                        and any(m.get('content') == 'Lifecycle acceptance' for m in messages.values())):
                    checked(web.delete('/api/v1/chats/' + session['id']))
                    count += 1
            print('Deleted previous acceptance fixtures:', count, flush=True)

        elif mode == 'prepare':
            file = upload('lifecycle_original.txt')
            a = create_chat('A', file)
            b = create_chat('B shared reference', file)
            marker = register(b, file)
            STATE.write_text(json.dumps({'a': a, 'b': b, 'original': file['id'], 'outputs': [], 'marker': marker['id']}))
            prompt = '读取附件中的文字，再将它原样保存为 lifecycle_report.md。请实际执行工具。'
            attachment = {'type': 'file', 'id': file['id'], 'name': file['filename'], 'file': file, 'url': file['id']}
            payload = {'model': 'forestry_runtime', 'stream': True, 'chat_id': a,
                'messages': [{'role': 'user', 'content': prompt}], 'files': [attachment]}
            answer = ''
            with web.stream('POST', '/api/chat/completions', json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith('data: ') and line[6:] != '[DONE]':
                        item = json.loads(line[6:])
                        if item.get('error'):
                            raise RuntimeError(str(item['error']))
                        answer += item.get('choices', [{}])[0].get('delta', {}).get('content', '') or ''
            outputs = list(set(re.findall(r'/api/v1/files/([0-9a-f-]+)/content', answer)))
            if not outputs:
                raise AssertionError('No Pipe-generated artifact: ' + answer)
            for output in outputs:
                content = web.get(f'/api/v1/files/{output}/content')
                content.raise_for_status()
                assert 'LIFECYCLE-OK' in content.text
            STATE.write_text(json.dumps({'a': a, 'b': b, 'original': file['id'], 'outputs': outputs, 'marker': marker['id']}))
            print('PASS: real HTTP → Pipe → Qwen → tools → generated download; two chats registered', flush=True)

        elif mode == 'delete-a':
            state = json.loads(STATE.read_text())
            assert state['outputs'], 'No generated files to verify'
            deleted = web.delete('/api/v1/chats/' + state['a'])
            if deleted.status_code != 404:
                checked(deleted)
            wait_for(lambda: runtime.get('/assets', headers={'X-Chat-ID': state['a']}).status_code == 410,
                     'deleted chat rejects new asset access')
            wait_for(lambda: all(web.get(f'/api/v1/files/{f}/content').status_code == 404 for f in state['outputs']),
                     'generated Open WebUI copies deleted automatically')
            assert web.get(f"/api/v1/files/{state['original']}/content").status_code == 200
            assert runtime.get(f"/assets/{state['marker']}/content", headers={'X-Chat-ID': state['b']}).status_code == 200
            print('PASS: shared original and other chat Runtime assets retained', flush=True)

        elif mode == 'delete-b':
            state = json.loads(STATE.read_text())
            checked(web.delete('/api/v1/chats/' + state['b']))
            wait_for(lambda: web.get(f"/api/v1/files/{state['original']}/content").status_code == 404,
                     'shared original removed after last chat deletion')
            wait_for(lambda: not any(s['id'] in (state['a'], state['b']) for s in checked(runtime.get('/lifecycle/sessions'))['sessions']),
                     'both sessions cleanup acknowledged')

        elif mode == 'prepare-recovery':
            state = json.loads(STATE.read_text())
            file = upload('lifecycle_recovery.txt')
            c = create_chat('C restart recovery', file)
            register(c, file)
            state.update(c=c, recovery_file=file['id'])
            STATE.write_text(json.dumps(state))
            print('PASS: recovery fixture created', flush=True)

        elif mode == 'verify-archive':
            file = upload('lifecycle_archive.txt')
            chat_id = create_chat('D archive retention', file)
            asset = register(chat_id, file)
            checked(web.post('/api/v1/chats/' + chat_id + '/archive'))
            time.sleep(12)  # More than one cleanup interval.
            assert runtime.get(f"/assets/{asset['id']}/content", headers={'X-Chat-ID': chat_id}).status_code == 200
            assert web.get(f"/api/v1/files/{file['id']}/content").status_code == 200
            print('PASS: archived chat retains Runtime and Open WebUI files', flush=True)
            checked(web.delete('/api/v1/chats/' + chat_id))
            wait_for(lambda: web.get(f"/api/v1/files/{file['id']}/content").status_code == 404,
                     'deleting archived chat cleans its files')

        elif mode == 'delete-offline':
            state = json.loads(STATE.read_text())
            checked(web.delete('/api/v1/chats/' + state['c']))
            print('PASS: chat deleted while Runtime offline', flush=True)

        elif mode == 'verify-recovery':
            state = json.loads(STATE.read_text())
            wait_for(lambda: web.get(f"/api/v1/files/{state['recovery_file']}/content").status_code == 404,
                     'cleanup resumes after Open WebUI and Runtime restart')
            wait_for(lambda: not any(s['id'] == state['c'] for s in checked(runtime.get('/lifecycle/sessions'))['sessions']),
                     'recovery session cleanup acknowledged')
        else:
            raise ValueError(mode)


if __name__ == '__main__':
    main()
