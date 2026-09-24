"""Implemented evaluation cases and verifier entry points."""

from .capability_chm import CASE as CAPABILITY_CHM
from .capability_inventory import CASE as CAPABILITY_INVENTORY
from .capability_ndvi import CASE as CAPABILITY_NDVI
from .capability_raster_stats import CASE as CAPABILITY_RASTER_STATS
from .capability_recompute import CASE as CAPABILITY_RECOMPUTE
from .capability_supervised import CASE as CAPABILITY_SUPERVISED
from .core_changed_input import CASE as CORE_CHANGED_INPUT
from .core_csv import CASE as CORE_CSV
from .core_paths import CASE as CORE_PATHS
from .core_repair import CASE as CORE_REPAIR
from .forestry_chm import CASE as FORESTRY_CHM
from .forestry_inventory import CASE as FORESTRY_INVENTORY
from .forestry_ndvi import CASE as FORESTRY_NDVI
from .forestry_product_qa import CASE as FORESTRY_PRODUCT_QA


CASES = {
    case.id: case
    for case in (
        CORE_CHANGED_INPUT, CORE_CSV, CORE_PATHS, CORE_REPAIR,
        FORESTRY_CHM, FORESTRY_INVENTORY, FORESTRY_NDVI, FORESTRY_PRODUCT_QA,
        CAPABILITY_CHM, CAPABILITY_INVENTORY, CAPABILITY_NDVI,
        CAPABILITY_RASTER_STATS, CAPABILITY_RECOMPUTE, CAPABILITY_SUPERVISED,
    )
}

# Agent trials whose verifier module is the only one that understands their checks.
AGENT_TRIALS = {
    "capability.chm": "evaluation.verify.capability_trial",
    "capability.inventory": "evaluation.verify.capability_inventory_trial",
    "capability.ndvi": "evaluation.verify.capability_trial",
    "capability.raster_stats": "evaluation.verify.capability_trial",
    "capability.recompute": "evaluation.verify.capability_trial",
    "capability.supervised": "evaluation.verify.capability_trial",
    "core.changed_input": "evaluation.verify.core_repair_trial",
    "core.csv": "evaluation.verify.core_csv_trial",
    "core.paths": "evaluation.verify.core_paths_trial",
    "core.repair": "evaluation.verify.core_repair_trial",
    "forestry.inventory": "evaluation.verify.inventory_trial",
    "forestry.ndvi": "evaluation.verify.ndvi_trial",
    "forestry.chm": "evaluation.verify.chm_trial",
    "forestry.product_qa": "evaluation.verify.product_qa_trial",
}

# Cases that hand their verifier a declared input condition. Every case with more
# than one `fixture_conditions()` entry belongs here: without it the verifier falls
# back to its ``condition="normal"`` default, and *both* declared conditions are then
# graded as if they were the first. That is how `capability.recompute` lost its
# `changed` variant: slots 4..6 collected the changed fixture, were graded with
# `require_input_change=True` (the normal rule), and were reported as
# `recompute@normal` alongside slots 1..3 -- one row of six where there should have
# been two rows of three, with the condition the case exists to test never graded.
CONDITIONAL_CASES = frozenset({
    "capability.chm", "capability.inventory", "capability.ndvi",
    "capability.raster_stats", "capability.supervised", "capability.recompute",
    "forestry.chm",
})


GATE_VERIFIERS = {
    "gate.idempotency": ("evaluation.verify.idempotency_trial", "verify_trial"),
    "gate.permissions": ("evaluation.verify.permissions_trial", "verify_trial"),
    "gate.recovery": ("evaluation.verify.recovery_trial", "verify_trial"),
    "gate.sandbox": ("evaluation.verify.sandbox_trial", "verify_trial"),
}

# One browser collection produces independent records for both contracts.
BROWSER_CASES = {"ui.send", "ui.reconnect"}


__all__ = [
    "AGENT_TRIALS", "BROWSER_CASES", "CASES", "CONDITIONAL_CASES",
    "GATE_VERIFIERS",
]
