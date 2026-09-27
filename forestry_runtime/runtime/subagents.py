"""Delegate a bounded piece of work to a context of its own.

The problem is not that the model cannot explore; it is that exploring inside the main
conversation costs the main conversation. A long read-only investigation -- twelve file
reads, three searches, a dead end -- stays in the history forever, is re-sent on every
following request, and pushes out the task state, which is the one thing that must not
be pushed out.

Isolation here means what it says: the child is built from its contract, the evidence
retrieved for it, and minimum project metadata. **The main history is not copied.** A
"subagent" that receives the parent's messages is just another turn with extra steps.

Only the structured result comes back -- facts, evidence ids, artifacts, open
questions. Raw logs go to the artifact or tool-result store, where they are retrievable
but do not occupy anyone's context.

**Integration state.** A ``delegate`` tool exists and is wired into the tool set, but is
**off by default** (``SUBAGENTS_ENABLED``). Two reasons, and the second is the real one:

1. The tool changes the visible schema. Every tool the model can see is a way for it to
   spend the Run, and a delegation is the most expensive thing on the list -- it costs
   model requests the parent could have spent itself.
2. Whether a delegated result is worth that cost is an empirical question, and it is
   now answerable: the verification layer can say whether a subagent's product is real.
   It has not been measured yet. Turning the tool on before the measurement is how a
   capability becomes a liability.

The permission boundary -- ``effective_tools()`` is the narrower of contract and policy,
depth is fixed at one, the parent's history is never copied -- is the part that had to
be right first, and it is enforced here rather than documented in a prompt. A
permitted-but-unenforced subagent is worse than none.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

MAX_SUMMARY = 1200
MAX_FACTS = 20
MAX_DEPTH = 1

#: Delegation is off until it has been measured. See the module docstring.
ENV_FLAG = "SUBAGENTS_ENABLED"


def enabled() -> bool:
    """Whether the parent model is offered delegation at all."""
    return os.getenv(ENV_FLAG, "false").strip().lower() == "true"

SubagentStatus = ("success", "blocked", "failed", "waiting")


@dataclass
class SubagentPolicy:
    """What one kind of subagent is allowed to do."""

    name: str
    allowed_tools: tuple[str, ...] = ()
    may_write_workspace: bool = False
    may_write_source: bool = False
    may_mutate_environment: bool = False

    def permits(self, tool: str) -> bool:
        return tool in self.allowed_tools


#: Depth is fixed at one. A subagent that can spawn subagents can spawn a context no
#: one can reason about, and the failure modes compound faster than the benefit.
POLICIES: dict[str, SubagentPolicy] = {
    "explore": SubagentPolicy(
        "explore",
        allowed_tools=("fs_list", "fs_read", "fs_search", "knowledge_search",
                       "knowledge_read", "tool_result_read"),
    ),
    "environment": SubagentPolicy(
        "environment",
        allowed_tools=("fs_list", "fs_read", "fs_write", "code_run",
                       "dependency_install", "environment_check", "job_status",
                       "job_log"),
        may_write_workspace=True,
        may_mutate_environment=True,
        # Deliberately not source: an agent fixing the environment must not "fix" the
        # code to make the environment look fixed.
        may_write_source=False,
    ),
    "verifier": SubagentPolicy(
        "verifier",
        allowed_tools=("fs_read", "fs_list", "artifacts_inspect", "artifacts_preview",
                       "code_run", "tool_result_read"),
    ),
}


@dataclass
class DelegationContract:
    objective: str = ""
    task_state_revision: int = 0
    allowed_tools: list[str] = field(default_factory=list)
    resource_scope: list[str] = field(default_factory=list)
    evidence_required: list[str] = field(default_factory=list)
    max_model_requests: int = 8
    parent_run_id: str = ""
    depth: int = 0
    kind: str = "explore"

    def effective_tools(self) -> tuple[str, ...]:
        """The narrower of the contract and the policy.

        A contract cannot grant more than its kind's policy allows: if it could, the
        permission boundary would be whatever the parent model wrote down.
        """
        policy = POLICIES.get(self.kind)
        policy_tools = policy.allowed_tools if policy else ()
        if not policy:
            return tuple(self.allowed_tools)
        granted = tuple(tool for tool in self.allowed_tools if tool in policy_tools)
        return granted or policy_tools


@dataclass
class SubagentResult:
    status: str = "success"
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    recommended_next_actions: list[str] = field(default_factory=list)

    def bounded(self) -> SubagentResult:
        """Cut the result down to what belongs in a parent context."""
        return SubagentResult(
            status=self.status,
            summary=self.summary[:MAX_SUMMARY],
            facts=[str(item)[:400] for item in self.facts][:MAX_FACTS],
            evidence_ids=list(self.evidence_ids)[:MAX_FACTS],
            artifact_ids=list(self.artifact_ids)[:MAX_FACTS],
            open_questions=[str(item)[:300] for item in self.open_questions][:10],
            recommended_next_actions=[str(item)[:300]
                                      for item in self.recommended_next_actions][:10],
        )

    def as_dict(self) -> dict:
        bounded = self.bounded()
        return {
            "status": bounded.status, "summary": bounded.summary,
            "facts": bounded.facts, "evidence_ids": bounded.evidence_ids,
            "artifact_ids": bounded.artifact_ids,
            "open_questions": bounded.open_questions,
            "recommended_next_actions": bounded.recommended_next_actions,
        }


class DelegateInput(BaseModel):
    """What the parent model hands over. The child sees nothing else.

    ``objective`` is not a hint: the child's context is built from it alone, so an
    objective that assumes shared context produces a child that cannot start.
    """

    objective: str = Field(
        description=(
            "Exactly what the subagent must find out or produce, in one or two "
            "sentences. It becomes the child's whole task -- the child cannot see this "
            "conversation, so anything not written here is not known to it."
        )
    )
    kind: str = Field(
        "explore",
        description=(
            "'explore' = read-only investigation; 'environment' = install or diagnose "
            "the execution environment; 'verifier' = check a product that already exists."
        ),
    )
    resource_scope: list[str] = Field(
        default_factory=list,
        description="Paths, sources or asset ids the subagent is allowed to look at.",
    )
    evidence_required: list[str] = Field(
        default_factory=list,
        description="What has to be true for the answer to count. Vague here means uncheckable.",
    )


def fold_result(
    *, status: str = "success", output: str = "", observations=None,
    artifacts=None, open_questions=None,
) -> SubagentResult:
    """Turn what a child Run produced into what the parent is allowed to see.

    The child's raw turns stay out of the parent's context -- that is the entire point
    of delegating. What crosses back is a bounded set of facts derived from the child's
    observation log, which is a record of what it established rather than of what it
    said. A failure is folded too: a parent that hears nothing about a dead end will
    delegate the same dead end again.
    """
    facts: list[str] = []
    for entry in observations or []:
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool", "")).strip()
        summary = str(entry.get("summary", "") or "").strip()
        if not tool:
            continue
        line = f"{tool}: {summary}" if summary else tool
        if entry.get("ok") is False:
            line = "FAILED " + line
        facts.append(line[:400])

    return SubagentResult(
        status=status if status in SubagentStatus else "failed",
        summary=(output or "").strip()[:MAX_SUMMARY],
        facts=facts,
        evidence_ids=[],
        artifact_ids=[str(item) for item in (artifacts or [])][:MAX_FACTS],
        open_questions=[str(item)[:300] for item in (open_questions or [])][:10],
        recommended_next_actions=[],
    )


class SubagentRunner:
    """Build the child's context and hold it to its permissions."""

    def __init__(self, policies: dict[str, SubagentPolicy] | None = None):
        self.policies = policies or POLICIES

    def policy_for(self, contract: DelegationContract) -> SubagentPolicy | None:
        return self.policies.get(contract.kind)

    def permits(self, contract: DelegationContract, tool: str) -> bool:
        return tool in contract.effective_tools()

    def build_child_context(
        self, contract: DelegationContract, *, retrieved=None,
        project_metadata: dict | None = None, evidence=None,
    ) -> list[dict]:
        """The child's messages. The parent's history is deliberately not among them."""
        lines = [f"Objective: {contract.objective}"]
        if contract.evidence_required:
            lines.append("Evidence required: " + ", ".join(contract.evidence_required[:10]))
        if contract.resource_scope:
            lines.append("Scope: " + ", ".join(contract.resource_scope[:10]))
        lines.append(f"Tools available: {', '.join(contract.effective_tools())}")
        lines.append(
            f"Answer with facts, not prose. At most {contract.max_model_requests} "
            "model requests; if you cannot finish, say what is still open."
        )
        for item in retrieved or []:
            lines.append(f"- [{item.source_type}] {item.id}: {item.content[:300]}")
        for item in evidence or []:
            lines.append(f"- evidence {item}")
        if project_metadata:
            lines.append("Project: " + ", ".join(
                f"{key}={value}" for key, value in list(project_metadata.items())[:6]
            ))
        return [{"role": "system", "content": "\n".join(lines)}]

    def may_delegate(self, contract: DelegationContract) -> tuple[bool, str]:
        if contract.depth >= MAX_DEPTH:
            return False, f"delegation depth is fixed at {MAX_DEPTH}"
        if contract.kind not in self.policies:
            return False, f"unknown subagent kind: {contract.kind!r}"
        if not contract.objective.strip():
            return False, "a delegation needs an objective"
        return True, ""


__all__ = [
    "ENV_FLAG", "MAX_DEPTH", "DelegateInput", "DelegationContract", "POLICIES",
    "SubagentPolicy", "SubagentResult", "SubagentRunner", "SubagentStatus",
    "enabled", "fold_result",
]
