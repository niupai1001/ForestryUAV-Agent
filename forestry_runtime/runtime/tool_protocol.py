"""Compatibility import; new code should use runtime.kernel.protocol."""

from .kernel.protocol import (
    ToolPreconditionError,
    execution_failure,
    inline_schema,
    normalize_arguments,
    parse_arguments,
    schema_at,
    validation_failure,
)

__all__ = [
    "ToolPreconditionError", "execution_failure", "inline_schema",
    "normalize_arguments", "parse_arguments", "schema_at",
    "validation_failure",
]
