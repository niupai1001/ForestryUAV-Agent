"""Work-plan capability declaration.

The plan is the Run's recorded reasoning about *this* task: what it is delivering,
which conditions it has actually observed, which methods those conditions support,
what is still missing, and how the result will be checked. It is a working note, not
a second source of truth -- the observations stay in the tool ledger and the event
stream, and the plan points at them.
"""

from pydantic import Field, model_validator

from ...kernel.spec import Declaration, Scope, SideEffect, ToolSpec
from ..base import Args


class PlanInput(Args):
    condition: str = Field(
        min_length=1, max_length=2000,
        description="An observed condition of the inputs, stated as a fact about them.",
    )
    evidence: str = Field(
        min_length=1, max_length=300,
        description=(
            "The observation id (obs_...) this condition was observed from. Required: "
            "a condition that cannot be pointed at is not an observation."
        ),
    )
    value: str = Field(default="", max_length=400)


class PlanCandidate(Args):
    id: str = Field(min_length=1, max_length=60, description="Short name, e.g. 'exg-otsu'.")
    method: str = Field(min_length=1, max_length=2000)
    precondition: str = Field(
        min_length=1, max_length=2000,
        description="What the method needs from the inputs to apply at all.",
    )
    evidence: str = Field(
        min_length=1, max_length=300,
        description=(
            "An observation id the Run produced, or a document body a tool returned "
            "(guide://..., knowledge://...). A catalogue listing is not a document body."
        ),
    )
    status: str = Field(default="candidate", max_length=40)
    notes: str = Field(default="", max_length=400)


class PlanQuestion(Args):
    question: str = Field(min_length=1, max_length=2000)
    blocks: str = Field(default="", max_length=200)
    resolve_with: str = Field(default="", max_length=200)


class PlanRequirement(Args):
    id: str = Field(
        default="", max_length=30,
        description="Short id, e.g. 'r1'. Must be distinct from the other requirements.",
    )
    text: str = Field(
        min_length=1, max_length=2000,
        description=(
            "One part of what the user asked for, phrased so it can be told apart from "
            "the rest. Split the request rather than writing it as one sentence."
        ),
    )
    status: str = Field(
        default="open", max_length=20,
        description=(
            "'open' or 'blocked'. A requirement is marked satisfied by the Runtime, "
            "only when the observation it cites has actually been produced -- you "
            "cannot mark work done here."
        ),
    )
    evidence: str = Field(
        default="", max_length=300,
        description=(
            "Optional: the observation id (obs_...) that will satisfy this requirement. "
            "Without a citation it can never be settled, so it stays open."
        ),
    )


class WorkPlanArgs(Args):
    objective: str | None = Field(
        default=None, max_length=2000,
        description="The delivery goal in one or two sentences.",
    )
    outputs: list[str] | None = Field(
        default=None, max_length=16, description="The concrete outputs to hand over.",
    )
    inputs: list[PlanInput] | None = Field(
        default=None, max_length=16,
        description="Conditions observed in the real inputs, each citing its observation.",
    )
    candidates: list[PlanCandidate] | None = Field(
        default=None, max_length=16,
        description="Methods under consideration, each with its precondition and evidence.",
    )
    open_questions: list[PlanQuestion] | None = Field(
        default=None, max_length=16,
        description="Evidence still missing, and which decision each gap blocks.",
    )
    acceptance: list[str] | None = Field(
        default=None, max_length=16,
        description="How the delivered product will be checked before it is reported done.",
    )
    selected: str | None = Field(
        default=None, max_length=60,
        description="The candidate id now being followed. Changing it requires reason.",
    )
    reason: str | None = Field(
        default=None, max_length=2000,
        description=(
            "Why the method changed, or why this revision is being made. Required when "
            "`selected` differs from the recorded one."
        ),
    )
    requirements: list[PlanRequirement] | None = Field(
        default=None, max_length=16,
        description=(
            "The parts of the user's request, each of which has to be met before the "
            "work can be reported as finished. Split a multi-part request instead of "
            "recording it as one item."
        ),
    )
    constraints: list[str] | None = Field(
        default=None, max_length=16,
        description="Limits that hold for the whole task, e.g. 'source is read-only'.",
    )
    blockers: list[str] | None = Field(
        default=None, max_length=16,
        description="What is currently stopping progress, stated as a fact.",
    )
    waiting_for: list[str] | None = Field(
        default=None, max_length=16,
        description="What has to arrive from the job, the environment or the user first.",
    )
    replace: bool = Field(
        default=False,
        description="Replace the whole plan instead of updating the supplied sections.",
    )
    include_history: bool = Field(
        default=False, description="Also return recent revisions.",
    )

    @model_validator(mode="after")
    def something_to_do(self):
        supplied = any((
            self.objective is not None, self.outputs is not None, self.inputs is not None,
            self.candidates is not None, self.open_questions is not None,
            self.acceptance is not None, self.selected is not None,
            self.requirements is not None, self.constraints is not None,
            self.blockers is not None, self.waiting_for is not None,
        ))
        if not supplied and not self.include_history:
            raise ValueError(
                "supply at least one plan section to record, or set include_history=true "
                "to read the current plan"
            )
        return self


SPECS = (
    ToolSpec(
        "work_plan",
        (
            "Record and revise the working plan for this task: the delivery objective, "
            "the conditions actually observed in the inputs, the candidate methods with "
            "their preconditions, the evidence still missing, and how the product will "
            "be accepted. The current plan is already shown in every request, so call "
            "this to change it, not to read it. Every condition and candidate must cite "
            "evidence this Run produced: an observation id returned by a tool result, or "
            "a document body a tool returned. Reading the guide catalogue is not "
            "evidence of a guide's content. Changing the selected method requires a "
            "reason, and every revision is kept. Record `requirements` to split what "
            "the user asked into parts that can be met separately: the work must not "
            "be reported as finished while any of them is open. You cannot mark a "
            "requirement satisfied yourself -- cite the observation that will satisfy "
            "it and the Runtime settles it once that observation exists."
        ),
        WorkPlanArgs, "EvidenceArtifact",
        Scope(reads=["workspace"], writes=["workspace"]),
        SideEffect.FILE_WRITE, "record_plan", "plan",
        declaration=Declaration(
            id="plan.evidence_linked",
            requires=(
                "Each recorded condition cites the observation it came from, and each "
                "candidate cites a document body or observation that was actually "
                "returned."
            ),
            verify_with=(
                "work_plan(include_history=true)",
            ),
            if_unmet=(
                "If a citation is refused, call the tool that would produce that "
                "evidence first -- inspect the input, or read the document -- then cite "
                "the id it returns.",
            ),
        ),
    ),
)

__all__ = [
    "PlanCandidate", "PlanInput", "PlanQuestion", "PlanRequirement", "WorkPlanArgs",
    "SPECS",
]
