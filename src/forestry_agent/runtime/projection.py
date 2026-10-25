"""模型投影与界面投影。

同一份执行结果有三种读者：模型、界面、进展判定。它们读同一份事实，但需要的
形状不同——

- 模型拿执行通道返回的结构化结果（正文超限由原生外置存储处理）；
- 界面拿可读名称、状态和作业引用，不复制正文、不重写结论；
- 进展判定只读 ``runtime.evidence`` 的维度，不看这里的任何文本。

投影是纯函数：不写库、不改结果、不发起第二次调用。
"""

from __future__ import annotations

#: 面向用户的能力名称。技术工具名留在详情里，用户明确询问实现时再解释。
READABLE: dict[str, str] = {
    "fs.read": "读取文件", "read_file": "读取文件",
    "fs.list": "列目录", "list_files": "列目录",
    "fs.inputs": "查看输入", "list_inputs": "查看输入", "select_input_files": "选择输入文件",
    "fs.write_workspace": "写入产物", "write_file": "写入产物",
    "artifact.read": "读回产物", "read_artifact": "读回产物",
    "artifact.inspect": "检查产物", "inspect_artifact": "检查产物",
    "document.read": "读取文档", "read_document": "读取文档", "view_image": "查看图片",
    "knowledge.index": "索引文档", "index_document": "索引文档",
    "knowledge.search": "检索资料", "search_knowledge": "检索资料",
    "memory.read": "读取用户笔记", "read_user_memory": "读取用户笔记",
    "action.inspect": "读回动作", "inspect_action": "读回动作",
    "environment.inspect": "检查本机环境", "inspect_environment": "检查本机环境",
    "environment.acquire": "获取依赖", "acquire_dependencies": "获取依赖",
    "resolve_dependencies": "解析依赖",
    "environment.prepare": "准备运行环境", "prepare_environment": "准备运行环境",
    "code.execute": "运行代码作业", "code_execute": "运行代码作业",
    "process_imagery": "影像处理", "process_lidar_terrain": "地形产品",
    "process_lidar_canopy": "冠层产品", "apply_lidar_roi": "ROI 后处理",
    "job.observe": "查看作业", "wait_job": "查看作业",
    "web.search": "联网搜索", "search_web": "联网搜索",
    "web.read": "读取网页", "read_web_page": "读取网页",
    "task.plan": "更新计划", "role.delegate": "委派子任务",
}

#: 界面只需要这些字段；正文、凭据和本机路径不进入界面元数据。
UI_FIELDS = ("job_id", "artifact_ids", "status", "blocker")


def readable_name(tool: str) -> str:
    return READABLE.get(tool, tool)


def ui_view(tool: str, result) -> dict:
    """有界界面元数据：状态、可读名称、作业与产物引用；不含正文。"""
    value = result if isinstance(result, dict) else {}
    job = value.get("job") if isinstance(value.get("job"), dict) else {}
    artifact_ids = value.get("artifact_ids") or job.get("artifact_ids") or []
    error = value.get("error") if isinstance(value.get("error"), dict) else {}
    view = {"tool": tool, "readable_name": readable_name(tool),
            "outcome": value.get("kind") or (value.get("status") if isinstance(value.get("status"), str) else None),
            "artifact_count": len(artifact_ids) if isinstance(artifact_ids, list) else 0}
    if job.get("job_id"):
        view["job_id"] = job["job_id"]
        view["job_status"] = job.get("status")
    if value.get("job_id") and not view.get("job_id"):
        view["job_id"] = value["job_id"]
    if error.get("code"):
        view["error"] = {"code": error["code"], "message": str(error.get("message", ""))[:300]}
    return view


def model_view(tool: str, result):
    """模型看到的形状：执行通道返回的结构化结果本身。

    这里不做二次改写：正文超限由原生外置存储和窗口读取处理，模型需要 action_id
    引用真实动作。任何「替模型总结一遍结果」的分支都会形成第二份事实来源。
    """
    return result
