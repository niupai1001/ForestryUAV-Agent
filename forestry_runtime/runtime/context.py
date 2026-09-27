"""Compile bounded, inspectable context for each model request.

Request assembly follows one fixed order, and every stage is recorded so a
context problem can be located after the fact:

1. select the tools the model will actually see;
2. assemble instructions (global rules + project instructions + domain catalog)
   and the current runtime facts;
3. deduplicate, clear, and ledger-compress history until it fits the budget;
4. take the final, measured budget decision;
5. persist exactly what will be sent;
6. only then issue the request.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, Callable, Sequence

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from .tokens import (
    TokenEstimator,
    context_limit_tokens,
    context_utilisation,
    input_budget_tokens,
    output_reserve_tokens,
    safety_margin_tokens,
)

LEDGER_HEADER = (
    "Earlier conversation was compressed to fit the context budget. "
    "The ledger below is the authoritative record of what already happened; "
    "tool results were replaced by their outcome, key metrics and references. "
    "Re-issue a tool call if you need the full detail again."
)
BOUNDED_MARKER = "Runtime bounded this tool result"
CLEARED_MARKER = "Runtime cleared this earlier tool result"

# Keys worth keeping when a tool result is compressed.  Job, artifact and error
# references must survive compaction or the model loses its place in the task.
_REFERENCE_KEYS = (
    "job_id", "job_type", "state", "terminal", "exit_code", "needs_finalization",
    "artifact_id", "asset_id", "result_id", "source_id", "path", "reference",
    "artifacts", "created", "progress_percent", "wait_timed_out", "resource_busy",
    # One input reference, usable unchanged by every file-consuming tool. Losing it
    # to compaction is what makes the model re-identify a file it already resolved.
    "exact_reference", "input_references", "exhausted_path", "total_human", "free_human",
)
_METRIC_HINTS = (
    "count", "total", "mean", "median", "min", "max", "std", "sum", "area",
    "valid", "valid_pixels", "nodata", "shape", "width", "height", "crs",
    "resolution", "unit", "units", "rows", "columns", "accuracy", "score",
    "ndvi", "chm", "height", "density", "coverage", "percent", "ratio",
)


class ContextBudgetExceeded(Exception):
    """Raised when even the deterministic budget pipeline cannot fit a request.

    The Run is paused with an explicit reason instead of failing with a generic
    error, so the user can raise the window, trim project instructions, or start
    a new turn.  ``ledger`` carries the accounting that led here.
    """

    def __init__(self, reason: str, *, ledger: dict | None = None):
        super().__init__(reason)
        self.ledger = ledger or {}


@dataclass
class RequestBudgetCompaction(AbstractCapability[Any]):
    """Deterministic, budget-driven history compression with full accounting.

    Runs after the harness's anchoring compaction pass so the numbers recorded
    here describe the request that is actually about to be sent.
    """

    estimator: TokenEstimator
    file_key: Callable[[ToolCallPart], str | None] | None = None
    keep_pairs: int = 4
    ledger_keep_messages: int = 6
    record: Callable[[dict], None] | None = None
    _last: dict = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------ helpers

    def budget(self) -> tuple[int, int, int, int]:
        window = context_limit_tokens()
        reserve = output_reserve_tokens()
        margin = safety_margin_tokens(window)
        raw = input_budget_tokens(limit=window, reserve=reserve, margin=margin)
        return int(raw * context_utilisation()), window, reserve, margin

    def measure(self, messages: Sequence[Any], params: Any) -> int:
        return self.estimator.estimate_request(messages, params)

    # ------------------------------------------------------------------- tiers

    def _tier_drop_thinking(self, messages: list[Any]) -> list[Any]:
        """Remove reasoning parts from every message except the most recent pair."""
        keep_from = max(0, len(messages) - 2)
        output: list[Any] = []
        for index, message in enumerate(messages):
            parts = getattr(message, "parts", None)
            if parts is None or index >= keep_from:
                output.append(message)
                continue
            kept = [
                part for part in parts
                if getattr(part, "part_kind", "") not in {"thinking", "reasoning"}
            ]
            output.append(replace(message, parts=kept) if len(kept) != len(parts) else message)
        return output

    def _tier_dedupe(self, messages: list[Any]) -> list[Any]:
        """Replace repeated observation calls with a pointer to the first result."""
        if self.file_key is None:
            return messages
        keys: set[str] = set()
        cleared: set[str] = set()
        for message in messages:
            if not isinstance(message, ModelResponse):
                continue
            for part in message.parts:
                if not isinstance(part, ToolCallPart):
                    continue
                try:
                    key = self.file_key(part)
                except Exception:
                    key = None
                if not key:
                    continue
                if key in keys:
                    cleared.add(part.tool_call_id)
                else:
                    keys.add(key)
        if not cleared:
            return messages
        return self._rewrite_returns(
            messages, cleared,
            lambda name: (
                f"{CLEARED_MARKER}: an identical call to {name} already ran in this "
                "conversation and its result is recorded above. Re-issue the call if "
                "the underlying file may have changed."
            ),
        )

    def _tier_summarize_results(self, messages: list[Any], keep_pairs: int) -> list[Any]:
        """Replace old tool results with a structured digest of what they proved."""
        order = _tool_return_order(messages)
        if len(order) <= keep_pairs:
            return messages
        targets = set(order[: len(order) - keep_pairs])
        if not targets:
            return messages
        calls = _tool_calls(messages)
        return self._rewrite_returns(
            messages, targets,
            lambda name, content, call_id: _digest_result(
                name, content, calls.get(call_id)
            ),
        )

    def _tier_ledger(self, messages: list[Any], keep_messages: int) -> list[Any]:
        """Collapse the oldest messages into one deterministic ledger message."""
        if len(messages) <= keep_messages + 2:
            return messages
        head = messages[: len(messages) - keep_messages]
        tail = messages[len(messages) - keep_messages:]
        ledger = _build_ledger(head)
        if not ledger:
            return messages
        return [ModelRequest(parts=[UserPromptPart(content=ledger)]), *tail]

    def _rewrite_returns(
        self,
        messages: list[Any],
        target_ids: set[str],
        render: Callable[..., str],
    ) -> list[Any]:
        calls = _tool_calls(messages)
        output: list[Any] = []
        for message in messages:
            if not isinstance(message, ModelRequest):
                output.append(message)
                continue
            parts = []
            changed = False
            for part in message.parts:
                # Exact type check: typed ToolReturnPart subclasses carry structured
                # discovery payloads that core re-parses on every request.
                if type(part) is ToolReturnPart and part.tool_call_id in target_ids:
                    name = calls.get(part.tool_call_id, {}).get("name") or part.tool_name or "tool"
                    try:
                        rendered = render(name, part.content, part.tool_call_id)
                    except TypeError:
                        rendered = render(name)
                    if str(part.content) != rendered:
                        parts.append(replace(part, content=rendered))
                        changed = True
                        continue
                parts.append(part)
            output.append(replace(message, parts=parts) if changed else message)
        return output

    # -------------------------------------------------------------------- hook

    async def before_model_request(self, ctx, request_context):
        params = request_context.model_request_parameters
        messages: list[Any] = list(request_context.messages)
        budget, window, reserve, margin = self.budget()
        measured_before = self.measure(messages, params)
        ledger: dict[str, Any] = {
            "compaction": "request-budget-v2",
            "input_budget_tokens": budget,
            "context_limit_tokens": window,
            "output_reserve_tokens": reserve,
            "safety_margin_tokens": margin,
            "utilisation": context_utilisation(),
            "estimated_input_tokens_before": measured_before,
            "compaction_tiers_applied": [],
        }
        if measured_before <= budget:
            ledger["estimated_input_tokens"] = measured_before
            ledger["tokens_reclaimed"] = 0
            self._last = ledger
            self._record(ledger)
            return request_context

        tiers: list[tuple[str, Callable[[list[Any]], list[Any]]]] = [
            ("drop_thinking", self._tier_drop_thinking),
            ("dedupe_observations", self._tier_dedupe),
            ("digest_tool_results", lambda items: self._tier_summarize_results(items, self.keep_pairs)),
            ("digest_tool_results_aggressive", lambda items: self._tier_summarize_results(items, 2)),
            ("ledger", lambda items: self._tier_ledger(items, self.ledger_keep_messages)),
            ("ledger_aggressive", lambda items: self._tier_ledger(items, 2)),
        ]
        current = messages
        for name, tier in tiers:
            try:
                candidate = tier(current)
            except Exception as exc:  # a failing tier must never kill the request
                ledger["compaction_tiers_applied"].append(
                    {"tier": name, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            measured = self.measure(candidate, params)
            ledger["compaction_tiers_applied"].append({
                "tier": name,
                "tokens_after": measured,
                "tokens_reclaimed": max(0, self.measure(current, params) - measured),
            })
            current = candidate
            if measured <= budget:
                break

        measured_after = self.measure(current, params)
        ledger["estimated_input_tokens"] = measured_after
        ledger["tokens_reclaimed"] = max(0, measured_before - measured_after)
        self._last = ledger
        self._record(ledger)
        if measured_after > budget:
            ledger["fits"] = False
            raise ContextBudgetExceeded(
                "Compiled model context still exceeds the input budget after "
                f"deterministic compaction ({measured_after} > {budget} tokens; "
                f"window {window}, output reserve {reserve}, safety margin {margin}). "
                "Reduce project instructions, start a new Turn, or raise OLLAMA_CONTEXT.",
                ledger=ledger,
            )
        ledger["fits"] = True
        request_context.messages = current
        return request_context

    def _record(self, ledger: dict) -> None:
        if self.record is not None:
            try:
                self.record(ledger)
            except Exception:
                pass

    @property
    def last_ledger(self) -> dict:
        return dict(self._last)

    def ledger_for(self, messages: Sequence[Any], params: Any) -> dict:
        """Return the recorded ledger only if it still describes these messages.

        Returns ``None`` when the history changed after the budget pass, so a
        persisted record never claims a token count it did not measure.
        """
        ledger = self._last
        if not ledger:
            return {}
        if ledger.get("estimated_input_tokens") != self.measure(messages, params):
            return {}
        return dict(ledger)


# ---------------------------------------------------------------------------
# Deterministic rendering helpers
# ---------------------------------------------------------------------------


def _tool_calls(messages: Sequence[Any]) -> dict[str, dict]:
    calls: dict[str, dict] = {}
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_call_id:
                calls[part.tool_call_id] = {"name": part.tool_name, "args": part.args}
    return calls


def _tool_return_order(messages: Sequence[Any]) -> list[str]:
    order: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if type(part) is ToolReturnPart and part.tool_call_id:
                order.append(part.tool_call_id)
    return order


def _decode(content: Any) -> Any:
    if isinstance(content, (dict, list)):
        return content
    if isinstance(content, str):
        text = content.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except ValueError:
                return None
    return None


def _collect_metrics(value: Any, depth: int = 0, prefix: str = "") -> dict:
    """Pick out scalar numbers and units that describe an outcome."""
    metrics: dict[str, Any] = {}
    if depth > 3 or not isinstance(value, dict):
        return metrics
    for key, item in value.items():
        name = f"{prefix}{key}"
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)) and any(
            hint in key.casefold() for hint in _METRIC_HINTS
        ):
            metrics[name] = item
        elif isinstance(item, str) and key.casefold() in {"unit", "units", "crs", "reference"}:
            metrics[name] = item[:200]
        elif (
            isinstance(item, (list, tuple))
            and item
            and all(isinstance(entry, (int, float)) and not isinstance(entry, bool) for entry in item)
            and len(item) <= 8
            and any(hint in key.casefold() for hint in _METRIC_HINTS)
        ):
            # `shape`, `bounds`, `extent` and similar describe an outcome as a
            # short numeric vector; keep it rather than dropping it as non-scalar.
            metrics[name] = list(item)
        elif isinstance(item, dict):
            metrics.update(_collect_metrics(item, depth + 1, f"{name}."))
    return metrics


def _collect_references(value: Any, depth: int = 0) -> dict:
    references: dict[str, Any] = {}
    if depth > 3 or not isinstance(value, dict):
        return references
    for key, item in value.items():
        if key in _REFERENCE_KEYS:
            if isinstance(item, (str, int, float, bool)) or item is None:
                references[key] = item
            elif isinstance(item, list) and len(item) <= 12:
                references[key] = [
                    entry if isinstance(entry, (str, int, float, bool)) else
                    {k: entry.get(k) for k in ("id", "job_id", "asset_id", "path", "name")
                     if isinstance(entry, dict) and entry.get(k) is not None}
                    for entry in item
                ]
        elif isinstance(item, dict):
            references.update(_collect_references(item, depth + 1))
    return references


def _digest_result(name: str, content: Any, call: dict | None) -> str:
    """Render one old tool result as status + metrics + references + errors."""
    payload = _decode(content)
    arguments = (call or {}).get("args")
    summary: dict[str, Any] = {
        "tool": name,
        "compressed": True,
        "note": "Full detail was removed to fit the context budget; call the tool "
                "again or read the referenced artifact if you need it.",
    }
    if isinstance(arguments, dict) and arguments:
        summary["called_with"] = {
            key: (value if isinstance(value, (str, int, float, bool)) or value is None
                  else f"<{type(value).__name__}>")
            for key, value in list(arguments.items())[:8]
        }
    if payload is None:
        text = content if isinstance(content, str) else str(content)
        summary["outcome"] = text[:400] if text else "no content"
        if len(text) > 400:
            summary["outcome_truncated_chars"] = len(text)
        return json.dumps(summary, ensure_ascii=False)

    if isinstance(payload, dict):
        summary["ok"] = payload.get("ok", payload.get("outcome_ok"))
        error = payload.get("error")
        if error:
            summary["error"] = str(error)[:400]
        failure = payload.get("failure")
        if isinstance(failure, dict):
            summary["failure"] = {
                key: failure.get(key) for key in ("stage", "code", "message")
                if failure.get(key) is not None
            }
        data = payload.get("data")
        metrics = _collect_metrics(data if data is not None else payload)
        if metrics:
            summary["metrics"] = dict(list(metrics.items())[:24])
        references = _collect_references(payload)
        if references:
            summary["references"] = references
        if not metrics and not references and not error:
            encoded = json.dumps(payload, ensure_ascii=False)
            summary["outcome"] = encoded[:400]
            if len(encoded) > 400:
                summary["outcome_truncated_chars"] = len(encoded)
    else:
        encoded = json.dumps(payload, ensure_ascii=False)
        summary["outcome"] = encoded[:400]
        if len(encoded) > 400:
            summary["outcome_truncated_chars"] = len(encoded)
    return json.dumps(summary, ensure_ascii=False, allow_nan=False)


def _build_ledger(messages: Sequence[Any]) -> str:
    """Deterministically summarise a stretch of conversation.

    User turns keep their goal text; assistant turns and tool activity collapse
    to one line each with references preserved.
    """
    objectives: list[str] = []
    steps: list[str] = []
    calls = _tool_calls(messages)
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    text = (part.content if isinstance(part.content, str) else str(part.content)).strip()
                    if text:
                        objectives.append(text if len(text) <= 600 else text[:600] + " …")
                elif type(part) is ToolReturnPart:
                    name = calls.get(part.tool_call_id, {}).get("name") or part.tool_name or "tool"
                    steps.append(_digest_result(name, part.content, calls.get(part.tool_call_id)))
        elif isinstance(message, ModelResponse):
            names = [part.tool_name for part in message.parts if isinstance(part, ToolCallPart)]
            text = "\n".join(
                part.content for part in message.parts
                if isinstance(part, TextPart) and part.content
            ).strip()
            if names:
                steps.append(json.dumps({"called": names}, ensure_ascii=False))
            elif text:
                steps.append(json.dumps(
                    {"answered": text[:300] + (" …" if len(text) > 300 else "")},
                    ensure_ascii=False,
                ))
    if not objectives and not steps:
        return ""
    lines = [LEDGER_HEADER, "", "User objectives in this conversation:"]
    lines.extend(f"- {item}" for item in objectives[-8:])
    if steps:
        lines.append("")
        lines.append("Recorded activity (oldest first):")
        lines.extend(f"- {item}" for item in steps[-40:])
    return "\n".join(lines)


class ContextCompiler:
    """Build request-local facts without owning model history.

    Instruction assembly is layered and ordered stable-to-volatile:

    1. the global collaboration rules (``Agent(instructions=SYSTEM)``);
    2. the project instructions, pinned to the revision this Turn started with;
    3. the domain guide catalogue -- ids and summaries only, never full text;
    4. the runtime facts, which change every request.

    The project instruction text comes from the caller's snapshot, so a mid-Turn
    edit cannot change the instructions of a Turn already in flight.
    """

    def __init__(
        self,
        toolbox,
        *,
        run_facts: Callable[[], dict] | None = None,
        project_context: Callable[[], dict] | None = None,
    ):
        self.toolbox = toolbox
        self.run_facts = run_facts
        self.project_context = project_context

    def compile(self, query: str, tool_defs: list, history_messages: int) -> tuple[list[str], dict, int]:
        parts: list[str] = []
        project = {}
        try:
            project = self.project_context() if self.project_context else {}
        except Exception:
            project = {}
        instruction_part = _project_instruction_part(project)
        if instruction_part:
            parts.append(instruction_part)
        guide_part, guide_entries = _guide_catalogue_part(tool_defs)
        if guide_part:
            parts.append(guide_part)
        # The plan sits between the reference material and the volatile facts: it is
        # this Run's own reasoning, revised as observations arrive, and it is the only
        # place where "why this method" is recorded. Placed after the catalogue so a
        # plan can cite what the catalogue lists without the catalogue being evidence.
        plan_text, plan_revision = _work_plan_part(self.toolbox)
        if plan_text:
            parts.append(plan_text)

        attachments = self.toolbox.attachment_context()
        grants = self.toolbox.workspaces.list_grants(
            self.toolbox.owner, self.toolbox.chat_id
        )
        facts: dict = {
            "workspace": "managed and writable; inspect changing values with tools",
            "attachments": attachments[:30],
            "attachment_count": len(attachments),
            "source_grants": [
                {key: item.get(key) for key in ("id", "access", "host_path")}
                for item in grants[:20]
            ],
        }
        # What this chat can reach, and how to name it. The three kinds of input are
        # addressed by three different parameters (path / asset_id / source_id), and a
        # relative path written in a message is text rather than any of them. Stating
        # the reachable set and one reference per entry is what the model cannot
        # observe for itself when the set is empty: a directory listing of an empty
        # workspace cannot say whether the user forgot to attach the file.
        facts["reachable_inputs"] = reachable_inputs(
            attachments=attachments,
            grants=grants,
            workspace=getattr(self.toolbox, "workspace", None),
        )
        if self.run_facts:
            facts["run"] = self.run_facts()
        if project:
            facts["project"] = {
                key: project.get(key) for key in (
                    "project_id", "project_name", "instruction_revision",
                ) if project.get(key) is not None
            } | {
                "confirmed_memories": project.get("confirmed_memories") or [],
                "knowledge_sources": project.get("knowledge_sources") or [],
            }
        parts.append(
            "Runtime facts for this model request:\n"
            + json.dumps(facts, ensure_ascii=False, allow_nan=False)
        )

        instruction = "\n\n".join(parts)
        manifest = {
            "compiler": "request-v3",
            "history_messages": history_messages,
            "tool_count": len(tool_defs),
            "tool_names": [getattr(item, "name", "") for item in tool_defs],
            "project_bound": bool(project.get("project_id")) if project else False,
            "instruction_revision": project.get("instruction_revision"),
            "instruction_source": project.get("instruction_source"),
            "instruction_tokens": project.get("instruction_tokens"),
            "instruction_budget_tokens": project.get("instruction_budget_tokens"),
            "retrieval_mode": None,
            "knowledge_source_count": len(project.get("knowledge_sources") or []),
            "domain_guide_count": len(guide_entries),
            "domain_guide_ids": [entry["id"] for entry in guide_entries],
            "plan_revision": plan_revision,
            "plan_present": bool(plan_text),
        }
        return parts, manifest, len(instruction)


def reachable_inputs(*, attachments: list, grants: list, workspace) -> dict:
    """What this chat can actually reach, with one usable reference per entry.

    A discovery tool answers "which files match this pattern", inside one root. This
    answers a different question the model cannot infer from such a search: *which
    roots exist at all*, and how each kind of input must be addressed. The three
    kinds take three different tool parameters (``path``, ``asset_id``,
    ``source_id``), and an uploaded file is not a filesystem path anywhere -- so a
    relative path quoted in a user message names none of them reliably.

    Two things are therefore stated explicitly rather than left to be discovered:

    * a root that exists but is **empty** says so. "No files matched" and "there is
      nothing here to match" are different facts, and only the runtime knows which
      one holds. An empty directory listing cannot express the difference, which is
      why a model told only "not found" retries other directories instead of asking
      the user for the file.
    * every entry carries ``exact_reference``: the argument values that a tool
      accepts unchanged, so the same input can be passed from one tool to the next
      without re-identifying it.
    """
    workspace_names: list[str] = []
    workspace_root = None
    try:
        workspace_root = str(workspace)
        if workspace is not None and workspace.is_dir():
            workspace_names = sorted(
                entry.name for entry in workspace.iterdir() if entry.name != ".runtime"
            )
    except OSError:
        workspace_root = None

    workspace_entry = {
        "kind": "workspace",
        "id": None,
        "path": ".",
        "contents": workspace_names,
        "empty": not workspace_names,
        "exact_reference": {"scope": "workspace", "path": "."},
    }

    asset_entries = []
    for item in attachments:
        name = str(item.get("name") or "")
        asset_entries.append({
            "kind": "asset",
            "id": item.get("id"),
            "name": name,
            "media_type": item.get("media_type"),
            "exact_reference": {"scope": "assets", "asset_id": item.get("id"), "name": name},
        })

    grant_entries = []
    for item in grants:
        grant_entries.append({
            "kind": "source",
            "id": item.get("id"),
            "host_path": item.get("host_path"),
            "access": item.get("access"),
            "exact_reference": {"scope": "source", "source_id": item.get("id"), "path": "."},
        })

    # The model is told what to do about it, in the sense of what the observation
    # means -- never which method to use or whether to stop.
    if not asset_entries and not grant_entries and workspace_entry["empty"]:
        note = (
            "Nothing is reachable in this chat: no attached asset, no authorized source "
            "directory, and the workspace holds no files. A path written in a message is "
            "text and does not make a file reachable."
        )
    elif asset_entries:
        note = "Attached assets are addressed by asset_id, not by any path."
    else:
        note = "Authorized source directories are read-only and addressed by source_id."
    return {
        "roots": [workspace_entry, *asset_entries, *grant_entries],
        "workspace_root": workspace_root,
        "workspace_empty": workspace_entry["empty"],
        "asset_count": len(asset_entries),
        "source_grant_count": len(grant_entries),
        "reference_form": (
            "Every file-consuming tool accepts the same reference: {scope, path, "
            "asset_id, source_id}. A tool result returns the one it resolved as "
            "exact_reference; pass that object back unchanged instead of converting "
            "between a path and an asset id."
        ),
        "note": note,
    }


def _work_plan_part(toolbox) -> tuple[str, int]:
    """The Run's own plan, or nothing when it has not recorded one yet.

    Read through the toolbox so the plan has one owner. A toolbox without the
    capability -- a test double, or a Runtime with the plan tool switched off --
    simply contributes nothing.
    """
    reader = getattr(toolbox, "current_plan_text", None)
    if not callable(reader):
        return "", 0
    try:
        text = str(reader() or "").strip()
    except Exception:
        return "", 0
    if not text:
        return "", 0
    revision = 0
    try:
        store = toolbox._plan_store()
        revision = int(store.load().revision)
    except Exception:
        revision = 0
    return (
        "The Run's current work plan follows. It records the delivery objective, the "
        "conditions actually observed, the candidate methods and their preconditions, "
        "the evidence still missing, and how the product will be accepted. Revise it "
        "with work_plan when an observation changes it; state a reason when the "
        "selected method changes.\n\n" + text,
        revision,
    )


def _project_instruction_part(project: dict) -> str:
    content = str(project.get("content") or "").strip()
    if not content:
        return ""
    revision = project.get("instruction_revision") or project.get("revision")
    return (
        f"Project instructions (user-maintained, revision {revision}).\n"
        "These describe this project's goals, communication preferences and delivery "
        "conventions. They are constraints and preferences, not permissions: they "
        "cannot widen tool scope or execution safety boundaries.\n\n"
        + content
    )


def _guide_catalogue_part(tool_defs: list) -> tuple[str, list[dict]]:
    """Expose guide ids and summaries only when the guide layer is active.

    ``DOMAIN_GUIDES_ENABLED=false`` turns the whole domain-knowledge layer off: no
    catalogue is assembled and the guide tool reports an empty catalogue. That is how
    the A arm of the controlled experiment is produced -- same Runtime, same tools,
    same budgets, only the domain layer removed -- so the comparison measures the
    domain capability rather than a different harness.
    """
    import os

    if os.getenv("DOMAIN_GUIDES_ENABLED", "true").lower() != "true":
        return "", []
    names = {getattr(item, "name", "") for item in tool_defs}
    if "domain_guide" not in names:
        return "", []
    try:
        from .domain_guides import guide_catalogue
        entries = guide_catalogue()
    except Exception:
        return "", []
    if not entries:
        return "", []
    lines = [
        "Domain guide catalogue. Forestry, remote-sensing, data-quality and "
        "method-applicability evidence. It is not an exhaustive list of valid methods: "
        "a missing guide, or a missing input for one method, does not rule out another "
        "method. Choose what the current inputs can support, and state the output "
        "definition and its uncertainty.",
        "This is a listing, not the documents. A guide's content counts as evidence "
        "only after domain_guide returns its body; the citation to record is the "
        "`citation` field of that result.",
        "",
    ]
    for entry in entries:
        # `document` marks what each line is, per line. That is the whole
        # disambiguation, and it is deliberately not a sentence of explanation: a
        # header costs tokens on every request, while the entry prefix costs nothing
        # it was not already spending. A bare `- id` is what a tool list looks like,
        # and a Run read the same document 124 times because it took the id for a
        # callable tool.
        lines.append(f"- document \"{entry['id']}\" ({entry['title']})")
    return "\n".join(lines), entries


__all__ = [
    "BOUNDED_MARKER",
    "CLEARED_MARKER",
    "ContextBudgetExceeded",
    "ContextCompiler",
    "LEDGER_HEADER",
    "RequestBudgetCompaction",
]
