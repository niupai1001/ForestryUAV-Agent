from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import app as api

router = APIRouter()

from .assets import metadata

def validate_run_input(messages, asset_ids, owner, store):
    for asset_id in asset_ids:
        metadata(asset_id, owner, store)
    if sum(len(message.content) for message in messages) > 120000:
        raise HTTPException(413, 'Conversation input exceeds the Run limit')

@router.post('/runs', response_model=api.RunResponse)
async def create_run(body: api.RunRequest, owner: str = Depends(api.identity), store=Depends(api.request_store)):
    validate_run_input(body.messages, body.asset_ids, owner, store)
    return api.runs.create(store, owner, [message.model_dump() for message in body.messages], body.asset_ids, body.use_tools)

@router.get('/runs/{run_id}', response_model=api.RunResponse)
def get_run(run_id: str, owner: str = Depends(api.identity)):
    return api.runs.get(run_id, owner)

@router.get('/runs/{run_id}/events', response_model=api.RunEventsResponse)
async def get_run_events(
    run_id: str, after: int = 0, limit: int = 500,
    wait_seconds: int = 0, owner: str = Depends(api.identity),
):
    deadline = asyncio.get_running_loop().time() + min(max(wait_seconds, 0), 30)
    while True:
        page = api.runs.event_page(run_id, owner, after, limit)
        if page['events'] or asyncio.get_running_loop().time() >= deadline:
            return page | {'run': api.runs.get(run_id, owner)}
        await asyncio.sleep(0.25)

@router.get('/runs/{run_id}/turns', response_model=api.RunTurnsResponse)
def get_run_turns(run_id: str, owner: str = Depends(api.identity)):
    return {'turns': api.runs.turns(run_id, owner)}

@router.post('/runs/{run_id}/messages', response_model=api.RunResponse)
async def continue_run(run_id: str, body: api.RunMessage, owner: str = Depends(api.identity), store=Depends(api.request_store)):
    for asset_id in body.asset_ids:
        metadata(asset_id, owner, store)
    return api.runs.continue_run(
        run_id, owner, body.content, store, body.asset_ids, body.input_id
    )

@router.post('/runs/{run_id}/cancel', response_model=api.RunResponse)
async def cancel_run(run_id: str, owner: str = Depends(api.identity)):
    return await api.runs.cancel(run_id, owner)

@router.post('/runs/{run_id}/pause', response_model=api.RunResponse)
async def pause_run(run_id: str, owner: str = Depends(api.identity)):
    return await api.runs.pause(run_id, owner)
