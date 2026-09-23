"""Implemented evaluation cases and verifier entry points."""

from .core_csv import CASE as CORE_CSV
from .forestry_chm import CASE as FORESTRY_CHM
from .forestry_ndvi import CASE as FORESTRY_NDVI
from .forestry_product_qa import CASE as FORESTRY_PRODUCT_QA


CASES = {
    case.id: case
    for case in (CORE_CSV, FORESTRY_NDVI, FORESTRY_CHM, FORESTRY_PRODUCT_QA)
}

# Agent trials whose verifier module is the only one that understands their checks.
AGENT_TRIALS = {
    "core.csv": "evaluation.verify.core_csv_trial",
    "forestry.ndvi": "evaluation.verify.ndvi_trial",
    "forestry.chm": "evaluation.verify.chm_trial",
    "forestry.product_qa": "evaluation.verify.product_qa_trial",
}

GATE_VERIFIERS = {
    "gate.idempotency": ("evaluation.verify.idempotency_trial", "verify_trial"),
    "gate.permissions": ("evaluation.verify.permissions_trial", "verify_trial"),
    "gate.recovery": ("evaluation.verify.recovery_trial", "verify_trial"),
    "gate.sandbox": ("evaluation.verify.sandbox_trial", "verify_trial"),
}

# One browser collection produces independent records for both contracts.
BROWSER_CASES = {"ui.send", "ui.reconnect"}


__all__ = ["AGENT_TRIALS", "BROWSER_CASES", "CASES", "GATE_VERIFIERS"]
