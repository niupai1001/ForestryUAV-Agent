import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import json
import os
import logging
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from .agent import run_agent
from .storage import AssetError
from .lifecycle import Sessions, SessionClosed, chat_uuid
from .tools import Toolbox, schemas


sessions = Sessions(os.getenv('DATA_ROOT', '/data'))
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    if len(os.getenv('RUNTIME_API_KEY', '')) < 24:
        raise RuntimeError('Set RUNTIME_API_KEY to a random secret of at least 24 characters')
    async def cleaner():
        while True:
            try:
                await asyncio.to_thread(sessions.reap)
            except Exception:
                log.exception('Session cleanup worker failed')
            await asyncio.sleep(2)
    task = asyncio.create_task(cleaner())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title='Forestry Runtime', version='0.3.0', lifespan=lifespan)


def identity(authorization: str = Header(default=''), x_user_id: str = Header(default='local')):
    key = os.getenv('RUNTIME_API_KEY', '')
    if not key or not hmac.compare_digest(authorization, 'Bearer ' + key):
        raise HTTPException(401, 'Invalid runtime API key')
    # The shared key is held only by trusted adapters. End users cannot choose another identity.
    if not x_user_id or len(x_user_id) > 200:
        raise HTTPException(400, 'Invalid owner identity')
    return x_user_id


class SessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        scoped = path == '/chat' or path == '/assets' or path.startswith('/assets/') or path == '/tools/execute'
        if scope['type'] != 'http' or not scoped:
            return await self.app(scope, receive, send)
        headers = {k.decode('latin-1'): v.decode('latin-1') for k, v in scope['headers']}
        try:
            owner = identity(headers.get('authorization', ''), headers.get('x-user-id', 'local'))
            chat_id = chat_uuid(headers.get('x-chat-id'))
            store = sessions.acquire(owner, chat_id)
        except (AssetError, HTTPException) as exc:
            status = exc.status_code if isinstance(exc, HTTPException) else (410 if isinstance(exc, SessionClosed) else 400)
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return await JSONResponse({'detail': detail}, status_code=status)(scope, receive, send)
        scope.setdefault('state', {})['store'] = store
        try:
            await self.app(scope, receive, send)
        finally:
            sessions.release(chat_id)


app.add_middleware(SessionMiddleware)


def request_store(request: Request):
    return request.state.store


@app.exception_handler(AssetError)
async def asset_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=410 if isinstance(exc, SessionClosed) else 400)


class SessionRequest(BaseModel):
    chat_id: str


class Sources(BaseModel):
    file_ids: list[str] = Field(default_factory=list, max_length=1000)


@app.post('/sessions')
def create_session(body: SessionRequest, owner=Depends(identity)):
    return sessions.create(owner, body.chat_id)


@app.get('/lifecycle/sessions')
def pending_sessions(owner=Depends(identity)):
    # Shared key is a trusted service credential, as for the existing identity API.
    return {'sessions': sessions.pending()}


@app.delete('/sessions/{chat_id}')
def delete_session(chat_id: str, owner=Depends(identity)):
    return sessions.mark_deleted(owner, chat_id)


@app.post('/sessions/{chat_id}/sources')
def source_files(chat_id: str, body: Sources, owner=Depends(identity)):
    for file_id in body.file_ids:
        sessions.source(owner, chat_id, file_id)
    return {'ok': True}


@app.post('/sessions/{chat_id}/ack')
def acknowledge_session(chat_id: str, body: Sources, owner=Depends(identity)):
    return sessions.acknowledge(owner, chat_id, body.file_ids)


@app.get('/health')
def health():
    return {'status': 'ok', 'version': '0.3.0', 'model': os.getenv('OLLAMA_MODEL', 'qwen3.5:4b')}


@app.post('/assets')
async def upload(file: UploadFile = File(...), owner: str = Depends(identity), store=Depends(request_store)):
    try:
        return await asyncio.to_thread(store.put, file.file, file.filename, owner, file.content_type)
    except AssetError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await file.close()


@app.get('/assets')
def assets(owner: str = Depends(identity), store=Depends(request_store)):
    return {'assets': store.list(owner)}


@app.get('/assets/{asset_id}')
def metadata(asset_id: str, owner: str = Depends(identity), store=Depends(request_store)):
    try:
        return store.get(asset_id, owner)
    except AssetError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/assets/{asset_id}/content')
def content(asset_id: str, owner: str = Depends(identity), store=Depends(request_store)):
    asset = metadata(asset_id, owner, store)
    return FileResponse(store.path(asset_id, owner), filename=asset['name'], media_type='application/octet-stream')


@app.get('/tools')
def tools(owner: str = Depends(identity)):
    return {'tools': schemas()}


class ToolRequest(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)
    asset_ids: list[str] = Field(default_factory=list, max_length=1000)


@app.post('/tools/execute')
async def execute(body: ToolRequest, owner: str = Depends(identity), store=Depends(request_store)):
    try:
        box = Toolbox(store, owner, body.asset_ids)
    except AssetError as exc:
        raise HTTPException(404, str(exc)) from exc
    return await asyncio.to_thread(box.execute, body.name, body.arguments)


class Message(BaseModel):
    role: Literal['system', 'user', 'assistant']
    content: str = Field(max_length=100000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    asset_ids: list[str] = Field(default_factory=list, max_length=1000)
    stream: bool = False
    use_tools: bool = True


@app.post('/chat')
async def chat(body: ChatRequest, owner: str = Depends(identity), store=Depends(request_store)):
    for asset_id in body.asset_ids:
        metadata(asset_id, owner, store)
    if sum(len(m.content) for m in body.messages) > 60000:
        raise HTTPException(413, 'Conversation too long for this prototype; start a new chat')

    async def events():
        async for event in run_agent(store, owner, body.asset_ids, [m.model_dump() for m in body.messages], use_tools=body.use_tools):
            yield event

    if body.stream:
        async def lines():
            async for event in events():
                yield json.dumps(event, ensure_ascii=False) + '\n'
        return StreamingResponse(lines(), media_type='application/x-ndjson')
    received = [event async for event in events()]
    return {'content': '\n'.join(e['content'] for e in received if e['type'] in ('message', 'error')),
            'artifacts': [a for e in received if e['type'] == 'done' for a in e['artifacts']],
            'ok': not any(e['type'] == 'error' for e in received)}
