from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import app as api

router = APIRouter()

@router.get('/health', response_model=dict)
def health():
    return {
        'status': 'ok',
        'version': '0.10.0',
        'model': os.getenv('OLLAMA_MODEL', 'qwen3.5:4b'),
        'kernel': 'pydantic-ai',
        'remote_sensing_plugins': os.getenv('REMOTE_SENSING_PLUGINS_ENABLED', 'false').lower() == 'true',
    }

@router.get('/', include_in_schema=False)
def workbench_root():
    response = RedirectResponse('/ui/')
    response.set_cookie(
        api.UI_COOKIE, api._ui_session_key(), httponly=True,
        samesite='strict', secure=False,
    )
    return response

@router.get('/ui/', include_in_schema=False)
def ui_index():
    index = api.UI_ROOT / 'index.html'
    if index.is_file():
        response = FileResponse(index, media_type='text/html')
    else:
        response = HTMLResponse(
            '<h1>Forestry Agent UI is not built</h1>'
            '<p>Run <code>npm run build</code> in <code>frontend</code>, '
            'or use the Docker build.</p>',
            status_code=503,
        )
    response.set_cookie(
        api.UI_COOKIE, api._ui_session_key(), httponly=True,
        samesite='strict', secure=False,
    )
    return response
