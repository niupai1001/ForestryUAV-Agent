"""Decide, before a call runs, whether running it could tell the model anything new.

This replaces two rules that counted: three identical failures meant pause, and one
global evidence generation unlocked every recorded failure at once. Both were
substitutes for the question the Runtime can now ask directly -- "is there a recorded
failure, about this resource, at the version this call would meet, whose falsified
assumptions still stand?" -- and both got it wrong in opposite directions: the
counter paused a Run that had just legitimately changed the environment, and the
global generation reopened a path that was still missing.

The decision is returned rather than raised, and it carries the reason, because a
guard rail that stops work owes the model an account it can render in its own words.
"""
from __future__ import annotations

import json

from .classifier import evidence_from, resources_of, signature_of
from .models import Decision, FailureEvidence
from .store import FailureStore, ResourceVersions


class FailurePreflight:
    """Answer "may this call run" from recorded evidence about the world."""

    def __init__(self, store: FailureStore, versions: ResourceVersions | None = None,
                 environment_facts: set[str] | None = None):
        self.store = store
        self.versions = versions or ResourceVersions()
        self._environment_facts: set[str] = set(environment_facts or ())

    # -------------------------------------------------------------- the decision

    def check(self, tool: str, arguments: dict) -> Decision:
        """Whether ``tool(arguments)`` should reach the environment.

        A call is declined only when a recorded failure says the same mechanism,
        against the same resource at the same version, has already falsified the
        assumption this call rests on. Anything else runs: the Runtime has no basis
        to block it, and blocking a call it cannot justify is worse than letting one
        wasted call through.
        """
        arguments = arguments or {}
        resources = resources_of(arguments)
        signature = signature_of(tool, arguments, resources, [])
        resource_key = resources[0].id if resources else None

        for record in self.store.all():
            if record.strategy_id != signature.id:
                continue
            # Versioned against the resource the *record* names, not the one this call
            # happens to name: a failure about a module the arguments never mention is
            # still about that module, and it is that module which has to change.
            if record.resource_version != self.versions.version_of(record.resource_key):
                continue
            return Decision(
                # Only a deterministic finding justifies refusing to run the call at
                # all. A transient failure is a reason to retry later, not a reason to
                # conclude the call is wrong, and declining it would turn a slow
                # dependency into a permanent block.
                action=self._action_for(record),
                policy=record.retry_policy,
                reason=self._reason(record),
                label=record.label,
                code=record.code,
                stage=record.stage,
                message=record.message,
                evidence_id=record.id,
                resource_key=record.resource_key,
                invalid_assumptions=list(record.invalid_assumptions),
                occurrences=int(record.occurrences or 0),
            )
        return Decision(action="execute", resource_key=resource_key)

    # --------------------------------------------------------------- bookkeeping

    def record(self, tool: str, arguments: dict, output: dict, *,
               evidence_ids: list[str] | None = None) -> FailureEvidence | None:
        """Record a failed call against the version of the resource it met."""
        if (output or {}).get("outcome_ok", (output or {}).get("ok", False)):
            return None
        resources = resources_of(arguments or {})
        resource_key = resources[0].id if resources else None
        evidence = evidence_from(
            tool, arguments, output,
            resource_version=self.versions.version_of(resource_key),
            evidence_ids=evidence_ids,
        )
        return self.store.record(evidence)

    def observe_success(self, tool: str, arguments: dict, output: dict) -> str | None:
        """A success is evidence: move the version of whatever it was about.

        Two kinds of thing can move, and a Run needs both:

        * the **resource** the call named -- a path that turns out to exist, a
          distribution that turns out to install;
        * the **environment** as a whole, when a call proves a dependency is present.
          A call like ``code_run`` names no path at all, so its failures are versioned
          against the environment; without this, a successful install could never
          reopen it, and the retry that was the whole point of installing would be
          refused.

        Environment facts are deduplicated by content. Proving ``sklearn`` importable
        twice is one fact, not two, so repeating it does not manufacture progress.
        """
        moved = self.observe(arguments)
        data = (output or {}).get("data") or {}
        fact = None
        if tool == "dependency_install":
            verification = data.get("verification") or {}
            if verification.get("succeeded"):
                fact = ["installed", verification.get("installed_packages") or []]
        elif tool == "environment_check" and data.get("importable"):
            fact = ["importable", sorted(data["importable"])]
        if fact is None:
            return moved
        signature = json.dumps(fact, ensure_ascii=False, sort_keys=True, default=str)
        if signature in self._environment_facts:
            return moved
        self._environment_facts.add(signature)
        self.versions.bump(None)
        return moved or "*"

    def observe(self, arguments: dict) -> str | None:
        """A successful observation: move the version of the resource it was about.

        Returns the resource whose version moved, so the caller can tell "this failure
        is now re-openable" from "nothing relevant changed".

        Only resources that already carry a recorded failure are bumped. Bumping
        everything an observation happens to name would let a successful call about an
        unrelated thing advance a version it has no bearing on -- and it is the
        unrelated-observation case, not the related one, that the single global
        counter got wrong.
        """
        resources = resources_of(arguments or {})
        if not resources:
            return None
        failed = {item.resource_key for item in self.store.all() if item.resource_key}
        moved: str | None = None
        for ref in resources:
            if ref.id not in failed:
                continue
            self.versions.bump(ref.id)
            moved = moved or ref.id
        return moved

    # ------------------------------------------------------------------ wording

    @staticmethod
    def _action_for(record: FailureEvidence):
        """Which policies actually stop a call from running.

        ``model_decides`` declines too, and that is deliberate rather than a
        contradiction: an unclassified failure is not declared *permanently* failed --
        a new version of the resource still reopens it -- but re-running a
        byte-identical call against unchanged evidence is not a way to find out why it
        failed. It produces the same failure and spends a model call. Only a transient
        failure is allowed through, because there the world, not the call, was the
        problem.
        """
        return {
            "requires_new_evidence": "decline",
            "reconcile_only": "reconcile",
            "backoff": "execute",
        }.get(record.retry_policy, "decline")

    @staticmethod
    def _reason(record: FailureEvidence) -> str:
        """Why the call was declined, in terms the model can act on.

        Names the failed assumption rather than the failure count: "three strikes" is
        a rule about the Runtime's patience, while "this path does not exist, and
        nothing about it has changed since" is a fact the model can work around.
        """
        assumption = (record.invalid_assumptions or ["this call's preconditions"])[0]
        if record.retry_policy == "reconcile_only":
            return (
                "the earlier attempt may already have taken effect, so the outstanding "
                f"question is what it did, not whether to repeat it ({assumption})"
            )
        if record.retry_policy == "backoff":
            return f"the failure looks transient ({record.code}); retry only after a change"
        if record.retry_policy == "model_decides":
            return (
                f"this call already failed ({record.code}) and nothing it depends on "
                "has changed; the cause is not classified, so repeating it will not "
                "explain it -- change an input or obtain new evidence first"
            )
        return (
            f"nothing the call depends on has changed: {assumption} is already known "
            f"to be false (as of resource version {record.resource_version})"
        )


__all__ = ["FailurePreflight"]
