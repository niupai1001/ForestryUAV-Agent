"""工具观察的结构化事实与进展维度。

本模块只回答两个问题：这次结果里有没有**新的任务事实**，以及变化的到底是哪一
维。它不解析模型可见的正文，也不读界面文本；输入是执行通道保存的原始结构化
结果，输出是有界、可复算的事实。

五个维度（稳定输入身份/版本、读取范围、实际结果、失败类别、作业状态）来自计划
第 5.2 节。动作 ID、尝试 ID、时间戳、新句柄和外置位置属于应用信封，在规范化时
就被移除，不参与判定；它们既不能创造进展，也不能掩盖重复。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic_ai.messages import ToolReturn
from pydantic_ai_harness.tool_output_limits import indented_json

from ..store import encode

INPUT, RANGE, OUTCOME, FAILURE, JOB = "input", "range", "outcome", "failure", "job"
DIMENSIONS = (INPUT, RANGE, OUTCOME, FAILURE, JOB)

#: 每个维度只读这些**已声明**字段。未列出的字段不进入该维度，也不会因为出现
#: 在结果里就自动获得判定权。
FACT_FIELDS: dict[str, tuple[str, ...]] = {
    INPUT: ("resource_id", "artifact_id", "source_artifact_id", "target_artifact_id",
            "text_artifact_id", "handle", "job_id", "sha256", "source_sha256",
            "version", "url", "method_id", "stage", "operation"),
    RANGE: ("offset", "next", "page", "next_page", "from_end", "size_bytes", "has_more",
            "total", "limit"),
    FAILURE: ("error", "blocker"),
    JOB: ("job", "status", "kind"),
}

#: 工具 → 参与判定的维度。``OUTCOME`` 取「其余字段」的规范摘要，因此未在这张表
#: 里声明的工具保持与既有实现相同的敏感度，不会因为迁移而突然漏判。
PROFILES: dict[str, tuple[str, ...]] = {
    "read_file": (INPUT, RANGE, OUTCOME),
    "fs.read": (INPUT, RANGE, OUTCOME),
    "list_files": (INPUT, RANGE, OUTCOME),
    "fs.list": (INPUT, RANGE, OUTCOME),
    "list_inputs": (INPUT, RANGE, OUTCOME),
    "fs.inputs": (INPUT, RANGE, OUTCOME),
    "select_input_files": (INPUT, RANGE, OUTCOME),
    "read_artifact": (INPUT, RANGE, OUTCOME),
    "artifact.read": (INPUT, RANGE, OUTCOME),
    "read_tool_result_window": (RANGE, OUTCOME),
    "read_tool_result": (RANGE, OUTCOME),
    "inspect_artifact": (INPUT, OUTCOME),
    "artifact.inspect": (INPUT, OUTCOME),
    "read_document": (INPUT, RANGE, OUTCOME),
    "document.read": (INPUT, RANGE, OUTCOME),
    "index_document": (INPUT, RANGE, OUTCOME),
    "knowledge.index": (INPUT, RANGE, OUTCOME),
    "search_knowledge": (INPUT, OUTCOME),
    "knowledge.search": (INPUT, OUTCOME),
    "wait_job": (JOB, OUTCOME),
    "job.observe": (JOB, OUTCOME),
    "code_execute": (FAILURE, JOB, OUTCOME),
    "stdout": (FAILURE, JOB, OUTCOME),
    "process_imagery": (FAILURE, JOB, OUTCOME),
    "process_lidar_terrain": (FAILURE, JOB, OUTCOME),
    "process_lidar_canopy": (FAILURE, JOB, OUTCOME),
    "apply_lidar_roi": (FAILURE, JOB, OUTCOME),
}

#: 模型自报的记账不是新观察到的证据。
BOOKKEEPING = frozenset({"write_plan", "read_plan", "task.plan"})

#: 只由应用生成的信封字段。移除它们不是「清洗文本」，而是承认它们不承载任务事实。
ENVELOPE_KEYS = ("action_id", "attempt_id", "producer_action_id", "created_at", "observed_at",
                 "updated_at", "preview_url", "download_url", "locator", "continuation")

WINDOW_READERS = frozenset({"read_tool_result_window", "read_artifact", "read_file",
                            "artifact.read", "fs.read"})


def observation(tool, result):
    """Normalize known tool envelopes, leaving source text and user fields intact.

    Novel observations are a bounded engineering signal, not semantic acceptance.
    New attempt IDs, timestamps and recursive inspections are not new evidence.
    """
    if tool in BOOKKEEPING:
        return None  # Model-authored bookkeeping is not newly observed evidence.
    if isinstance(result, ToolReturn):
        value = observation(tool, result.return_value)
        return {"return_value": value, "content": result.content} if result.content is not None else value
    if tool in {"delegate_task", "role.delegate"} and isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            pass
    if not isinstance(result, dict):
        return result if result else None
    value = dict(result)
    if value.get("kind") in {"failed", "window_required"} or value.get("status") == "failed":
        return None
    if tool in WINDOW_READERS and value.get("text") == "":
        return None
    # Ignore only application-owned envelope keys, never arbitrary keys in text.
    for key in ENVELOPE_KEYS:
        value.pop(key, None)
    if tool in {"acquire_dependencies", "resolve_dependencies", "environment.acquire"} and "wheels" in value:
        # Re-downloading the same public bytes into fresh artifact IDs is not
        # new task evidence. Keep release/hash/source facts, not receipt handles.
        value.pop("wheel_artifact_ids", None)
        value.pop("source_artifact_ids", None)
        value.pop('built_wheel_artifact_ids', None)
        value.pop("lock_artifact_id", None)
        value.pop("resolution_lock_artifact_id", None)
        value.pop("target_artifact_id", None)
        value.pop("metadata_requests", None)
        value.pop("reused_from_action_id", None)
        value.pop("recorded_metadata_requests", None)
        for group in ['wheels', 'source_packages', 'built_wheels']:
            if group in value:
                value[group] = [{k: v for k, v in package.items() if k not in
                    {"artifact_id", "metadata_artifact_id", "hops", "observed_at"}} for package in value[group]]
    if tool in {"write_file", "inspect_artifact", "fs.write_workspace", "artifact.inspect"}:
        value.pop("artifact_id", None)
    if tool in {"read_tool_result_window", "read_tool_result"}:
        value.pop("handle", None)
    citation = value.get("citation")
    if isinstance(citation, dict):
        value["citation"] = {k: v for k, v in citation.items()
                             if k not in {"observed_at", "snapshot_artifact_id"}}
        value.pop("text_artifact_id", None)
    if tool in {"inspect_action", "action.inspect"} and "result" in value:
        # Flatten actual inspection envelopes, bounded against adversarial nesting.
        target = value
        for _ in range(32):
            if target.get("tool") != "action.inspect" or not isinstance(target.get("result"), dict):
                break
            target = target["result"]
        else:
            return None
        source_tool = target.get("tool", "unknown")
        source = observation(source_tool, target.get("result"))
        return {"tool": source_tool, "observation": source} if source is not None else None
    if isinstance(value.get("job"), dict):
        value["job"] = {k: v for k, v in value["job"].items()
                        if k not in {"updated_at", "created_at"}}
    return value


def canonical(value) -> str:
    """Canonical JSON for a normalized value; stable across key order."""
    return json.loads(indented_json(value)) if not isinstance(value, str) else value


def digest(value) -> str:
    return hashlib.sha256(encode(canonical(value)).encode()).hexdigest()


def fingerprint(tool, value) -> str | None:
    """Identity of one observation: the whole normalized value, tool-scoped."""
    return hashlib.sha256(encode(json.loads(indented_json({'tool': tool, 'observation': value}))).encode()).hexdigest() \
        if value is not None else None


@dataclass(frozen=True)
class Evidence:
    """一次工具结果的事实视图：哪些维度有值、各维度的摘要、失败类别。"""

    tool: str
    facets: tuple[tuple[str, str], ...] = ()
    failure: str | None = None
    obstacle: str | None = None
    problem: str = ""

    @property
    def dimensions(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.facets)

    def value(self, name: str) -> str | None:
        return dict(self.facets).get(name)

    def changed_from(self, other: "Evidence | None") -> tuple[str, ...]:
        """相对上一次观察，哪些维度真的变了；未变化的维度不产生进展。"""
        if other is None:
            return self.dimensions
        return tuple(name for name in DIMENSIONS
                     if self.value(name) is not None and self.value(name) != other.value(name))

    def payload(self) -> dict:
        changed = {name: (self.value(name) or "")[:16] for name in self.dimensions}
        return {"dimensions": list(self.dimensions), "facets": changed,
                **({"failure_code": self.failure} if self.failure else {}),
                **({"obstacle": self.obstacle} if self.obstacle else {})}


def facts(tool: str, result) -> Evidence:
    """把一次原始结构化结果切成已声明维度。

    规范化（信封剥离）在这里完成一次，因此调用方给原始结果即可；失败结果虽然不
    产生新事实，仍要保留失败类别，否则「同一阻碍反复尝试」就无从识别。
    """
    raw = result.return_value if isinstance(result, ToolReturn) else result
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    error = raw.get("error") if isinstance(raw, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    failure = code if isinstance(code, str) and code else None
    obstacle_id = obstacle(tool, failure, error) if failure else None
    value = observation(tool, result)
    if value is None:
        return Evidence(tool=tool, failure=failure, obstacle=obstacle_id)
    if not isinstance(value, dict):
        return Evidence(tool=tool, facets=((OUTCOME, digest(value)),), failure=failure, obstacle=obstacle_id)
    profile = PROFILES.get(tool, (OUTCOME,))
    facets: list[tuple[str, str]] = []
    consumed: set[str] = set()
    for name in profile:
        if name in (FAILURE, OUTCOME):
            continue
        picked = {key: value[key] for key in FACT_FIELDS[name] if key in value}
        if picked:
            facets.append((name, digest(picked)))
            consumed.update(picked)
    if OUTCOME in profile:
        rest = {key: item for key, item in value.items() if key not in consumed and key != "error"}
        if rest:
            facets.append((OUTCOME, digest(rest)))
    return Evidence(tool=tool, facets=tuple(facets), failure=failure, obstacle=obstacle_id)


def obstacle(tool: str, code: str, error) -> str:
    """同一障碍的身份：失败类别，不绑定具体工具。

    不同工具围绕同一个未改变障碍循环时，它们的 ``obstacle`` 相同，因此不会被当
    成三次互相独立的新发现；同一工具换一种说法重复同一阻碍也同样可识别。
    """
    message = error.get("message") if isinstance(error, dict) else ""
    return hashlib.sha256(encode({"code": code, "message": _stable(str(message or ""))}).encode()).hexdigest()


def _stable(text: str) -> str:
    """Bounded, whitespace-normalized message: enough to tell obstacles apart."""
    return " ".join(text.split())[:240]


def describe(evidence: Evidence) -> str:
    """One bounded line for the stop/observation event; never a substitute for facts."""
    if evidence.obstacle:
        return "同一阻碍重复出现（失败类别 " + (evidence.failure or "未知") + "）"
    return "变化的维度：" + "、".join(evidence.dimensions or ("无",))
