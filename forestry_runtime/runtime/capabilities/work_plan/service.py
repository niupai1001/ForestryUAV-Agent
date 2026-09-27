"""Work-plan capability."""

from __future__ import annotations

from ...plan import PlanStore


class WorkPlanCapability:
    """Read and revise the plan for this workspace."""

    def _plan_store(self) -> PlanStore:
        store = getattr(self, "_work_plan_store", None)
        if store is None:
            store = PlanStore(self.workspace, getattr(self, "ledger", None))
            self._work_plan_store = store
        return store

    def current_plan_text(self) -> str:
        """The plan as it is shown in a model request; empty when nothing is recorded."""
        try:
            return self._plan_store().load().render()
        except Exception:
            return ""

    def work_plan(
        self, objective=None, outputs=None, inputs=None, candidates=None,
        open_questions=None, acceptance=None, selected=None, reason=None,
        replace=False, include_history=False,
    ):
        store = self._plan_store()
        supplied = any((
            objective is not None, outputs is not None, inputs is not None,
            candidates is not None, open_questions is not None,
            acceptance is not None, selected is not None,
        ))
        # Reading is not revising. A request that only asks for the current plan must
        # not append a revision, or the history fills with reads and "why did the
        # method change" becomes unanswerable from it.
        plan = store.revise(
            objective=objective,
            outputs=None if outputs is None else [item for item in outputs],
            inputs=None if inputs is None else [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in inputs
            ],
            candidates=None if candidates is None else [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in candidates
            ],
            open_questions=None if open_questions is None else [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in open_questions
            ],
            acceptance=acceptance,
            selected=selected,
            reason=reason,
            replace=bool(replace),
        ) if supplied else store.load()
        result = {
            "plan": plan.as_dict(),
            "revision": plan.revision,
            "rendered": plan.render(),
            "note": (
                "The plan is shown in every following request. Revise it when an "
                "observation changes what a candidate requires, and state the reason "
                "when the selected method changes."
            ),
        }
        if include_history:
            result["history"] = store.history(limit=10)
        return result
