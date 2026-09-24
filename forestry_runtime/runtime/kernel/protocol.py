"""Model protocol adaptation and failure evidence without domain policy."""

import json

from pydantic import ValidationError

from ..storage import AssetError
from shared.outcome import failure_category, missing_shared_library, normalize_failure, normalize_result


class ToolPreconditionError(AssetError):
    def __init__(self, message: str, **details):
        super().__init__(message)
        self.failure_details = {
            "operation_started": False, "side_effects": "none", **details
        }


class SubmissionRefused(AssetError):
    """A submission that was deliberately not started.

    Raising rather than returning is deliberate: the tool dispatcher wraps a
    returned value in ``{"ok": True, "data": ...}``, so a refusal that must be
    reported as ``outcome_ok: false`` has to travel the exception path.
    """

    def __init__(self, reason: str, *, data: dict | None = None, **details):
        super().__init__(reason)
        self.data = data or {}
        self.failure_details = {
            "operation_started": False,
            "side_effects": "none",
            **details,
        }


def inline_schema(schema):
    definitions = schema.get("$defs", {})

    def expand(value, stack=()):
        if isinstance(value, list):
            return [expand(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/$defs/") or ref in stack:
                raise ValueError(f"Unsupported tool schema reference: {ref}")
            target = definitions[ref.removeprefix("#/$defs/")]
            return expand(target | {k: v for k, v in value.items() if k != "$ref"}, stack + (ref,))
        return {k: expand(v, stack) for k, v in value.items() if k != "$defs"}

    return expand(schema)


def normalize_arguments(value, schema, path=()):
    changes = []
    candidates = schema.get("anyOf", [schema])
    types = {item.get("type") for item in candidates}
    if isinstance(value, str) and "string" not in types and types & {"object", "array"}:
        try:
            decoded = json.loads(value)
        except ValueError:
            pass
        else:
            if (isinstance(decoded, dict) and "object" in types) or (isinstance(decoded, list) and "array" in types):
                value = decoded
                changes.append({"field": list(path), "operation": "decode_json_container"})
    kind = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else None
    selected = next((item for item in candidates if item.get("type") == kind), schema)
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            output[key], nested = normalize_arguments(item, selected.get("properties", {}).get(key, {}), path + (key,))
            changes.extend(nested)
        value = output
    elif isinstance(value, list):
        output = []
        for index, item in enumerate(value):
            normalized, nested = normalize_arguments(item, selected.get("items", {}), path + (index,))
            output.append(normalized)
            changes.extend(nested)
        value = output
    return value, changes


def schema_at(schema, location):
    for part in location:
        schema = schema.get("items", {}) if isinstance(part, int) else schema.get("properties", {}).get(part, {})
    return schema


def validation_failure(exc: ValidationError, schema):
    issues = []
    for error in exc.errors(include_url=False, include_context=False):
        location = error["loc"]
        issues.append({
            "field": list(location), "code": error["type"],
            "message": error["msg"],
            "received_type": type(error.get("input")).__name__,
            "expected": schema_at(schema, location),
        })
    return normalize_result({
        "ok": False,
        "error": "工具参数校验失败，业务函数尚未执行。",
        "failure": {
            "stage": "arguments", "code": "invalid_arguments",
            "operation_started": False, "issues": issues,
        },
    })


def parse_arguments(model, arguments):
    schema = inline_schema(model.model_json_schema())
    normalized, changes = normalize_arguments(arguments, schema)
    try:
        return model.model_validate(normalized), changes, None
    except ValidationError as exc:
        return None, changes, validation_failure(exc, schema)


def execution_failure(exc):
    details = getattr(exc, "failure_details", None)
    if isinstance(details, dict):
        result = {
            "ok": False,
            "error": str(exc),
            "failure": {"stage": "preconditions", **details},
        }
        # A deliberate refusal still carries the identifiers the caller needs to
        # continue (a job id, a suggested tool), so keep them on the result.
        data = getattr(exc, "data", None)
        if isinstance(data, dict) and data:
            result["data"] = data
        return normalize_result(result)
    if isinstance(exc, AssetError):
        return normalize_result({
            "ok": False, "error": str(exc),
            "failure": {
                "stage": "preconditions", "code": type(exc).__name__,
                "operation_started": False, "side_effects": "none",
            },
        })
    if isinstance(exc, (ImportError, OSError)):
        missing_library = missing_shared_library(str(exc))
        if missing_library:
            return normalize_result({
                "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "failure": {
                    "stage": "environment", "code": "missing_shared_library",
                    "reason": "missing_env",
                    "missing": [{"kind": "shared_library", "name": missing_library}],
                    "operation_started": True, "side_effects": "unknown",
                },
            })
    return normalize_result({
        "ok": False, "error": f"{type(exc).__name__}: {exc}",
        "failure": {
            "stage": "execution", "code": type(exc).__name__,
            "operation_started": True, "side_effects": "unknown",
        },
    })
