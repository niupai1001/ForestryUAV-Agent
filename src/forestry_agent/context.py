from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
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
    estimate_token_count,
)

from .contracts import Failure
from .resources import native_path
from .store import Store, encode, now, uid
from .vision import visual_token_bound

ESTIMATOR = "native-usage-anchor-v10-dimension-bounded-vision"
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


def unpaid_components(current, measured):
    """Match exact paid occurrences; changed components retain their full cost."""
    paid = Counter(encode(value) for value in measured or [])
    unpaid = []
    for value in current:
        key = encode(value)
        if paid[key]:
            paid[key] -= 1
        else:
            unpaid.append(value)
    return unpaid


def instruction_components(instructions):
    """Accounting only: retain exact prompt bytes, split canonical app facts.

    Only the application's canonical facts envelope has stable field boundaries.
    Arbitrary instructions, malformed/noncanonical JSON and source text remain
    whole components. Matching occurrences never refunds old provider usage.
    """
    components = []
    for text in instructions:
        if text.startswith(TASK_FACT_PREFIX):
            payload = text[len(TASK_FACT_PREFIX):]
            try:
                facts,end = json.JSONDecoder().raw_decode(payload)
                if isinstance(facts,dict) and encode(facts) == payload[:end]:
                    components.append({'kind':'facts_frame','text':TASK_FACT_PREFIX+'{}'})
                    components.extend({'kind':'facts_field','text':encode({key:value})} for key,value in facts.items())
                    components.extend({'kind':'facts_guidance','text':line} for line in payload[end:].splitlines(keepends=True))
                    continue
            except ValueError:
                pass
        components.append({'kind':'instruction','text':text})
    return components


