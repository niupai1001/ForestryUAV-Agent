"""Three-layer verification and the three states a run may report.

    from runtime.verification import RunFacts, VerificationService

    report = service.evaluate(RunFacts(artifacts=[descriptor]), task=task)
    report.state   # verified_complete | delivered_unverified | failed

The default is the middle state: a product that exists and is well-formed, whose
meaning nobody checked. Reaching ``verified_complete`` requires every layer to have
actually been evaluated and passed.
"""
from __future__ import annotations

from .checks import (
    EARLY_STOP_REASONS, RunFacts, execution_checks, product_qa_checks,
    semantic_checks, structural_checks, unverified_report_reason,
)
from .models import (
    LAYERS, STATE_FAILED, STATE_UNVERIFIED, STATE_VERIFIED, VERIFICATION_STATES,
    Check, VerificationReport, clamp, fold,
)
from .builtins import default_registry, raster_semantics, register_builtins
from .registry import Verifier, VerifierRegistry
from .service import ACTION_FOR_CHECK, VerificationService

__all__ = [
    "ACTION_FOR_CHECK", "Check", "EARLY_STOP_REASONS", "LAYERS", "RunFacts",
    "STATE_FAILED", "STATE_UNVERIFIED", "STATE_VERIFIED", "VERIFICATION_STATES",
    "VerificationReport", "VerificationService", "Verifier", "VerifierRegistry",
    "clamp", "default_registry", "execution_checks", "fold", "product_qa_checks",
    "raster_semantics", "register_builtins", "semantic_checks", "structural_checks",
    "unverified_report_reason",
]
