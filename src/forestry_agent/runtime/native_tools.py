"""原生外置结果的窗口读取。

只读取本次运行已经拥有的句柄，不暴露新的来源、文件系统、网络或执行权限；也不
重新执行原工具。因此它是一条读取路径，不产生动作记录，也不是执行通道的分支。
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import RunContext

from ..contracts import Failure
from ..context import utf8_window
from .scheduling import PARALLEL
from .tools import ToolRegistry, registrar


def register_native_tools(registry: ToolRegistry, *, spill_store, store,
                          publish: Callable[[str, str, dict], None]) -> Callable[[], None]:
    register, cleanup = registrar(registry, "native_tools")

    async def read_tool_result_window(ctx: RunContext, handle: str, offset: int = 0, limit: int = 4000,
                                      from_end: bool = False) -> dict:
        """用原生外置存储的精确handle读UTF-8字节窗口。limit为1–4000；使用返回的next继续，无损覆盖单行长JSON/中文文本。from_end=true读取结尾，便于查引用。只能读取当前任务/角色命名空间，不重新执行原工具。"""

        def check():
            current = store.task(ctx.deps.task_id)
            if current["spec_revision"] != ctx.deps.revision or current["pause_requested"] or current["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "控制或规范状态阻止外置结果读回", 409)

        check()
        try:
            data = await spill_store.read(handle)
        except (OSError, ValueError):
            return {"kind": "failed", "error": {"code": "PERMISSION_DENIED", "message": "当前任务/角色没有这个精确原生handle"}}
        check()
        try:
            window = utf8_window(data, offset, limit, from_end)
        except Failure as error:
            return {"kind": "failed", "error": {"code": error.code, "message": str(error)}}
        publish(ctx.deps.task_id, "tool_result.window_read", {"turn_id": ctx.deps.turn_id,
            "assignment_id": ctx.deps.assignment_id, "handle": handle, "offset": window["offset"],
            "next": window["next"], "size_bytes": window["size_bytes"]})
        return {"handle": handle, **window, "continuation":
            "仍有内容，仅按next继续读取需要的窗口。" if window["has_more"] else
            "已到原生结果末尾；不要继续读取相同末尾。请继续任务或使用原工具返回的产物ID读取正文。"}

    register(read_tool_result_window.__name__, "native.read", read_tool_result_window, PARALLEL)
    return cleanup
