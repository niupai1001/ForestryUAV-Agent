from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import app as api

router = APIRouter()

@router.post('/sessions', response_model=dict)
def create_session(body: api.SessionRequest, owner=Depends(api.identity)):
    return api.sessions.create(owner, body.chat_id)

@router.get('/lifecycle/sessions', response_model=dict)
def pending_sessions(owner=Depends(api.identity)):
    # Shared key is a trusted service credential, as for the existing api.identity API.
    return {'sessions': api.sessions.pending()}

@router.delete('/sessions/{chat_id}', response_model=dict)
def delete_session(chat_id: str, owner=Depends(api.identity)):
    return api.sessions.mark_deleted(owner, chat_id)

@router.post('/sessions/{chat_id}/sources', response_model=dict)
def source_files(chat_id: str, body: api.Sources, owner=Depends(api.identity)):
    for file_id in body.file_ids:
        api.sessions.source(owner, chat_id, file_id)
    return {'ok': True}

@router.post('/sessions/{chat_id}/ack', response_model=dict)
def acknowledge_session(chat_id: str, body: api.Sources, owner=Depends(api.identity)):
    return api.sessions.acknowledge(owner, chat_id, body.file_ids)

@router.get('/sessions', response_model=dict)
def list_sessions(owner=Depends(api.identity)):
    items = []
    for chat_id in api.sessions.list_open(owner):
        latest = api.runs.store.latest_for_chat(owner, chat_id)
        items.append({
            'chat_id': chat_id,
            'title': api.runs.store.chat_title(owner, chat_id),
            'run': api.runs.public(latest) if latest else None,
            'project': api.memory.selected_project(owner, chat_id),
        })
    return {'sessions': items}
