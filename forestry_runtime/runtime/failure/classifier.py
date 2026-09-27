"""Turn one tool result into the resource-level fact the next call has to answer.

This is where "the call failed" becomes "this assumption about that resource is
false, as of that version". The Runtime used to keep the first and infer the second
from a counter, which is why it could not tell a retry that was new work from one
that was stalling.

Everything here is mechanical -- no model output, no semantic guessing. A signature
built from a model's description of its own strategy would drift, and a hard control
rule built on a drifting key blocks calls it should allow and allows ones it should
block.
"""
from __future__ import annotations

from .models import FailureEvidence, ResourceRef, StrategySignature, _stable, _text
from .taxonomy import classify

#: Arguments that name a thing in the world rather than configure a call. Only these
#: can make two calls "about the same resource"; ``timeout_seconds`` cannot.
RESOURCE_ARGUMENTS: tuple[str, ...] = (
    "path", "folder_path", "source_id", "asset_id", "module", "package",
    "packages", "job_id", "guide_id", "citation", "name",
)

#: Assumptions a failure can falsify, keyed by resource kind. Written as the claim
#: that is no longer safe to act on, because that is what the next call needs.
ASSUMPTION_BY_KIND: dict[str, str] = {
    "path": "{key} exists and is readable",
    "asset": "asset {key} is attached to this workspace",
    "module": "module {key} is importable in the job image",
    "distribution": "distribution {key} is installed",
    "system_library": "system library {key} is present",
    "requirement": "requirement {key} is satisfied",
    "job": "job {key} reached a terminal state",
}

#: Failure codes that name a falsified assumption about a path.
PATH_CODES = {"path_not_found", "not_found", "source_path_not_found",
              "known_invalid_source_path", "requested_input_unavailable", "missing_input"}


def _normalize_path(value: str) -> str:
    return str(value).replace("/", "\\").rstrip("\\").casefold()


def _kind_for_argument(name: str) -> str:
    return {
        "path": "path", "folder_path": "path", "source_id": "asset", "asset_id": "asset",
        "module": "module", "package": "distribution", "packages": "distribution",
        "job_id": "job", "guide_id": "asset", "citation": "asset", "name": "asset",
    }.get(name, "asset")


def resources_of(arguments: dict) -> list[ResourceRef]:
    """The things this call is about, read from its arguments.

    Scope matters as much as identity: a call that names no path is not "about" every
    path, so it is recorded against nothing in particular and cannot be unlocked by
    an observation of an unrelated directory.
    """
    found: list[ResourceRef] = []
    for name in RESOURCE_ARGUMENTS:
        if name not in arguments:
            continue
        value = arguments.get(name)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, str) or not item.strip():
                continue
            kind = _kind_for_argument(name)
            key = _normalize_path(item) if kind == "path" else item.strip().casefold()
            ref = ResourceRef(kind=kind, key=key)
            if ref not in found:
                found.append(ref)
    return found


def resource_of_failure(failure: dict) -> ResourceRef | None:
    """The resource a failure itself names as missing or invalid.

    Real failures name the thing in several shapes, and reading only one of them is
    what let the old counter treat six distinct failure shapes as six fresh
    situations: a structured ``missing`` list, plain string lists, and single keys.
    """
    if not isinstance(failure, dict):
        return None
    missing = failure.get("missing")
    if isinstance(missing, list):
        for entry in missing:
            if not isinstance(entry, dict):
                continue
            value = next(
                (entry[key] for key in ("path", "asset_id", "name", "module", "value")
                 if entry.get(key)), None,
            )
            if value:
                kind = str(entry.get("kind") or "").strip().casefold() or "asset"
                if kind in {"path", "file", "folder", "directory"}:
                    kind = "path"
                return ResourceRef(
                    kind=kind,
                    key=_normalize_path(value) if kind == "path" else str(value).strip().casefold(),
                )
    for key, kind in (("unverified_modules", "module"), ("available_assets", "asset"),
                      ("requirements", "requirement"),
                      ("missing_from_manifest", "distribution")):
        values = failure.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.strip():
                    return ResourceRef(kind=kind, key=value.strip().casefold())
    for key, kind in (("missing_system_library", "system_library"),
                      ("requested_path", "path"), ("path", "path"),
                      ("module", "module"), ("job_id", "job")):
        value = failure.get(key)
        if isinstance(value, str) and value.strip():
            return ResourceRef(
                kind=kind,
                key=_normalize_path(value) if kind == "path" else value.strip().casefold(),
            )
    # Some producers nest the evidence instead of listing it at the top level.
    nested = failure.get("evidence")
    if isinstance(nested, dict):
        return resource_of_failure(nested)
    return None


