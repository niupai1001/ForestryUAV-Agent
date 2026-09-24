"""Scoring-rule identity, so evidence is never re-scored under changed rules.

Every record carries the rules version it was produced under, and every trial
directory carries a fingerprint of what it was collected from. Two consequences:

* changing a verifier bumps the rules version, so old evidence scores as
  ``unknown`` with an explicit reason (or is rejected) instead of being silently
  graded by new rules it never saw;
* resuming a run checks the fingerprint, so a slot collected against a different
  fixture, case or configuration is reported as a conflict rather than believed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Bump whenever a verifier's judgement changes. A rule change is not a refactor:
# historical results stay attached to the version that produced them.
#
# 2.1 -- a Runtime-initiated pause (the repeated-failure guard rail, an exhausted
#        model-call budget, or a pause request) is an Agent outcome and now scores
#        as a crash rather than as an untested slot.
# 2.2 -- the inventory composition check compares file *types*, so `.PNG`, `PNG` and
#        `png` are one answer instead of three. The first baseline run enumerated the
#        directory correctly in dotted-upper-case form and was failed for notation.
# 2.3 -- the CHM gap check accepts the missing-evidence finding as one sentence as
#        well as in a list. A Run that reported `built: false` and named the empty
#        vertical-reference field was failed for the shape of its answer.
SCORING_RULES_VERSION = "forestry-rules-2.3"

FINGERPRINT_FILE = "collection.json"


def fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return "fp_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def fixture_fingerprint(fixture: Path) -> str:
    """Content identity of a fixture directory, excluding the prompt text."""
    entries: list[list[str]] = []
    if fixture.is_dir():
        for path in sorted(fixture.iterdir()):
            if not path.is_file() or path.name == "prompt.txt":
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append([path.name, digest])
    return fingerprint({"files": entries})


def collection_fingerprint(
    *, case_id: str, condition: str, fixture: Path, configuration: dict,
    rules_version: str | None = None, runtime_environment: dict | None = None,
) -> str:
    """Identity of everything that determines what a slot measures.

    ``runtime_environment`` is the part an operator can get wrong without noticing:
    a slot collected while the container was configured for the other arm looks
    perfectly normal on disk. Folding the arm's switches into the fingerprint makes
    that a mismatch the resume check refuses, instead of evidence that silently
    belongs to a different treatment.
    """
    return fingerprint({
        "case_id": case_id,
        "condition": condition,
        "fixture": fixture_fingerprint(fixture),
        "configuration": configuration,
        "rules_version": rules_version or SCORING_RULES_VERSION,
        "runtime_environment": runtime_environment or {},
    })


def write_fingerprint(trial: Path, value: str, *, details: dict | None = None) -> None:
    trial.mkdir(parents=True, exist_ok=True)
    (trial / FINGERPRINT_FILE).write_text(
        json.dumps(
            {"fingerprint": value, "rules_version": SCORING_RULES_VERSION,
             **(details or {})},
            ensure_ascii=False, indent=2, allow_nan=False,
        ),
        encoding="utf-8",
    )


def read_fingerprint(trial: Path) -> dict | None:
    path = trial / FINGERPRINT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def fingerprint_conflict(
    recorded: dict | None, expected: str, *, case_id: str, repeat: int
) -> str | None:
    """Explain why a recorded slot cannot be reused, or return None if it can."""
    if recorded is None:
        return (
            f"{case_id} repeat {repeat} was collected before fingerprints were "
            "recorded, so this run cannot confirm it matches the current case, "
            "fixture and configuration. Re-collect it with --force."
        )
    if recorded.get("fingerprint") != expected:
        return (
            f"{case_id} repeat {repeat} was collected from different inputs than the "
            "current case, fixture or configuration. Resuming it would mix two "
            "experiments into one scorecard. Re-collect it with --force."
        )
    if recorded.get("rules_version") != SCORING_RULES_VERSION:
        return (
            f"{case_id} repeat {repeat} was scored under rules "
            f"{recorded.get('rules_version')!r}, not {SCORING_RULES_VERSION!r}. "
            "Re-verify it, or keep it in its own evidence root."
        )
    return None


__all__ = [
    "FINGERPRINT_FILE",
    "SCORING_RULES_VERSION",
    "collection_fingerprint",
    "fingerprint",
    "fingerprint_conflict",
    "fixture_fingerprint",
    "read_fingerprint",
    "write_fingerprint",
]
