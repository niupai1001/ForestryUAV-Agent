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
import re

from .classifier import evidence_from, resources_of, signature_of
from .models import Decision, FailureEvidence, _digest, _stable
from .store import FailureStore, ResourceVersions

#: How many times the *same* mechanism may be re-attempted on its free budget,
#: per retry policy. The count is the failures already observed for this
#: mechanism summed across every version on file -- so a version movement
#: re-opens the decision but does not refill the budget. "Free attempts = 1"
#: means one retry after the first failure, then a decline until real new
#: evidence changes the outcome rather than the allowance.
MODEL_DECIDES_FREE_ATTEMPTS = 1
BACKOFF_FREE_ATTEMPTS = 2


class FailurePreflight:
    """Answer "may this call run" from recorded evidence about the world."""

    def __init__(self, store: FailureStore, versions: ResourceVersions | None = None,
                 environment_facts: set[str] | None = None):
        self.store = store
        #: Default to the store's own table: it loads the versions the workspace
        #: already persisted and writes every bump through, so a resumed Run meets
        #: the world the last Run left behind instead of a reset one.
        self.versions = versions or getattr(store, "versions", None) or ResourceVersions()
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
        footprint = self._footprint(arguments)
        call_digest = _digest(_stable(arguments))
        resource_key = resources[0].id if resources else None

        # The free-retry budget is counted across *all* versions of this mechanism,
        # not the current one. A record's identity carries the version it failed at,
        # so without this a version movement would mint a fresh record with a fresh
        # budget -- and "obtain new evidence" would double as "reset the retry
        # allowance", which is exactly what repeating an identical observation used
        # to do and must not do again. New evidence still opens the door (the
        # version comparison below decides that); it does not buy more free tries.
        attempted = 0
        for record in self.store.all():
            if record.strategy_id != signature.id:
                continue
            # The mechanism is deliberately coarse about authored content, so the
            # strategy alone does not say the call is "about" what the record is
            # about; the record's resource has to appear in this call's own words.
            if not self._about_call(record, footprint, call_digest):
                continue
            attempted += int(record.occurrences or 0)
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
                action=self._action_for(record, attempted),
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

    # ---------------------------------------------------------------- matching

    @staticmethod
    def _footprint(arguments: dict) -> str:
        """The call's own words, normalised the way resource keys are.

        Every string the call names -- structured paths and modules, and the code it
        would run -- in one casefolded text with path separators unified, so a
        resource key recorded from a failure can be looked up in it directly.
        """
        parts: list[str] = []
        for value in (arguments or {}).values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, str) and item.strip():
                    parts.append(item)
        text = re.sub(r"[\\/]+", r"\\", "\n".join(parts)).casefold()
        return text

    def _about_call(self, record: FailureEvidence, footprint: str,
                    call_digest: str) -> bool:
        """Whether the recorded failure is about something *this* call names.

        Two anchors, chosen by what the record is about:

        * a record about a resource matches only calls whose own arguments or code
          mention that resource. Without this, one failed script could block every
          other script once the mechanism stopped carrying the code text;
        * a record about nothing in particular keeps the old byte-identity anchor:
          its ``call_digest`` must equal this call's, because a generic failure of
          one script says nothing about a different one.
        """
        if not record.resource_key:
            return bool(record.call_digest) and record.call_digest == call_digest
        kind, _, key = record.resource_key.partition(":")
        if not key:
            return True
        # Word-boundary, not substring: a failure about module ``sklearn`` must not
        # match a call that only uses ``sklearn_extra``, and a failure about
        # ``C:\\data`` must not match ``C:\\data_v2``.
        return re.search(r"\b" + re.escape(key) + r"\b", footprint) is not None

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

        Environment facts are deduplicated by content, and the dedup happens
        *before* anything moves. Proving ``sklearn`` importable twice is one fact,
        not two -- and a repeated check still names the module in its arguments, so
        letting the resource branch run first would bump the module's version every
        time, manufacturing progress from a repeated fact and reopening the very
        call the repeat was supposed not to reopen.
        """
        data = (output or {}).get("data") or {}
        fact = None
        if tool == "dependency_install":
            verification = data.get("verification") or {}
            if verification.get("succeeded"):
                fact = ["installed", verification.get("installed_packages") or []]
        elif tool == "environment_check" and data.get("importable"):
            fact = ["importable", sorted(data["importable"])]
        if fact is not None:
            signature = json.dumps(fact, ensure_ascii=False, sort_keys=True, default=str)
            if signature in self._environment_facts:
                return None
            self._environment_facts.add(signature)
        moved = self.observe(arguments)
        if fact is None:
            return moved
        self.versions.bump(None)
        # The environment fact is about *specific* things -- the distribution that
        # installed, the module that imported. A failure about that module is keyed
        # ``module:<name>``, which moving the workspace-wide ``*`` never touches, so
        # the retry that the install existed to enable would stay blocked. Name the
        # things the fact proved, and move whichever of them carry recorded failures.
        # Both spellings are tried because the import name and the distribution name
        # of one dependency often differ (``sklearn`` / ``scikit-learn``).
        failed = {item.resource_key for item in self.store.all() if item.resource_key}
        for name in fact[1]:
            for prefix in ("module", "distribution", "system_library"):
                key = f"{prefix}:{str(name).strip().casefold()}"
                if key in failed:
                    self.versions.bump(key)
                    moved = moved or key
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
    def _action_for(record: FailureEvidence, attempted: int | None = None):
        """What the record's retry policy allows for the next identical call.

        The count is the failures already observed for this mechanism, summed
        across every version on file (``attempted``); it defaults to the record's
        own count for direct callers. Version-aware but not version-reset: a
        moved resource version re-opens the *decision* — the version comparison,
        not this budget, is what makes new evidence matter — while the free
        attempts stay spent. ``model_decides`` hands an unclassified failure to
        the model by letting one re-attempt through (the first failure is itself
        information the model may act on, and re-running it once is how the model
        learns the failure repeats), but a call that has failed twice against an
        unchanged resource is declined with the account below -- repeating it a
        third time is not a way to find out why it failed. ``backoff`` allows two
        retries because the world, not the call, was the problem, but "transient"
        cannot mean "forever": past that the honest conclusion is that it was not
        transient, and the call needs new evidence like any deterministic one.
        """
        occurrences = int(record.occurrences or 0) if attempted is None else int(attempted)
        if record.retry_policy == "backoff":
            return "decline" if occurrences > BACKOFF_FREE_ATTEMPTS else "execute"
        if record.retry_policy == "model_decides":
            return "decline" if occurrences > MODEL_DECIDES_FREE_ATTEMPTS else "execute"
        return {
            "requires_new_evidence": "decline",
            "reconcile_only": "reconcile",
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
            return (
                f"the failure looked transient ({record.code}) but it has now happened "
                f"{int(record.occurrences or 0)} times at this resource version without "
                "a change; treat it as real and obtain new evidence first"
            )
        if record.retry_policy == "model_decides":
            return (
                f"this call already failed {int(record.occurrences or 0)} times "
                f"({record.code}) and nothing it depends on has changed; the cause is "
                "not classified, so repeating it will not explain it -- change an "
                "input or obtain new evidence first"
            )
        return (
            f"nothing the call depends on has changed: {assumption} is already known "
            f"to be false (as of resource version {record.resource_version})"
        )


__all__ = ["FailurePreflight"]