def assumptions_of(failure: dict, resources: list[ResourceRef]) -> list[str]:
    """The claims this failure falsified, phrased as claims.

    "path /flight/A exists" is usable by the next call in a way that
    "code=source_path_not_found" is not: it says what may no longer be assumed,
    independent of which tool happened to discover it.
    """
    failure = failure if isinstance(failure, dict) else {}
    code = str(failure.get("code") or "")
    if code in PATH_CODES or failure.get("reason") == "not_found":
        template = "{key} exists and is readable"
    else:
        template = None
    out: list[str] = []
    for ref in resources:
        pattern = template or ASSUMPTION_BY_KIND.get(ref.kind, "{key} is usable")
        text = pattern.format(key=ref.key)
        if text not in out:
            out.append(text)
    if not out:
        named = resource_of_failure(failure)
        if named is not None:
            pattern = ASSUMPTION_BY_KIND.get(named.kind, "{key} is usable")
            out.append(pattern.format(key=named.key))
    return out


def signature_of(tool: str, arguments: dict, resources: list[ResourceRef],
                 assumptions: list[str]) -> StrategySignature:
    """The mechanism of a call, with the resource factored out.

    Arguments are normalised rather than taken literally so that argument *order*
    and unset optional keys cannot manufacture a "new" strategy.
    """
    mechanism = _stable({key: value for key, value in sorted((arguments or {}).items())})
    return StrategySignature(
        tool_family=str(tool or ""),
        mechanism=mechanism,
        resource_keys=tuple(ref.id for ref in resources),
        assumptions=tuple(sorted(assumptions)),
    )


def evidence_from(tool: str, arguments: dict, output: dict, *,
                  resource_version: str = "0",
                  evidence_ids: list[str] | None = None) -> FailureEvidence:
    """Build the durable record for one failed call."""
    failure = (output or {}).get("failure") or {}
    if not isinstance(failure, dict):
        failure = {}
    arguments = arguments or {}
    #: Only what the call itself declared. The resource a failure names is a finding,
    #: not part of the mechanism: folding it into the signature would mean the
    #: signature could only be recomputed by a caller that already knew the failure,
    #: and the next call -- the one that has to be recognised -- does not.
    resources = resources_of(arguments)
    named = resource_of_failure(failure)
    assumptions = assumptions_of(failure, resources)
    label, failure_class, policy = classify(failure)
    signature = signature_of(tool, arguments, resources, [])
    return FailureEvidence(
        label=label,
        stage=str(failure.get("stage") or ""),
        code=str(failure.get("code") or ""),
        failure_class=failure_class,
        retry_policy=policy,
        resource_key=(resources[0].id if resources else (named.id if named else None)),
        resource_version=str(resource_version),
        invalid_assumptions=assumptions,
        strategy_id=signature.id,
        tool=str(tool or ""),
        evidence_ids=list(evidence_ids or []),
        message=_text(output.get("error") or failure.get("message") or "", 400),
    )


__all__ = [
    "ASSUMPTION_BY_KIND", "PATH_CODES", "RESOURCE_ARGUMENTS",
    "assumptions_of", "evidence_from", "resource_of_failure", "resources_of",
    "signature_of",
]
