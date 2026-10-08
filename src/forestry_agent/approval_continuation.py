"""Persist native deferred-call histories in the existing Harness step store."""
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai_harness import StepPersistence


class ApprovalPersistence(StepPersistence):
    async def after_run(self, ctx, *, result):
        # Harness normally saves only fully paired histories. Native approval
        # output intentionally leaves calls open. Preserve that final boundary,
        # including settled siblings, in the same store, explicitly interrupted.
        if isinstance(result.output, DeferredToolRequests):
            await self._save_continuable_snapshot(ctx, list(result.all_messages()),
                self._last_snapshot_step or ctx.run_step, state="interrupted")
        return await super().after_run(ctx, result=result)
