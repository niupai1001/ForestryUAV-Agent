"""工作区写入与动作读回。

写入是副作用，声明为独占；读回动作只读本任务当前修订的已确认记录。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic_ai import RunContext

from ..contracts import Failure
from .scheduling import EXCLUSIVE, PARALLEL
from .tools import ToolRegistry, registrar


def register_workspace_tools(registry: ToolRegistry, *, resources, store, roles,
                             cite: Callable[[Any, str, str, dict | None], str],
                             journal: Callable[[Any, str, dict, Callable], Any]) -> Callable[[], None]:
    """注册产物写入与动作读回；``cite`` 是运行核心提供的引用复制入口。"""
    register, cleanup = registrar(registry, "workspace_tools")

    def execute(ctx, kind, args, operation):
        return journal(ctx, kind, args, operation)

    def write_file(ctx: RunContext, relative_path: str, content: str,
                   citation_actions: dict[str, str] | None = None) -> dict:
        """写UTF-8并封存；citation_actions={字段名:来源action_id}只附返回顶层citation的真实引用。JSON合字段，Markdown/文本附来源区；产物/记忆回执无citation，标识与SHA/版本写正文。不证明正文正确。"""
        args = {"relative_path": relative_path, "content": content}
        if citation_actions is not None:
            args["citation_actions"] = citation_actions
        return execute(ctx, "fs.write_workspace", args,
            lambda action_id: {k: v for k, v in resources.write(ctx.deps.task_id, action_id,
                roles.output_path(ctx.deps, relative_path),
                cite(ctx.deps, relative_path, content, citation_actions)
                if citation_actions is not None else content).items() if k != "locator"})

    def inspect_action(ctx: RunContext, action_id: str, view: Literal["result", "citation"] = "result") -> dict:
        """读回本任务当前修订的已确认动作；view=citation只返回来源引用，不重读正文。默认result保留完整结果，可能外置。不能授予权限或重做动作。"""

        def operation(_):
            with store.transaction() as db:
                row = db.execute("SELECT * FROM actions WHERE id=? AND task_id=? AND revision=? AND status IN ('confirmed','failed')",
                                 (action_id, ctx.deps.task_id, ctx.deps.revision)).fetchone()
                if not row:
                    raise Failure("PERMISSION_DENIED", "动作不属于当前任务修订或尚未确认", 403)
                if view == "citation":
                    result = json.loads(row["result"])
                    if not result.get("citation"):
                        raise Failure("PRECONDITION_MISSING", "这个动作没有来源引用；请使用实际来源动作ID", 422)
                    return {"source_action_id": row["id"], "tool": row["kind"], "status": row["status"], "citation": result["citation"]}
                return {"action_id": row["id"], "tool": row["kind"], "status": row["status"],
                        "args": json.loads(row["args"]), "result": json.loads(row["result"])}

        args = {"action_id": action_id}
        if view != "result":
            args["view"] = view
        return execute(ctx, "action.inspect", args, operation)

    register("write_file", "fs.write_workspace", write_file, EXCLUSIVE)
    register("inspect_action", "action.inspect", inspect_action, PARALLEL)
    return cleanup
