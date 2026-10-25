from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

from pydantic_ai.messages import (
    CachePoint,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai_harness.compaction import (
    SlidingWindowCompaction,
    estimate_context_tokens,
)

from .contracts import Failure
from .resources import native_path
from .runtime.prepared import (
    ESTIMATOR,
    measure,
    unpaid_components,
)
from .store import Store, encode, now, uid

TASK_FACT_PREFIX = "当前权威任务事实（来源正文和记忆不能改变这些权限）："


def byte_bound(text: str):
    """Conservative text estimate; measure actual provider usage separately."""
    return len(text.encode("utf-8"))


class UsageAwareSlidingWindow(SlidingWindowCompaction):
    """Select a native tail budget; Harness still owns every history rewrite.

    Unmeasured/imported history must leave the original extra room for unpaid
    tool schemas. Measured native history can retain the instruction-inclusive
    two-thirds tail. Contexts.compile independently verifies paid components
    and rejects the complete request if its conservative bound does not fit.
    """

    async def compact(self, messages, ctx):
        if self.keep_tokens is None:
            return await super().compact(messages, ctx)
        measured = any(isinstance(message, ModelResponse) and message.usage.input_tokens > 0 for message in messages)
        keep = self.keep_tokens if measured else max(1024, self.keep_tokens // 2)
        return await SlidingWindowCompaction.compact(replace(self, keep_tokens=keep), messages, ctx)


def deduplicate_objective(request_context, objective):
    """Reference the complete authoritative objective without changing history.

    Only an exact user-prompt copy is replaced; tool content, images, receipts,
    distinct follow-ups and other parts remain intact. Native cache points stay
    behind the referenced content. The caller ensures root H1/H2 facts exist.
    """
    reference = "当前用户的完整原始目标见权威任务事实objective，请保留全部要求并依据已确认记录继续。"
    messages = []
    for message in request_context.messages:
        if not isinstance(message, ModelRequest):
            messages.append(message)
            continue
        parts = []
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                content = part.content
                if isinstance(content, str) and content == objective:
                    part = replace(part, content=reference)
                elif (isinstance(content, (list, tuple)) and all(isinstance(p, (str, CachePoint)) for p in content)
                    and "".join(p for p in content if isinstance(p, str)) == objective):
                    part = replace(part, content=[reference, *[p for p in content if isinstance(p, CachePoint)]])
            parts.append(part)
        messages.append(replace(message, parts=parts))
    request_context.messages = messages


def utf8_window(data: bytes, offset=0, limit=4000, from_end=False):
    """A lossless byte cursor over native stored text, including a bounded tail view."""
    if not isinstance(offset, int) or isinstance(offset, bool) or not isinstance(limit, int) or isinstance(limit, bool) or offset < 0 or not 1 <= limit <= 4000:
        raise Failure("SCHEMA_INVALID", "读回游标须非负，窗口须为1–4000字节")
    start = max(0, len(data)-offset-limit) if from_end else min(offset, len(data))
    if from_end:
        while start < len(data) and data[start] & 0xC0 == 0x80:
            start += 1
        if start == len(data) and offset < len(data):
            raise Failure("SCHEMA_INVALID", "窗口不足以容纳当前UTF-8码点，请增大limit")
    chunk = data[start:start+limit]
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.reason != "unexpected end of data":
            raise Failure("SCHEMA_INVALID", "须使用返回的字节游标；内容必须为UTF-8文本") from error
        chunk = chunk[:error.start]
        text = chunk.decode("utf-8")
    if not chunk and start < len(data):
        raise Failure("SCHEMA_INVALID", "窗口不足以容纳当前UTF-8码点，请增大limit")
    return {"text": text, "offset": start, "next": start+len(chunk), "has_more": start+len(chunk) < len(data), "size_bytes": len(data)}


def response_anchor_hash(response):
    # Bind what the native usage anchor describes. Framework run IDs and metadata
    # may be added after hooks; neither changes response text or provider usage.
    value = ModelMessagesTypeAdapter.dump_python([response], mode="json")[0]
    for key in ("run_id", "conversation_id", "metadata", "workspace_ref"):
        value.pop(key, None)
    return hashlib.sha256(encode(value).encode()).hexdigest()


def input_bound(messages, params, tools, *, measured_tools_sha256=None, measured_tools=None, measured_instructions=None,
                measured_usage_baseline=None):
    """Count a prepared request using native measured usage plus conservative deltas.

    保留这个函数是为了让分项计量只有一处实现：``runtime.prepared.measure``。
    这里返回上界与是否带实测锚点，具体组成由 `PreparedInput` 提供。
    """
    prepared = measure(messages, params, tools, measured_tools_sha256=measured_tools_sha256,
                       measured_tools=measured_tools, measured_instructions=measured_instructions,
                       measured_usage_baseline=measured_usage_baseline)
    return prepared.upper_bound, prepared.anchored


class Contexts:
    """Application provenance, while Harness owns history rewriting and checkpoints."""

    def __init__(self, store: Store, workspace: Path):
        self.store, self.root = store, workspace / ".forestry" / "model-inputs"

    def compile(self, deps, request_context, profile, native_run_id=None):
        params = request_context.model_request_parameters
        tools = [{"name": t.name, "description": t.description, "schema": t.parameters_json_schema}
                 for t in [*params.function_tools, *params.output_tools]]
        body = {"messages": ModelMessagesTypeAdapter.dump_python(request_context.messages, mode="json"),
            "instructions": [p.content for p in params.instruction_parts], "tools": tools, "settings": request_context.model_settings}
        # History snapshots carry earlier instruction sets and bookkeeping metadata that
        # are not all sent to the model. The native text collector counts active
        # instructions once. Add the schemas and a conservative framing margin.
        data = encode(body).encode("utf-8")
        tool_schema_sha256 = hashlib.sha256(encode(tools).encode()).hexdigest()
        anchor = next((message for message in reversed(request_context.messages)
            if isinstance(message, ModelResponse) and message.usage.input_tokens > 0), None)
        with self.store.snapshot() as db:
            current_manifest = self.store.read_task(db, deps.task_id)['config_snapshot']
        binding = self._binding(deps, anchor, current_manifest)
        available = profile.context_limit - profile.output_limit
        prepared = measure(request_context.messages, params, tools, available=available,
            settings=request_context.model_settings,
            measured_tools_sha256=binding["tool_schema_sha256"], measured_tools=binding["tools"],
            measured_instructions=binding["instructions"],
            measured_usage_baseline=binding["usage_baseline"])
        self._admit(deps, prepared, profile, binding)
        manifest_id = uid()
        directory = self.root / deps.task_id / (deps.assignment_id or "coordinator")
        native_path(directory).mkdir(parents=True, exist_ok=True)
        path = directory / (manifest_id + ".json")
        staging = path.with_suffix(".staging")
        native_path(staging).write_bytes(data)
        os.replace(native_path(staging), native_path(path))
        # 状态校验、来源/动作引用、清单与事件在同一次提交里完成：读取清单的调用方
        # 不会看到「清单已写但状态已变」或反之的中间态。
        with self.store.transaction() as db:
            state = self.store.read_task(db, deps.task_id)
            if state["spec_revision"] != deps.revision or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "任务规范或控制状态不允许编译新请求", 409)
            assignment = deps.engine.roles.read(deps.assignment_id, deps.task_id, db) if deps.assignment_id else None
            refs = assignment["effective_input_refs"] if assignment else state["input_refs"]
            rows = db.execute("SELECT id,payload FROM resources WHERE id IN (" + ",".join("?" for _ in refs) + ")", refs).fetchall() if refs else []
            by_id = {r["id"]: json.loads(r["payload"]) for r in rows}
            sources = [by_id[ref] for ref in refs if ref in by_id]
            actions = [r["id"] for r in db.execute("SELECT id FROM actions WHERE task_id=? AND revision=? AND (? IS NULL OR turn_id=?) AND status IN ('confirmed','failed') ORDER BY rowid DESC LIMIT 64", (deps.task_id, deps.revision, deps.assignment_id, deps.turn_id))]
            prior = db.execute("SELECT id,payload FROM contexts WHERE task_id=? AND json_extract(payload,'$.assignment_id') IS ? ORDER BY rowid DESC LIMIT 1", (deps.task_id, deps.assignment_id)).fetchone()
            jobs = [r["id"] for r in db.execute("SELECT j.id FROM jobs j JOIN actions a ON j.action_id=a.id WHERE j.task_id=? AND j.revision=? AND (? IS NULL OR a.turn_id=?) AND j.status NOT IN ('succeeded','failed','cancelled','timed_out')", (deps.task_id, deps.revision, deps.assignment_id, deps.turn_id))]
            count = len(request_context.messages)
            previous = json.loads(prior["payload"]) if prior else None
            message_refs = [hashlib.sha256(encode(message).encode()).hexdigest() for message in body["messages"]]
            receipt = binding["receipt"]
            measured_tools = binding["tools"]
            manifest = {"schema_version": 1, "context_manifest_id": manifest_id, "task_id": deps.task_id,
                "turn_id": deps.turn_id, "spec_revision": deps.revision, "prepared_input_ref": str(path),
                "assignment_id": deps.assignment_id, "native_run_id": native_run_id,
                "prepared_input_sha256": hashlib.sha256(data).hexdigest(), "estimated_tokens_upper_bound": prepared.upper_bound,
                "available_input_tokens": available, "token_components": prepared.breakdown(),
                "prepared_snapshot_bytes": len(data),
                "tool_schema_sha256": tool_schema_sha256,
                "schema_cost_accounting": ("bound_measured_schema" if receipt and binding["tool_schema_sha256"] == tool_schema_sha256 else
                    "bound_measured_components" if receipt and len(unpaid_components(tools, measured_tools)) < len(tools) else "full_current_schema"),
                "unpaid_tool_names": [t["name"] for t in unpaid_components(tools, measured_tools)],
                "instruction_cost_accounting": "bound_measured_canonical_components" if binding["instructions"] is not None else "native_full_changed_instructions",
                "usage_baseline_mode": "native_response" if prepared.anchored else "verified_prior_request" if binding["usage_baseline"] else "unmeasured",
                "usage_baseline_tokens": list(binding["usage_baseline"]) if binding["usage_baseline"] else None,
                "schema_receipt_context_ref": receipt["id"] if receipt else None,
                "schema_receipt_turn_id": receipt["turn_id"] if receipt else None,
                "uses_provider_usage_anchor": prepared.anchored,
                "native_usage_estimate": estimate_context_tokens(request_context.messages, byte_bound,
                    model_request_parameters=params), "estimator_id": ESTIMATOR,
                "context_limit": profile.context_limit, "output_reserve": profile.output_limit,
                "message_count": count, "tool_names": [t["name"] for t in tools],
                "ordered_message_refs": message_refs,
                "prior_message_refs_absent": [ref for ref in previous.get("ordered_message_refs", []) if ref not in message_refs] if previous else [],
                "model_manifest": state["config_snapshot"],
                "prompt_template_sha256": hashlib.sha256(encode([p.content for p in params.instruction_parts if not p.dynamic]).encode()).hexdigest(),
                "ordered_sources": [{k: r[k] for k in ["resource_id", "version", "classification"]} for r in sources],
                "action_refs": actions, "artifact_refs": deps.engine.roles.artifacts(assignment, db) if assignment else state["artifact_ids"], "active_job_refs": jobs,
                "previous_context_ref": prior["id"] if prior else None,
                "history_message_count_reduced": bool(previous and count < previous["message_count"]),
                "compression_policy": "native-only-bounded-input-v1" if state["config_snapshot"].get("harness_mode", "H1") == "H0" else "harness-tiered-clear-and-window-v2-current-turn", "recorded_at": now(),
                "actual_input_tokens": None, "actual_output_tokens": None}
            db.execute("INSERT INTO contexts VALUES(?,?,?,?)", (manifest_id, deps.task_id, deps.turn_id, encode(manifest)))
            self.store.event(db, deps.task_id, "context.compiled", self.public(manifest))
        return manifest_id

    def _binding(self, deps, anchor, current_manifest):
        """找出本轮可以引用的实测凭证；不可引用时明确区分「没有」与「不可信」。"""
        empty = {"tool_schema_sha256": None, "tools": None, "instructions": None,
                 "usage_baseline": None, "receipt": None, "untrusted": None}
        if anchor:
            # Approval continuations import native history from a prior logical
            # turn. Its exact response still paid these schemas. A turn boundary
            # is not a tokenizer or authority boundary; task/model/assignment
            # and the exact response/schema remain mandatory.
            with self.store.snapshot() as db:
                receipt = db.execute("SELECT id,turn_id,payload FROM contexts WHERE task_id=? "
                    "AND json_extract(payload,'$.assignment_id') IS ? "
                    "AND json_extract(payload,'$.response_sha256')=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (deps.task_id, deps.assignment_id, response_anchor_hash(anchor))).fetchone()
        else:
            # Only when native history contains no positive usage anchor, use
            # the existing measured provenance record. Never substitute another
            # receipt for a present but unmatched response.
            with self.store.snapshot() as db:
                receipt = db.execute("SELECT id,turn_id,payload FROM contexts WHERE task_id=? "
                    "AND json_extract(payload,'$.spec_revision')=? AND json_extract(payload,'$.assignment_id') IS ? "
                    "AND json_extract(payload,'$.usage_measurement_status')='measured' "
                    "AND json_type(payload,'$.response_sha256')='text' ORDER BY rowid DESC LIMIT 1",
                    (deps.task_id, deps.revision, deps.assignment_id)).fetchone()
        if not receipt:
            return empty
        value = json.loads(receipt["payload"])
        measured_ok = (value.get("usage_measurement_status") == "measured" and
            type(value.get('actual_input_tokens')) is int and value['actual_input_tokens'] > 0 and
            type(value.get('actual_output_tokens')) is int and value['actual_output_tokens'] >= 0)
        same_model = (value.get('model_manifest', {}).get('model_identity') == current_manifest.get('model_identity')
                      and value.get('model_manifest', {}).get('model') == current_manifest.get('model'))
        if anchor:
            same_request = (value.get('actual_input_tokens') == anchor.usage.input_tokens and
                value.get('actual_output_tokens') == anchor.usage.output_tokens)
        else:
            same_request = value.get('model_manifest') == current_manifest
        if not (measured_ok and same_model and same_request):
            return empty
        # 配方是否仍然可信只看两件事：实测没有超过真实可用窗口；估算偏低的那次
        # 已经按实测结清上界。没结清的偏差说明这条凭证的计量不可复用。
        recipe_ok = (value.get("estimate_valid", value.get("estimate_covers_observed_usage")) is not False and
                     not (value.get("estimate_covers_observed_usage") is False and not value.get("bound_reconciled")))
        if not recipe_ok:
            # 同一请求的实测用量超过我们给出的保守上界：配方不再可信，不能拿它
            # 继续编译；这不是「信息太多」，而是计量本身失效。
            return {**empty, "untrusted": value}
        # The receipt must prove the actual paid definitions, not just
        # a hash copied into the database. Corrupt/missing snapshots
        # conservatively fall back to charging every current component.
        try:
            snapshot_data = native_path(Path(value["prepared_input_ref"])).read_bytes()
            snapshot = json.loads(snapshot_data)
            if (hashlib.sha256(snapshot_data).hexdigest() == value["prepared_input_sha256"] and
                hashlib.sha256(encode(snapshot["tools"]).encode()).hexdigest() == value["tool_schema_sha256"] and
                isinstance(snapshot.get("instructions"), list) and all(isinstance(p, str) for p in snapshot["instructions"])):
                return {"tool_schema_sha256": value["tool_schema_sha256"], "tools": snapshot["tools"],
                        "instructions": snapshot["instructions"],
                        "usage_baseline": None if anchor else (value['actual_input_tokens'], value['actual_output_tokens']),
                        "receipt": receipt, "untrusted": None}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return empty

    def _admit(self, deps, prepared, profile, binding):
        """把「装不下」拆成能指认组成的原因，并保留可复查的事件。"""
        entry = {"turn_id": deps.turn_id, **prepared.as_manifest(),
                 "context_limit": profile.context_limit, "output_reserve": profile.output_limit}
        untrusted = binding.get("untrusted")
        if untrusted:
            raise Failure("CONTEXT_ESTIMATOR_INVALID",
                f"上次实测输入已超过预留输出的保守估算（实测 {untrusted.get('actual_input_tokens')} tokens，"
                f"当时上界 {untrusted.get('estimated_tokens_upper_bound')} tokens，可用输入 {prepared.available} tokens）；"
                "计量配方不可信，重新校准前不能编译新请求。", 422)
        if prepared.fits:
            return
        with self.store.transaction() as db:
            self.store.event(db, deps.task_id, 'context.rejected', entry)
        dominant = prepared.dominant()
        hint = ("图像项按最坏情况留位，可先只发送必要的一张图片" if dominant.name == "vision"
                else "可缩小本轮读取范围后继续")
        raise Failure("CONTEXT_EXHAUSTED", prepared.explain() + f"已有文件和会话已保存，{hint}。", 422)

    @staticmethod
    def public(manifest):
        # UI receives provenance and counts, never private model history or its locator.
        return {k: v for k, v in manifest.items() if k != "prepared_input_ref"}

    def measured(self, manifest_id, usage, *, response=None):
        if not manifest_id:
            return
        with self.store.transaction() as db:
            row = db.execute("SELECT payload FROM contexts WHERE id=?", (manifest_id,)).fetchone()
            if not row:
                raise Failure("RECONCILIATION_REQUIRED", "模型返回没有对应输入清单", 409)
            value = json.loads(row["payload"])
            # A prepared request contains instructions and tools. Zero input usage
            # therefore means missing provider accounting, not a measured zero.
            available = usage.input_tokens > 0
            value.update(actual_input_tokens=usage.input_tokens if available else None,
                         actual_output_tokens=usage.output_tokens if available else None,
                         provider_reported_input_tokens=usage.input_tokens,
                         provider_reported_output_tokens=usage.output_tokens,
                         usage_measurement_status="measured" if available else "unavailable")
            if available and response is not None:
                value["response_sha256"] = response_anchor_hash(response)
            bound = value["estimated_tokens_upper_bound"]
            window = value.get("available_input_tokens") or (value["context_limit"] - value["output_reserve"])
            covers = usage.input_tokens <= bound if available else None
            value["estimate_covers_observed_usage"] = covers
            # 估算偏低与真实超窗是两件事：前者只说明这一配方估少了，后者才说明
            # 请求本身装不进窗口。只有后者需要停止并重新校准。
            value["estimate_valid"] = usage.input_tokens <= window if available else None
            value["estimate_shortfall_tokens"] = max(0, usage.input_tokens - bound) if available else None
            if covers is False:
                # 实测已经给出真实成本：把上界按实测结清，后续请求才不会再拿一个
                # 被证伪的上界去判定，也不会因此把图片或历史说成「过大」。
                value["pre_send_bound_tokens"] = bound
                value["reconciled_bound_tokens"] = usage.input_tokens
            value["bound_reconciled"] = covers is False
            db.execute("UPDATE contexts SET payload=? WHERE id=?", (encode(value), manifest_id))
            self.store.event(db, value["task_id"], "context.measured", self.public(value))
        if value["estimate_valid"] is False:
            components = "、".join(f"{c['label']}{c['tokens']:,}（{c['status']}）"
                                   for c in value.get("token_components", []) if c.get("tokens"))
            raise Failure("CONTEXT_ESTIMATOR_INVALID",
                f"实测输入用量超过预留输出后的可用窗口（实测 {value['actual_input_tokens']:,} tokens > "
                f"可用 {value.get('available_input_tokens'):,} tokens；当时保守估算上界 "
                f"{value['estimated_tokens_upper_bound']:,} tokens）；分项：{components}。停止并重新校准上下文配方。", 422)
