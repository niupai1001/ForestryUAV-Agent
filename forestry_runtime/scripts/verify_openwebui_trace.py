"""Verify that Open WebUI receives Runtime thinking and trace markup."""
import json
from uuid import uuid4

from install_forestry_runtime import admin_client


def main():
    with admin_client() as web:
        web.timeout = 180
        message_id = str(uuid4())
        message = {
            'id': message_id,
            'role': 'user',
            'content': '请只回答：链路正常。',
            'parentId': None,
            'childrenIds': [],
        }
        response = web.post('/api/v1/chats/new', json={'chat': {
            'title': 'Forestry trace acceptance',
            'models': ['forestry_runtime'],
            'history': {
                'messages': {message_id: message},
                'currentId': message_id,
            },
            'messages': [message],
        }})
        response.raise_for_status()
        chat_id = response.json()['id']
        try:
            payload = {
                'model': 'forestry_runtime',
                'stream': True,
                'chat_id': chat_id,
                'messages': [{'role': 'user', 'content': message['content']}],
            }
            answer = ''
            reasoning = ''
            with web.stream('POST', '/api/chat/completions', json=payload) as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if not line.startswith('data: ') or line[6:] == '[DONE]':
                        continue
                    item = json.loads(line[6:])
                    if item.get('error'):
                        raise RuntimeError(str(item['error']))
                    delta = item.get('choices', [{}])[0].get('delta', {})
                    answer += delta.get('content', '') or ''
                    reasoning += delta.get('reasoning_content', '') or ''
            if not reasoning:
                raise AssertionError('Native reasoning_content was not emitted')
            if '链路正常' not in answer:
                raise AssertionError('Final answer was not emitted: ' + answer)
            if '<details' in answer or '&quot;' in answer:
                raise AssertionError('Trace markup leaked into message content')
            print('PASS: Open WebUI stream contains native reasoning_content')
            print('PASS: no trace HTML or tool JSON leaked into message content')
        finally:
            delete = web.delete('/api/v1/chats/' + chat_id)
            if delete.status_code not in (200, 404):
                delete.raise_for_status()


if __name__ == '__main__':
    main()
