from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import app as api

router = APIRouter()

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
def local_file(chat_id: str, asset_id: str, owner: str = Depends(api.identity)):
    store = api.sessions.acquire(owner, chat_id)
    try:
        asset = store.get(asset_id, owner)
        path = store.path(asset_id, owner)
    finally:
        api.sessions.release(chat_id)
    return FileResponse(path, filename=asset['name'], media_type='application/octet-stream')
