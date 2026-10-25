"""文档、图像与知识检索能力。

定义放在组件里，模型可见 schema 由函数签名与文档串派生；能力只通过受守卫通道
``journal`` 执行，准入、权限与动作记录仍由运行核心里那一处完成。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from .scheduling import PARALLEL
from .tools import ToolRegistry, registrar


def register_document_tools(registry: ToolRegistry, *, documents, knowledge, vision,
                            journal: Callable[[Any, str, dict, Callable], Any],
                            assignment: dict | None = None) -> Callable[[], None]:
    """注册文档读取、图片查看与知识检索；全部只读，可与读取类工具并行。"""
    register, cleanup = registrar(registry, "document_tools")

    def execute(ctx, kind, args, operation):
        return journal(ctx, kind, args, operation)

    def view_image(ctx: RunContext, resource_id: str | None = None, artifact_id: str | None = None,
                   relative_path: str = '') -> ToolReturn:
        """实际查看会话图片或目录内图片，也可看本会话图片产物。图像以视觉内容传入，保留原文件；不把文件名当图像。"""
        args = {'relative_path': relative_path}
        if resource_id:
            args['resource_id'] = resource_id
        if artifact_id:
            args['artifact_id'] = artifact_id
        picture = None

        def operation(_):
            nonlocal picture
            metadata, picture = vision.prepare(ctx.deps.task_id, resource_id=resource_id,
                artifact_id=artifact_id, relative_path=relative_path)
            return metadata

        receipt = execute(ctx, 'document.read', args, operation)
        if receipt['kind'] == 'failed':
            return ToolReturn(return_value=receipt)
        if picture is None:
            _, picture = vision.prepare(ctx.deps.task_id, resource_id=resource_id,
                artifact_id=artifact_id, relative_path=relative_path)
        return ToolReturn(return_value=receipt, content=[picture])

    def read_document(ctx: RunContext, resource_id: str, relative_path: str = "", page: int = 0) -> dict:
        """读取授权 PDF、DOCX 或文本，返回文本和来源引用。PDF 页码从 0 开始，不提供 OCR。"""
        return execute(ctx, "document.read", {"resource_id": resource_id, "relative_path": relative_path, "page": page},
            lambda _: documents.read(ctx.deps.task_id, resource_id, relative_path, page))

    def index_document(ctx: RunContext, resource_id: str, relative_path: str = "", page: int = 0) -> dict:
        """缓存本任务授权文档的一页/窗口供检索，不构成用户知识导入。保留来源版本，按 next_page 继续。"""
        return execute(ctx, "knowledge.index", {"resource_id": resource_id, "relative_path": relative_path, "page": page},
            lambda _: knowledge.index(ctx.deps.task_id, resource_id, relative_path, page))

    def search_knowledge(ctx: RunContext, query: str) -> dict:
        """检索本任务授权来源的当前版本，保留明确导入的来源分类、适用条件、局限及冲突。

        检查 source_notices，不能使用过期来源或把专家断言当测量验证。无匹配不代表读过来源。
        """
        return execute(ctx, "knowledge.search", {"query": query}, lambda _: knowledge.search(
            ctx.deps.task_id, query, allowed_refs=assignment["effective_input_refs"] if assignment else None))

    for func, kind in ((view_image, "document.read"), (read_document, "document.read"),
                       (index_document, "knowledge.index"), (search_knowledge, "knowledge.search")):
        register(func.__name__, kind, func, PARALLEL)
    return cleanup
