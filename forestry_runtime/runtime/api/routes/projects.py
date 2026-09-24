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


# --------------------------------------------------------------- instructions

@router.get('/projects/{project_id}/instruction', response_model=dict)
def get_instruction(project_id: str, owner=Depends(api.identity)):
    """Current editable project instruction, with its revision and budget."""
    return api.memory.project_instruction(owner, project_id)


@router.put('/projects/{project_id}/instruction', response_model=dict)
def set_instruction(project_id: str, body: api.InstructionRequest,
                    owner=Depends(api.identity)):
    """Replace the project instruction.

    Two refusals are deliberate: text over the budget is rejected with its
    measurement instead of being truncated, and a stale ``expected_revision`` is
    rejected so concurrent editors cannot silently overwrite each other.
    The new revision takes effect from the next user Turn.
    """
    return api.memory.set_project_instruction(
        owner, project_id, body.content,
        expected_revision=body.expected_revision, actor=owner,
    )


@router.post('/projects/{project_id}/instruction/reset', response_model=dict)
def reset_instruction(project_id: str, owner=Depends(api.identity)):
    return api.memory.reset_project_instruction(owner, project_id, actor=owner)


@router.get('/projects/{project_id}/instruction/versions', response_model=dict)
def instruction_versions(project_id: str, owner=Depends(api.identity)):
    return {'versions': api.memory.instruction_history(owner, project_id)}


@router.get('/projects/{project_id}/instruction/versions/{revision}', response_model=dict)
def instruction_version(project_id: str, revision: int, owner=Depends(api.identity)):
    return api.memory.instruction_version(owner, project_id, revision)


@router.post('/projects/{project_id}/instruction/preview', response_model=dict)
def preview_instruction(project_id: str, body: api.InstructionPreviewRequest,
                        owner=Depends(api.identity)):
    """Show exactly how the next Turn's instructions will be assembled.

    Reports each layer's origin, revision and token cost, plus the rendered text.
    Credentials are never part of the assembly and are never returned here.
    """
    from ...agent import SYSTEM
    from ...context import _guide_catalogue_part
    from ...kernel.registry import runtime_registry
    from ...tokens import estimate_text

    current = api.memory.project_instruction(owner, project_id)
    draft = current["content"] if body.content is None else body.content
    budget = current["budget_tokens"]
    draft_tokens = estimate_text(draft)

    catalogue_text, entries = _guide_catalogue_part(list(runtime_registry()))
    facts_note = (
        "Runtime facts are assembled per request and are not shown here: they "
        "contain the current attachments, source grants, job state and unfinished "
        "jobs. They never contain credentials or filesystem paths outside the "
        "authorized grants."
    )
    layers = [
        {
            "layer": "global_rules",
            "title": "全局协作规则",
            "source": "runtime",
            "editable": False,
            "tokens": estimate_text(SYSTEM),
            "content": SYSTEM,
        },
        {
            "layer": "project_instruction",
            "title": "项目指令",
            "source": "project",
            "editable": True,
            "revision": current["revision"],
            "is_default": current["is_default"],
            "draft": body.content is not None,
            "tokens": draft_tokens,
            "budget_tokens": budget,
            "within_budget": draft_tokens <= budget,
            "content": draft,
        },
        {
            "layer": "domain_guides",
            "title": "领域指南目录",
            "source": "knowledge/guides",
            "editable": False,
            "tokens": estimate_text(catalogue_text),
            "entries": entries,
            "content": catalogue_text,
        },
        {
            "layer": "runtime_facts",
            "title": "当前任务事实",
            "source": "runtime",
            "editable": False,
            "tokens": None,
            "content": None,
            "note": facts_note,
        },
    ]
    total = sum(item["tokens"] or 0 for item in layers)
    return {
        "project_id": project_id,
        "active_revision": current["revision"],
        "is_default": current["is_default"],
        "budget_tokens": budget,
        "draft_tokens": draft_tokens,
        "draft_within_budget": draft_tokens <= budget,
        "layers": layers,
        "assembled_tokens_excluding_runtime_facts": total,
        "note": (
            "保存后在下一个用户 Turn 生效；本轮已开始的工具链继续使用当前生效的版本。"
        ),
    }


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
