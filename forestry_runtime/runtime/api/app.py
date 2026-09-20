import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import os
import logging
import secrets
from http.cookies import SimpleCookie
from pathlib import Path
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..storage import AssetError
from ..lifecycle import Sessions, SessionClosed, chat_uuid
from ..workspace import WorkspaceRegistry
from ..session.coordinator import RunCoordinator
from ..memory import MemoryManager
from .models import (
    AssetListResponse, AssetResponse, GrantRequest, KnowledgeSourceRequest,
    MemoryRequest, Message, ProjectRequest, ProjectSelection, RunEventsResponse,
    RunMessage, RunRequest, RunResponse, RunTurnsResponse, SessionRequest, Sources,
)


sessions = Sessions(os.getenv('DATA_ROOT', '/data'))
workspaces = WorkspaceRegistry(os.getenv('DATA_ROOT', '/data'))
memory = MemoryManager(os.getenv('DATA_ROOT', '/data'))
runs = RunCoordinator(sessions, workspaces, memory)
knowledge_tasks: set[asyncio.Task] = set()


def cleanup_chat_resources(chat_id: str):
    workspaces.cleanup_session(chat_id)
    runs.store.cleanup_chat(chat_id)
    memory.forget_session(chat_id)


sessions.cleanup = cleanup_chat_resources
log = logging.getLogger(__name__)
_EPHEMERAL_UI_SESSION_KEY = secrets.token_urlsafe(32)


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
        await runs.reconcile()
    except Exception:
        # A stale or temporarily unavailable durable job must not prevent the
        # HTTP service (including health and recovery controls) from starting.
        log.exception('Run reconciliation failed during startup')
    try:
        yield
    finally:
        task.cancel()
        await runs.shutdown()
        with suppress(asyncio.CancelledError):
            await task
        if knowledge_tasks:
            await asyncio.gather(*knowledge_tasks, return_exceptions=True)


app = FastAPI(title='Forestry Agent Runtime', version='0.10.0', lifespan=lifespan)
UI_COOKIE = 'forestry_runtime_ui'
APP_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = (
    APP_ROOT / 'frontend_dist'
    if (APP_ROOT / 'frontend_dist').is_dir()
    else APP_ROOT / 'frontend' / 'dist'
)


def _ui_session_key() -> str:
    return os.getenv('UI_SESSION_KEY', '') or _EPHEMERAL_UI_SESSION_KEY


def _identity(authorization: str, x_user_id: str, ui_cookie: str = '') -> str:
    key = os.getenv('RUNTIME_API_KEY', '')
    ui_key = _ui_session_key()
    if ui_cookie and hmac.compare_digest(ui_cookie, ui_key):
        return 'local'
    if not key or not hmac.compare_digest(authorization, 'Bearer ' + key):
        raise HTTPException(401, 'Invalid runtime API key')
    # The shared key is held only by trusted adapters. End users cannot choose another identity.
    if not x_user_id or len(x_user_id) > 200:
        raise HTTPException(400, 'Invalid owner identity')
    return x_user_id


def identity(
    request: Request, authorization: str = Header(default=''),
    x_user_id: str = Header(default=''),
):
    return _identity(
        authorization, x_user_id, request.cookies.get(UI_COOKIE, '')
    )


class SessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        scoped = (
            path == '/assets' or path.startswith('/assets/')
            or path == '/runs' or path.startswith('/runs/')
            or path == '/workspace' or path.startswith('/workspace/')
        )
        if scope['type'] != 'http' or not scoped:
            return await self.app(scope, receive, send)
        headers = {k.decode('latin-1'): v.decode('latin-1') for k, v in scope['headers']}
        try:
            cookie = SimpleCookie()
            cookie.load(headers.get('cookie', ''))
            ui_cookie = cookie[UI_COOKIE].value if UI_COOKIE in cookie else ''
            owner = _identity(
                headers.get('authorization', ''),
                headers.get('x-user-id', ''),
                ui_cookie,
            )
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














































































from .routes.assets import router as assets_router
from .routes.health import router as health_router
from .routes.projects import router as projects_router
from .routes.runs import router as runs_router
from .routes.sessions import router as sessions_router
from .routes.workspace import router as workspace_router

for router in (
    sessions_router, projects_router, assets_router, workspace_router,
    runs_router, health_router,
):
    app.include_router(router)


if (UI_ROOT / 'assets').is_dir():
    app.mount(
        '/ui/assets', StaticFiles(directory=UI_ROOT / 'assets'), name='ui-assets'
    )
