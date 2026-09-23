from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import app as api

router = APIRouter()

@router.get('/workspace', response_model=dict)
def workspace_state(owner: str = Depends(api.identity), store=Depends(api.request_store)):
    api.workspaces.workspace(store)
    return {
        'chat_id': store.chat_id,
        'workspace': '.',
        'grants': api.workspaces.list_grants(owner, store.chat_id, active_only=False),
    }

@router.post('/workspace/grants', response_model=dict)
def create_grant(body: api.GrantRequest, owner: str = Depends(api.identity), store=Depends(api.request_store)):
    # Calling this explicit endpoint is the user's grant action; basis is retained for audit.
    return api.workspaces.grant(owner, store.chat_id, body.path, body.basis, body.access)

@router.delete('/workspace/grants/{grant_id}', response_model=dict)
def revoke_grant(grant_id: str, owner: str = Depends(api.identity), store=Depends(api.request_store)):
    return api.workspaces.revoke(owner, store.chat_id, grant_id)

@router.post('/workspace/files', response_model=dict)
async def upload_workspace_file(
    relative_path: str = Form(...), file: UploadFile = File(...),
    owner: str = Depends(api.identity), store=Depends(api.request_store),
):
    target = api.workspaces.workspace_path(store, relative_path)
    if target.exists():
        raise HTTPException(409, 'Workspace file already exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    limit = int(os.getenv('MAX_UPLOAD_BYTES', '536870912'))
    size = 0
    try:
        with target.open('xb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f'File exceeds {limit} bytes')
                output.write(chunk)
        return {'path': target.relative_to(api.workspaces.workspace(store)).as_posix(), 'size': size}
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
