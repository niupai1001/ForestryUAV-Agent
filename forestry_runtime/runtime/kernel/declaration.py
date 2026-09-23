"""What a tool declares about its own preconditions, and what it observed.

The split this module exists to hold:

* a **declaration** is what a tool says about when it can be used -- what must be
  true, *how the model can verify that for itself*, and what the legitimate paths
  are when it is not true. It is authored data, shipped with the tool;
* a **fact** is an authoritative observation the Runtime made -- what the
  workspace actually holds, whether a module imports, which package broke a
  pip transaction. Facts are not judgements and never tell the model what to do.

The model owns correction: it decides the method, the parameters, whether to keep
trying, whether to change approach, or whether to stop and ask the user. The
Runtime owns constraint (permissions, resource ceilings, action registration,
real execution) and, here, the supply of facts the model cannot observe for
itself.

Nothing in this module evaluates a declaration. A declaration carries
``verify_with``, not an evaluator: it tells the model *how to check*, and the
checking is a tool call the model makes. That is the deliberate difference from
a predicate the Runtime would evaluate on the model's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Declaration:
    """A tool's statement of when it applies.

    ``id`` is the stable identity used in three places at once: the declaration,
    the fact block a failure carries, and the repetition observable the model is
    shown. Without one shared id, "the same blocker appeared again" is not
    expressible and every repetition looks like a fresh situation.

    ``verify_with`` is the important field. It lists the tool calls that turn the
    precondition into an observation, so the model can establish the fact itself
    instead of inferring it from a failure.
    """

    id: str
    requires: str
    verify_with: tuple[str, ...] = ()
    if_unmet: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("A declaration needs a stable id")
        if not self.requires.strip():
            raise ValueError(f"Declaration {self.id!r} must state what it requires")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "requires": self.requires,
            "verify_with": list(self.verify_with),
            "if_unmet": list(self.if_unmet),
            "alternatives": list(self.alternatives),
            "note": self.note,
        }


#: Declarations shared by several tools, kept in one place so a tool family does
#: not restate the same precondition with slightly different wording.
COMMON: dict[str, Declaration] = {
    "input.exists": Declaration(
        id="input.exists",
        requires=(
            "Every path the request refers to must resolve to something this chat can "
            "actually reach: a file inside the workspace, an attached asset, or a file "
            "inside a directory the user explicitly authorized."
        ),
        verify_with=(
            "fs_list(scope='workspace', path='.')",
            "fs_list(scope='assets')",
            "fs_list(scope='source', source_id=...)",
        ),
        if_unmet=(
            "Report which of the three places you checked and what each returned.",
            "State that a path written in a message does not by itself upload or mount a file.",
            "Ask the user to attach the file, or to authorize the directory that holds it.",
        ),
        note=(
            "A relative path in a user message is text, not a resource reference. The Runtime "
            "cannot decide which of the three kinds it names, so do not assume one."
        ),
    ),
    "input.unambiguous": Declaration(
        id="input.unambiguous",
        requires="Exactly one reachable resource matches what the request refers to.",
        verify_with=(
            "fs_list(scope='assets')",
            "fs_search(query=..., scope='workspace')",
        ),
        if_unmet=(
            "Present the candidates you found and let the user or your own reading choose.",
            "Never silently pick one on the user's behalf.",
        ),
    ),
    "env.module_importable": Declaration(
        id="env.module_importable",
        requires="Every module the planned code imports must be importable inside the job image.",
        verify_with=(
            "environment_check(modules=[...])",
        ),
        if_unmet=(
            "Check whether the module is absent, installed but failing to import, or "
            "unavailable for this image before choosing a remedy.",
            "A module that is installed yet fails to import needs an image-level library, "
            "which no install inside the container can supply.",
            "Prefer a method that uses what the image already provides.",
        ),
    ),
    "env.capability_known": Declaration(
        id="env.capability_known",
        requires=(
            "Before promising a result, the capability it needs must be known to exist or be "
            "installable in this environment."
        ),
        verify_with=(
            "environment_check(modules=[...])",
        ),
        if_unmet=(
            "Do not name a package speculatively; check it before planning around it.",
            "If the capability is unavailable and cannot be installed, say so and offer what "
            "the environment can do instead.",
        ),
    ),
    "job.terminal": Declaration(
        id="job.terminal",
        requires="A durable job must reach a terminal state before its output is read as a result.",
        verify_with=(
            "job_status(job_id=...)",
            "job_wait(job_id=...)",
        ),
        if_unmet=(
            "Report that the job is still running rather than presenting partial output as final.",
        ),
    ),
}

__all__ = ["COMMON", "Declaration"]
