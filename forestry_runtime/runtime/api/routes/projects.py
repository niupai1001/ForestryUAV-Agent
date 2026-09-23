from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import app as api

router = APIRouter()

@router.get('/projects', response_model=dict)
def list_projects(owner=Depends(api.identity)):
    return {'projects': api.memory.projects(owner)}

@router.post('/projects', response_model=dict)
def create_project(body: api.ProjectRequest, owner=Depends(api.identity)):
    return api.memory.create_project(owner, body.name)

@router.delete('/projects/{project_id}', response_model=dict)
def delete_project(project_id: str, owner=Depends(api.identity)):
    api.memory.delete_project(owner, project_id)
    return {'ok': True}

@router.put('/sessions/{chat_id}/project', response_model=dict)
def select_project(chat_id: str, body: api.ProjectSelection, owner=Depends(api.identity)):
    api.sessions.acquire(owner, chat_id)
    api.sessions.release(chat_id)
    return api.memory.select_project(owner, chat_id, body.project_id)

@router.get('/projects/{project_id}/memories', response_model=dict)
def list_memories(project_id: str, owner=Depends(api.identity)):
    return {'memories': api.memory.memories(owner, project_id)}

@router.post('/projects/{project_id}/memories', response_model=dict)
def create_memory(project_id: str, body: api.MemoryRequest, owner=Depends(api.identity)):
    return api.memory.add_memory(owner, project_id, body.content, body.confirmed)

@router.put('/projects/{project_id}/memories/{memory_id}', response_model=dict)
def update_memory(project_id: str, memory_id: str, body: api.MemoryRequest, owner=Depends(api.identity)):
    return api.memory.update_memory(
        owner, project_id, memory_id, body.content, body.confirmed
    )

@router.delete('/projects/{project_id}/memories/{memory_id}', response_model=dict)
def delete_memory(project_id: str, memory_id: str, owner=Depends(api.identity)):
    api.memory.delete_memory(owner, project_id, memory_id)
    return {'ok': True}

@router.get('/projects/{project_id}/memories/{memory_id}/versions', response_model=dict)
def memory_versions(project_id: str, memory_id: str, owner=Depends(api.identity)):
    return {'versions': api.memory.memory_history(owner, project_id, memory_id)}

@router.get('/projects/{project_id}/knowledge/sources', response_model=dict)
def list_knowledge_sources(project_id: str, owner=Depends(api.identity)):
    return {'sources': api.memory.sources(owner, project_id)}

@router.post('/projects/{project_id}/knowledge/sources', status_code=202, response_model=dict)
async def index_knowledge_source(project_id: str, body: api.KnowledgeSourceRequest, owner=Depends(api.identity)):
    source = api.memory.queue_source(owner, project_id, body.kind, body.locator)
    indexing = asyncio.create_task(asyncio.to_thread(
        api.memory.index_source, owner, project_id, body.kind, body.locator
    ))
    api.knowledge_tasks.add(indexing)

    def completed(task: asyncio.Task) -> None:
        api.knowledge_tasks.discard(task)
        try:
            task.result()
        except Exception:
            api.log.exception('Knowledge source indexing failed: %s', source['id'])

    indexing.add_done_callback(completed)
    return source

@router.delete('/projects/{project_id}/knowledge/sources/{source_id}', response_model=dict)
def delete_knowledge_source(project_id: str, source_id: str, owner=Depends(api.identity)):
    api.memory.delete_source(owner, project_id, source_id)
    return {'ok': True}
