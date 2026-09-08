"""
title: Forestry Runtime
author: Local
version: 0.3.1
description: Independent Qwen runtime with managed files and real file tools.
"""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import BaseModel, Field


def decode_image_data_url(url, max_bytes):
    """Decode in bounded chunks to a disk file, not another full-size RAM buffer."""
    comma = url.find(',')
    header = url[:comma] if comma >= 0 else ''
    if not header.startswith('data:image/') or not header.endswith(';base64'):
        raise ValueError('Unsupported image encoding')
    mime = header[5:].split(';')[0]
    suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
              'image/tiff': '.tif', 'image/x-tiff': '.tif'}.get(mime)
    if not suffix:
        raise ValueError('Unsupported image type')
    encoded_size = len(url) - comma - 1
    if encoded_size > 4 * ((max_bytes + 2) // 3):
        raise ValueError(f'Image exceeds the {max_bytes // 1048576} MiB file limit')
    output = tempfile.TemporaryFile(mode='w+b')
    digest = hashlib.sha256()
    size = 0
    try:
        for offset in range(comma + 1, len(url), 1048576):
            chunk = url[offset:offset + 1048576]
            if offset + 1048576 < len(url) and '=' in chunk:
                raise ValueError('Invalid base64 padding')
            decoded = base64.b64decode(chunk, validate=True)
            size += len(decoded)
            if size > max_bytes:
                raise ValueError(f'Image exceeds the {max_bytes // 1048576} MiB file limit')
            output.write(decoded)
            digest.update(decoded)
        output.seek(0)
        return output, digest.hexdigest(), mime, suffix
    except BaseException:
        output.close()
        raise


class Pipe:
    class Valves(BaseModel):
        RUNTIME_URL: str = 'http://host.docker.internal:8010'
        API_KEY: str = Field(default='', json_schema_extra={'input': {'type': 'password'}})
        MAX_FILE_MB: int = Field(default=512, ge=1, description='Inline image limit in MiB; keep aligned with runtime MAX_UPLOAD_BYTES.')
        MANAGED_SINCE: int = Field(default=0, description='Deployment timestamp; do not manage older chats or original files.')

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(self, body: dict, __user__: dict, __files__: list = None,
                   __request__=None, __event_emitter__=None, __task__=None, __metadata__: dict = None):
        from open_webui.models.files import Files
        from open_webui.models.users import Users
        from open_webui.storage.provider import Storage
        from open_webui.routers.files import upload_file_handler
        from starlette.datastructures import UploadFile, Headers
        from open_webui.models.chats import Chat, ChatFile
        from open_webui.internal.db import get_async_db
        from sqlalchemy import select

        user = await Users.get_user_by_id(__user__['id'])
        if not user:
            raise ValueError('Unknown Open WebUI user')
        try:
            chat_id = str(UUID(str((__metadata__ or {}).get('chat_id'))))
        except (ValueError, TypeError, AttributeError):
            if __task__:
                return
            raise ValueError('请新建普通聊天；文件生命周期管理不支持临时聊天或缺少chat_id的请求。')
        async with get_async_db() as db:
            chat_record = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalars().first()
            if not chat_record or chat_record.user_id != user.id:
                raise ValueError('Chat missing or belongs to another user')
            if chat_record.created_at < self.valves.MANAGED_SINCE:
                if __task__:
                    return
                raise ValueError('此版本仅管理更新后新建的聊天。请新建聊天上传影像，旧聊天不迁移。')
            source_ids = (await db.execute(select(ChatFile.file_id).where(ChatFile.chat_id == chat_id))).scalars().all()
        headers = {'Authorization': 'Bearer ' + self.valves.API_KEY, 'X-User-ID': user.id, 'X-Chat-ID': chat_id}
        base = self.valves.RUNTIME_URL.rstrip('/')
        attached = {}
        known_content = {}
        image_counter = 0

        async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(600, connect=10)) as client:
            response = await client.post(base + '/sessions', json={'chat_id': chat_id})
            response.raise_for_status()

            async def register_source(file_id):
                record = await Files.get_file_by_id(file_id)
                if not record or record.user_id != user.id or (record.created_at or 0) < self.valves.MANAGED_SINCE:
                    return
                response = await client.post(base + '/sessions/' + chat_id + '/sources', json={'file_ids': [file_id]})
                response.raise_for_status()

            if not __task__:
                for source_id in source_ids:
                    await register_source(source_id)

            async def transfer(file_id):
                if file_id in attached:
                    return
                record = await Files.get_file_by_id(file_id)
                # Deliberately require ownership, including for admin chat sessions.
                if not record or record.user_id != user.id:
                    raise ValueError('Attachment missing or belongs to another user')
                await register_source(file_id)
                path = await asyncio.to_thread(Storage.get_file, record.path)
                with Path(path).open('rb') as stream:
                    response = await client.post(base + '/assets', files={'file': (record.filename, stream, (record.meta or {}).get('content_type') or 'application/octet-stream')})
                response.raise_for_status()
                asset = response.json()
                attached[file_id] = asset['id']
                known_content[asset['sha256']] = asset['id']

            async def image_asset(url):
                nonlocal image_counter
                if url.startswith('data:image/'):
                    stream, checksum, mime, suffix = await asyncio.to_thread(
                        decode_image_data_url, url, self.valves.MAX_FILE_MB * 1048576)
                    try:
                        # Open WebUI can send the same picture as both file metadata and base64.
                        # Reuse the already uploaded original; do not create chat_image duplicates.
                        if checksum in known_content:
                            attached['inline:' + checksum] = known_content[checksum]
                            return
                        image_counter += 1
                        response = await client.post(base + '/assets', files={'file': (f'chat_image_{image_counter}{suffix}', stream, mime)})
                        response.raise_for_status()
                        asset = response.json()
                        attached['inline:' + checksum] = asset['id']
                        known_content[checksum] = asset['id']
                    finally:
                        stream.close()
                else:
                    match = re.search(r'/api/v1/files/([0-9a-f-]+)/content', urlparse(url).path)
                    if match:
                        await transfer(match.group(1))
                        return
                    raise ValueError('External image URLs are not fetched; upload the image as an attachment')

            if not __task__:
                for item in (__files__ or body.get('files') or []):
                    record = item.get('file') or {}
                    file_id = record.get('id') or item.get('id')
                    if file_id:
                        await transfer(file_id)
                    elif item.get('type') == 'image' and item.get('url'):
                        await image_asset(item['url'])
                    else:
                        raise ValueError('Attachment has no resolvable file ID')

            messages = []
            for message in body.get('messages', []):
                if message.get('role') not in ('user', 'assistant', 'system'):
                    continue
                value = message.get('content') or ''
                if isinstance(value, list):
                    pieces = []
                    for part in value:
                        if part.get('type') == 'text':
                            pieces.append(part.get('text', ''))
                        elif part.get('type') == 'image_url' and not __task__:
                            image = part.get('image_url', {})
                            await image_asset(image if isinstance(image, str) else image.get('url', ''))
                    value = '\n'.join(pieces)
                # Generated file links in previous turns remain usable after a page reload.
                if not __task__ and message.get('role') == 'assistant':
                    for file_id in re.findall(r'/api/v1/files/([0-9a-f-]+)/content', value):
                        await transfer(file_id)
                    # Runtime轨迹用于界面显示，不作为下一轮模型输入的一部分。
                    value = re.sub(
                        r'<details data-forestry-trace="true".*?</details>\s*',
                        '', value, flags=re.DOTALL,
                    )
                messages.append({'role': message['role'], 'content': value})

            async def output():
                async with client.stream('POST', base + '/chat', json={'messages': messages, 'asset_ids': list(set(attached.values())), 'stream': True, 'use_tools': not bool(__task__)}) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if event['type'] == 'thinking':
                            # Return OpenAI-compatible reasoning instead of embedding
                            # HTML in message content. Open WebUI renders this using
                            # its native reasoning panel and keeps it out of正文.
                            yield {
                                'choices': [{
                                    'index': 0,
                                    'delta': {
                                        'reasoning_content': event.get('content', '')
                                    },
                                    'finish_reason': None,
                                }]
                            }
                        elif event['type'] == 'tool_start':
                            if __event_emitter__:
                                await __event_emitter__({
                                    'type': 'status',
                                    'data': {
                                        'description': (
                                            '正在执行工具：'
                                            + event.get('name', '')
                                        ),
                                        'done': False,
                                    },
                                })
                        elif event['type'] == 'tool_end':
                            if __event_emitter__:
                                status = '完成' if event.get('ok') else '失败'
                                await __event_emitter__({
                                    'type': 'status',
                                    'data': {
                                        'description': (
                                            f'工具{status}：'
                                            f'{event.get("name", "")}（'
                                            f'{event.get("duration_seconds", 0)}秒）'
                                        ),
                                        'done': True,
                                    },
                                })
                        elif event['type'] in ('message', 'error'):
                            yield event['content']
                        elif event['type'] == 'done':
                            for asset in {a['id']: a for a in event['artifacts']}.values():
                                download = await client.get(base + '/assets/' + asset['id'] + '/content')
                                download.raise_for_status()
                                uploaded = UploadFile(filename=asset['name'], file=io.BytesIO(download.content), headers=Headers({'content-type': asset.get('media_type') or 'application/octet-stream'}))
                                try:
                                    result = await upload_file_handler(__request__, file=uploaded,
                                        metadata={'forestry_cleanup_chat': chat_id, 'forestry_cleanup_since': self.valves.MANAGED_SINCE},
                                        process=False, process_in_background=False, user=user)
                                finally:
                                    await uploaded.close()
                                file_id = result['id'] if isinstance(result, dict) else result.id
                                await register_source(file_id)
                                label = asset['name'].replace('[', '').replace(']', '').replace('\n', '')
                                url = f'/api/v1/files/{file_id}/content'
                                yield f'\n\n[{label}]({url}?attachment=true)'
                                if asset.get('media_type') == 'image/png':
                                    yield f'\n\n![预览]({url})'
            # Yielding keeps the HTTP client alive for the entire upstream stream.
            async for chunk in output():
                yield chunk
