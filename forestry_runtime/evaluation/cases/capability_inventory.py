"""Bindings for the capability data-inventory and suitability case.

Condition ``normal`` alone is declared here; the ``gap`` fixture directory and its
gold contract are created by ``evaluation/make_capability_inventory.py`` and the
runner expands the declared list below into its own repeat slots.
"""

from .base import Case, Verifier


CASE = Case(
    id="capability.inventory",
    track="agent",
    group="capability",
    checks={
        "counts": Verifier(
            "evaluation.verify.capability_inventory", "inventory_counts_match"
        ),
        "suitability": Verifier(
            "evaluation.verify.capability_inventory", "suitability_matches"
        ),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/capability_inventory_normal",
    gold="evaluation/fixtures/gold/capability_inventory_normal.json",
    fixtures={
        "normal": "evaluation/fixtures/capability_inventory_normal",
        "gap": "evaluation/fixtures/capability_inventory_gap",
    },
)


__all__ = ["CASE"]
