"""Bindings for the capability recalculation cases.

Two declared conditions, both about not reusing a stale result:

* ``normal`` -- the *data* changes between the two passes, so the earlier answer is
  stale and the second pass must recompute from the new input;
* ``changed`` -- the data is unchanged but the *requested statistic* changes (mean,
  then median), so the earlier output cannot answer the later request.

The second exists because "the file changed" is not the only reason a cached result
stops being valid, and an Agent that only watches input hashes will hand back the
mean when asked for the median.
"""

from .base import Case, Verifier


CASE = Case(
    id="capability.recompute",
    track="agent",
    group="capability",
    checks={
        "lineage": Verifier(
            "evaluation.verify.core_repair", "source_was_not_modified"
        ),
        "fresh_job": Verifier(
            "evaluation.verify.core_repair", "rerun_produced_new_result"
        ),
        "new_result": Verifier(
            "evaluation.verify.core_repair", "summary_matches"
        ),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/capability_recompute_normal",
    gold="evaluation/fixtures/gold/capability_recompute_normal.json",
    fixtures={
        "normal": "evaluation/fixtures/capability_recompute_normal",
        "changed": "evaluation/fixtures/capability_recompute_changed",
    },
)


__all__ = ["CASE"]