def input_bound(messages, params, tools, *, measured_tools_sha256=None, measured_tools=None, measured_instructions=None,
                measured_usage_baseline=None):
    """Count a prepared request using native measured usage plus conservative deltas."""
    counted = list(messages)
    instructions = "\n".join(part.content for part in params.instruction_parts)
    # Count only: leave native messages/checkpoints untouched. The next request's
    # dynamic instructions may differ from the historical ModelRequest instructions.
    if counted and isinstance(counted[-1], ModelRequest):
        counted[-1] = replace(counted[-1], instructions=instructions)
    else:
        counted.append(ModelRequest(parts=[], instructions=instructions))
    anchored = any(isinstance(message, ModelResponse) and message.usage.input_tokens > 0 for message in counted)
    measured = anchored or measured_usage_baseline is not None
    instruction_delta = 0
    if measured and measured_instructions is not None:
        # Provider usage already includes the previous request's instructions.
        # Keep that entire cost (including removed/changed parts), then pay the
        # full UTF-8 bound for every new or changed occurrence. Do not subtract
        # heuristic compaction reclaim from measured provider usage.
        instruction_delta = sum(byte_bound(value['text']) for value in unpaid_components(
            instruction_components([part.content for part in params.instruction_parts]),
            instruction_components(measured_instructions)))
        counted = [replace(message, instructions=None) if isinstance(message, ModelRequest) else message for message in counted]
    text_bound = estimate_context_tokens(counted, byte_bound, model_request_parameters=params) if anchored else estimate_token_count(counted, byte_bound)
    if not anchored and measured_usage_baseline is not None:
        # Native compaction may remove every response. A verified earlier request
        # still paid its fixed components. Retain ALL its input/output cost and
        # additionally pay for EVERY current message, not an estimated reclaim.
        text_bound += sum(measured_usage_baseline)
    # Unknown/changed schemas pay the full conservative cost. Exact schemas bound
    # to this response's measured request have already been paid by its native
    # input usage; do not charge them twice. Framing remains conservatively added.
    schema = encode(tools)
    known_schema = measured and measured_tools_sha256 == hashlib.sha256(schema.encode()).hexdigest()
    unpaid_tools = unpaid_components(tools, measured_tools) if measured and measured_tools is not None else tools
    schema_cost = 0 if known_schema or not unpaid_tools else byte_bound(encode(unpaid_tools))
    margin = schema_cost + 256 + 16 * len(messages) + 32 * len(tools)
    return text_bound + instruction_delta + margin + visual_token_bound(messages), anchored


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
        measured_tools_sha256 = None
        measured_tools = None
        measured_instructions = None
        measured_usage_baseline = None
        schema_receipt = None
        receipt = None
        if anchor:
            # Approval continuations import native history from a prior logical
            # turn. Its exact response still paid these schemas. A turn boundary
            # is not a tokenizer or authority boundary; task/model/assignment
            # and the exact response/schema remain mandatory.
            with self.store.transaction() as db:
                receipt = db.execute("SELECT id,turn_id,payload FROM contexts WHERE task_id=? "
                    "AND json_extract(payload,'$.assignment_id') IS ? "
                    "AND json_extract(payload,'$.response_sha256')=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (deps.task_id, deps.assignment_id, response_anchor_hash(anchor))).fetchone()
        else:
            # Only when native history contains no positive usage anchor, use
            # the existing measured provenance record. Never substitute another
            # receipt for a present but unmatched response.
            with self.store.transaction() as db:
                receipt = db.execute("SELECT id,turn_id,payload FROM contexts WHERE task_id=? "
                    "AND json_extract(payload,'$.spec_revision')=? AND json_extract(payload,'$.assignment_id') IS ? "
                    "AND json_extract(payload,'$.usage_measurement_status')='measured' "
                    "AND json_extract(payload,'$.estimate_covers_observed_usage')=1 "
                    "AND json_type(payload,'$.response_sha256')='text' ORDER BY rowid DESC LIMIT 1",
                    (deps.task_id,deps.revision,deps.assignment_id)).fetchone()
        value = json.loads(receipt["payload"]) if receipt else {}
        usage_valid = (value.get("usage_measurement_status") == "measured" and value.get("estimate_covers_observed_usage") is True and
            type(value.get('actual_input_tokens')) is int and value['actual_input_tokens'] > 0 and
            type(value.get('actual_output_tokens')) is int and value['actual_output_tokens'] >= 0)
        binding_valid = (value.get('actual_input_tokens') == anchor.usage.input_tokens and
            value.get('actual_output_tokens') == anchor.usage.output_tokens) if anchor else (
            bool(value) and value.get('model_manifest') == self.store.task(deps.task_id)['config_snapshot'])
        current_manifest = self.store.task(deps.task_id)['config_snapshot']
        same_model = (value.get('model_manifest',{}).get('model_identity') == current_manifest.get('model_identity')
                      and value.get('model_manifest',{}).get('model') == current_manifest.get('model'))
        if usage_valid and binding_valid and same_model:
            # The receipt must prove the actual paid definitions, not just
            # a hash copied into the database. Corrupt/missing snapshots
            # conservatively fall back to charging every current component.
            try:
                snapshot_data = native_path(Path(value["prepared_input_ref"])).read_bytes()
                snapshot = json.loads(snapshot_data)
                if (hashlib.sha256(snapshot_data).hexdigest() == value["prepared_input_sha256"] and
                    hashlib.sha256(encode(snapshot["tools"]).encode()).hexdigest() == value["tool_schema_sha256"] and
                    isinstance(snapshot.get("instructions"), list) and all(isinstance(p, str) for p in snapshot["instructions"])):
                    measured_tools_sha256 = value["tool_schema_sha256"]
                    measured_tools = snapshot["tools"]
                    measured_instructions = snapshot["instructions"]
                    schema_receipt = receipt
                    if not anchor:
                        measured_usage_baseline = (value['actual_input_tokens'],value['actual_output_tokens'])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        bound, anchored = input_bound(request_context.messages, params, tools, measured_tools_sha256=measured_tools_sha256,
            measured_tools=measured_tools, measured_instructions=measured_instructions,
            measured_usage_baseline=measured_usage_baseline)
        available = profile.context_limit - profile.output_limit
        if bound > available:
            with self.store.transaction() as db:
                self.store.event(db, deps.task_id, 'context.rejected', {
                    'turn_id':deps.turn_id,'estimated_tokens_upper_bound':bound,
                    'available_input_tokens':available,'context_limit':profile.context_limit,
                    'output_reserve':profile.output_limit,'estimator_id':ESTIMATOR,
                    'visual_tokens_upper_bound':visual_token_bound(request_context.messages)})
            raise Failure("CONTEXT_EXHAUSTED", "本轮需要保留的信息超过当前模型窗口；已有文件和会话已保存，可缩小本轮读取范围后继续。", 422)
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
        manifest_id = uid()
        directory = self.root / deps.task_id / (deps.assignment_id or "coordinator")
        native_path(directory).mkdir(parents=True, exist_ok=True)
        path = directory / (manifest_id + ".json")
        staging = path.with_suffix(".staging")
        native_path(staging).write_bytes(data)
        os.replace(native_path(staging), native_path(path))
        count = len(request_context.messages)
        previous = json.loads(prior["payload"]) if prior else None
        message_refs = [hashlib.sha256(encode(message).encode()).hexdigest() for message in body["messages"]]
        manifest = {"schema_version": 1, "context_manifest_id": manifest_id, "task_id": deps.task_id,
            "turn_id": deps.turn_id, "spec_revision": deps.revision, "prepared_input_ref": str(path),
            "assignment_id": deps.assignment_id, "native_run_id": native_run_id,
            "prepared_input_sha256": hashlib.sha256(data).hexdigest(), "estimated_tokens_upper_bound": bound,
            "prepared_snapshot_bytes": len(data),
            "tool_schema_sha256": tool_schema_sha256,
            "schema_cost_accounting": ("bound_measured_schema" if schema_receipt and measured_tools_sha256 == tool_schema_sha256 else
                "bound_measured_components" if schema_receipt and len(unpaid_components(tools, measured_tools)) < len(tools) else "full_current_schema"),
            "unpaid_tool_names": [t["name"] for t in unpaid_components(tools, measured_tools)],
            "instruction_cost_accounting": "bound_measured_canonical_components" if measured_instructions is not None else "native_full_changed_instructions",
            "usage_baseline_mode": "native_response" if anchored else "verified_prior_request" if measured_usage_baseline else "unmeasured",
            "usage_baseline_tokens": list(measured_usage_baseline) if measured_usage_baseline else None,
            "schema_receipt_context_ref": schema_receipt["id"] if schema_receipt else None,
            "schema_receipt_turn_id": schema_receipt["turn_id"] if schema_receipt else None,
            "uses_provider_usage_anchor": anchored,
            "native_usage_estimate": estimate_context_tokens(request_context.messages, byte_bound,
                model_request_parameters=params), "estimator_id": ESTIMATOR,
            "context_limit": profile.context_limit, "output_reserve": profile.output_limit,
            "message_count": count, "tool_names": [t["name"] for t in tools],
            "ordered_message_refs": message_refs,
            "prior_message_refs_absent": [ref for ref in previous.get("ordered_message_refs", []) if ref not in message_refs] if previous else [],
            "model_manifest": state["config_snapshot"],
            "prompt_template_sha256": hashlib.sha256(encode([p.content for p in params.instruction_parts if not p.dynamic]).encode()).hexdigest(),
            "ordered_sources": [{k: r[k] for k in ["resource_id", "version", "classification"]} for r in sources],
            "action_refs": actions, "artifact_refs": deps.engine.roles.artifacts(assignment) if assignment else state["artifact_ids"], "active_job_refs": jobs,
            "previous_context_ref": prior["id"] if prior else None,
            "history_message_count_reduced": bool(previous and count < previous["message_count"]),
            "compression_policy": "native-only-bounded-input-v1" if state["config_snapshot"].get("harness_mode", "H1") == "H0" else "harness-tiered-clear-and-window-v2-current-turn", "recorded_at": now(),
            "actual_input_tokens": None, "actual_output_tokens": None}
        with self.store.transaction() as db:
            db.execute("INSERT INTO contexts VALUES(?,?,?,?)", (manifest_id, deps.task_id, deps.turn_id, encode(manifest)))
            self.store.event(db, deps.task_id, "context.compiled", self.public(manifest))
        return manifest_id

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
            value["estimate_covers_observed_usage"] = usage.input_tokens <= value["estimated_tokens_upper_bound"] if available else None
            db.execute("UPDATE contexts SET payload=? WHERE id=?", (encode(value), manifest_id))
            self.store.event(db, value["task_id"], "context.measured", self.public(value))
        if value["estimate_covers_observed_usage"] is False:
            raise Failure("CONTEXT_ESTIMATOR_INVALID", "实测输入用量超过保守估算，停止并重新校准上下文配方", 422)
