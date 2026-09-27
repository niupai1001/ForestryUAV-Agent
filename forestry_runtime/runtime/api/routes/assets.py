from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import app as api

router = APIRouter()


class HeldFileResponse(FileResponse):
    """Release a lifecycle hold after the ASGI send succeeds or aborts."""

    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        self._release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


@router.post('/assets', response_model=api.AssetResponse)
async def upload(file: UploadFile = File(...), owner: str = Depends(api.identity), store=Depends(api.request_store)):
    try:
        return await asyncio.to_thread(store.put, file.file, file.filename, owner, file.content_type)
    except api.AssetError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await file.close()

@router.get('/assets', response_model=api.AssetListResponse)
def assets(owner: str = Depends(api.identity), store=Depends(api.request_store)):
    return {'assets': store.list(owner)}

@router.get('/assets/{asset_id}', response_model=api.AssetResponse)
def metadata(asset_id: str, owner: str = Depends(api.identity), store=Depends(api.request_store)):
    try:
        return store.get(asset_id, owner)
    except api.AssetError as exc:
        raise HTTPException(404, str(exc)) from exc

@router.get('/assets/{asset_id}/content')
def content(asset_id: str, owner: str = Depends(api.identity), store=Depends(api.request_store)):
    asset = metadata(asset_id, owner, store)
    return FileResponse(store.path(asset_id, owner), filename=asset['name'], media_type='application/octet-stream')

@router.get('/files/{chat_id}/{asset_id}')
def local_file(
    chat_id: str, asset_id: str, inline: int = 0, download: int = 0,
    owner: str = Depends(api.identity),
):
    """Serve one asset, either as a file to save or as something to look at.

    The route used to answer every request with ``application/octet-stream`` and an
    attachment disposition. A registered PNG therefore reached the browser as a
    download, which is why a preview that existed was still not visible. ``inline=1``
    asks for the asset's own media type and an inline disposition; the file itself is
    unchanged either way.
    """
    sessions = api.sessions
    store = sessions.acquire(owner, chat_id)
    try:
        asset = store.get(asset_id, owner)
        path = store.path(asset_id, owner)
        media_type = str(asset.get('media_type') or 'application/octet-stream')
        disposition = 'inline' if inline and not download else 'attachment'
        # FileResponse opens and streams the path after this route returns. Keep
        # the session active until Starlette completes (or aborts) that send.
        return HeldFileResponse(
            path, filename=asset['name'], media_type=media_type,
            content_disposition_type=disposition,
            release=lambda: sessions.release(chat_id),
        )
    except Exception:
        sessions.release(chat_id)
        raise
