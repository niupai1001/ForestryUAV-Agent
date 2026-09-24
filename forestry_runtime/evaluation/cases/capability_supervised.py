"""Bindings for the capability supervised-classification case.

The two conditions carry the same data; the difference is what the prompt asks the
Run to do when the model does not beat the baseline. In both, the checks are the
same: the split must not cross plots, the metrics are recomputed from the delivered
predictions, and a test accuracy materially above the majority-class baseline is
treated as evidence of leakage rather than as a better model.
"""

from .base import Case, Verifier


CASE = Case(
    id="capability.supervised",
    track="agent",
    group="capability",
    checks={
        "split": Verifier(
            "evaluation.verify.capability_supervised", "split_respects_groups"
        ),
        "metrics": Verifier(
            "evaluation.verify.capability_supervised", "predictions_and_metrics"
        ),
        "baseline": Verifier(
            "evaluation.verify.capability_supervised", "baseline_was_compared"
        ),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/capability_supervised",
    gold="evaluation/fixtures/gold/capability_supervised.json",
    fixtures={
        "normal": "evaluation/fixtures/capability_supervised",
        "gap": "evaluation/fixtures/capability_supervised_gap",
    },
)


__all__ = ["CASE"]
