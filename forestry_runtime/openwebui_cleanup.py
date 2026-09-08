"""
title: Forestry Chat Asset Cleanup
author: Local
version: 0.2.0
description: Server-side chat asset cleanup with restart recovery; not an LLM tool.
"""
import asyncio
import logging
import time

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, delete, or_, select, text

log = logging.getLogger(__name__)


class Event:
    class Valves(BaseModel):
        RUNTIME_URL: str = 'http://host.docker.internal:8010'
        API_KEY: str = Field(default='', json_schema_extra={'input': {'type': 'password'}})
        MANAGED_SINCE: int = Field(default=0)
        INTERVAL_SECONDS: int = Field(default=10, ge=2, le=300)

    def __init__(self):
        self.valves = self.Valves()

    async def delete_file(self, file_id, owner, managed_since):
        from open_webui.internal.db import Base, get_async_db
        from open_webui.models.chats import Chat, ChatFile
        from open_webui.models.files import File
        from open_webui.storage.provider import Storage
        from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

        async with get_async_db() as db:
            if db.bind.dialect.name != 'sqlite':
                raise RuntimeError('This lifecycle integration requires SQLite')
            # Exclude concurrent new references between the check and deletion.
            await db.execute(text('BEGIN IMMEDIATE'))
            record = (await db.execute(select(File).where(File.id == file_id))).scalars().first()
            if record is None:
                await db.rollback()
                return True
            if record.user_id != owner or (record.created_at or 0) < managed_since:
                await db.rollback()
                # Intentionally outside this deployment's cleanup scope.
                return True
            live = await db.scalar(select(ChatFile.id).join(Chat, Chat.id == ChatFile.chat_id)
                                   .where(ChatFile.file_id == file_id).limit(1))
            if live:
                await db.rollback()
                return False
            # Include JSON links in chat forks, shared snapshots, knowledge, folders,
            # channels and other loaded resource tables. Ambiguous references retain.
            for table in Base.metadata.tables.values():
                if table.name in {'file', 'chat_file'}:
                    continue
                conditions = [cast(column, String).contains(file_id, autoescape=True) for column in table.columns]
                if conditions and await db.scalar(select(1).select_from(table).where(or_(*conditions)).limit(1)):
                    await db.rollback()
                    return False
            try:
                await asyncio.to_thread(Storage.delete_file, record.path)
            except FileNotFoundError:
                pass
            await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=f'file-{file_id}')
            await db.execute(delete(ChatFile).where(ChatFile.file_id == file_id))
            await db.execute(delete(File).where(File.id == file_id))
            await db.commit()
            log.info('Deleted Open WebUI file %s', file_id)
            return True

    async def cycle(self, valves):
        from open_webui.internal.db import get_async_db
        from open_webui.models.chats import Chat
        from open_webui.models.files import File

        if not valves.API_KEY or not valves.MANAGED_SINCE:
            return
        async with httpx.AsyncClient(base_url=valves.RUNTIME_URL.rstrip('/'),
                headers={'Authorization': 'Bearer ' + valves.API_KEY}, timeout=30) as client:
            response = await client.get('/lifecycle/sessions')
            response.raise_for_status()

            # Recover output files created before Pipe could register their returned ID.
            # Only explicitly tagged outputs from this deployment are candidates.
            async with get_async_db() as db:
                tagged = (await db.execute(select(File).where(
                    cast(File.meta, String).contains('forestry_cleanup_chat'),
                    File.created_at >= valves.MANAGED_SINCE))).scalars().all()
                recover = []
                for record in tagged:
                    data = (record.meta or {}).get('data') or {}
                    chat_id = data.get('forestry_cleanup_chat')
                    if not chat_id or data.get('forestry_cleanup_since') != valves.MANAGED_SINCE:
                        continue
                    if await db.scalar(select(Chat.id).where(Chat.id == chat_id)) is None:
                        recover.append((chat_id, record.user_id, record.id))
            for chat_id, owner, file_id in recover:
                registered = await client.post(f'/sessions/{chat_id}/sources',
                    headers={'X-User-ID': owner}, json={'file_ids': [file_id]})
                registered.raise_for_status()

            if recover:
                response = await client.get('/lifecycle/sessions')
                response.raise_for_status()
            for session in response.json()['sessions']:
                chat_id, owner = session['id'], session['owner']
                async with get_async_db() as db:
                    # A query error is an error, never evidence that a chat is gone.
                    exists = await db.scalar(select(Chat.id).where(Chat.id == chat_id))
                if exists is not None:
                    continue
                headers = {'X-User-ID': owner}
                if session['state'] != 'deleted':
                    deleted = await client.delete(f'/sessions/{chat_id}', headers=headers)
                    deleted.raise_for_status()
                    continue
                finished = []
                for file_id in session['file_ids']:
                    try:
                        if await self.delete_file(file_id, owner, valves.MANAGED_SINCE):
                            finished.append(file_id)
                    except Exception:
                        log.exception('File cleanup deferred: %s', file_id)
                acknowledged = await client.post(f'/sessions/{chat_id}/ack', headers=headers,
                                                json={'file_ids': finished})
                acknowledged.raise_for_status()

    async def event(self, event: dict, __id__: str = None, __app__=None, **kwargs):
        if __app__ is None or __id__ is None:
            return
        from open_webui.models.functions import Functions

        state = __app__.state
        task = getattr(state, 'forestry_cleanup_task', None)
        if task is None or task.done():
            wake = asyncio.Event()
            state.forestry_cleanup_wake = wake

            async def worker():
                while True:
                    interval = 10
                    try:
                        function = await Functions.get_function_by_id(__id__)
                        if not function or not function.is_active:
                            return
                        saved = await Functions.get_function_valves_by_id(__id__)
                        valves = self.Valves(**(saved or {}))
                        interval = valves.INTERVAL_SECONDS
                        await self.cycle(valves)
                        state.forestry_cleanup_last_success = time.time()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception('Forestry cleanup deferred; will retry')
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass
                    wake.clear()

            state.forestry_cleanup_task = asyncio.create_task(worker())
            log.info('Started Forestry cleanup worker')
        if event.get('event') in {'chat.deleted', 'chat.deleted_all', 'system.startup.completed',
                                  'function.enabled', 'function.valves_updated'}:
            state.forestry_cleanup_wake.set()
