"""Shared strict parameter base for capability declarations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InputRef(Args):
    """One way to name a file, usable by any tool that opens one.

    The same three arguments appear on every file-consuming tool, and a tool
    result's ``exact_reference`` is exactly this object, so passing an input on
    does not require re-identifying it or converting between a path and an id.

    Validation is deliberately not strict: a reference travels as a plain mapping
    -- from a tool result's ``input_references``, or from a failure's
    ``suggested_arguments`` -- and a nested reference that only accepted an
    ``InputRef`` instance would reject the very values this type exists to carry.
    Unknown keys are still rejected, so the shape stays closed.
    """

    model_config = ConfigDict(extra="forbid", strict=False)

    scope: Literal["auto", "workspace", "asset", "source"] = Field(
        default="auto",
        description=(
            "Which root this input belongs to. 'auto' resolves what the other "
            "fields already imply; copy exact_reference from a tool result to be "
            "explicit."
        ),
    )
    asset_id: str | None = None
    path: str = ""
    source_id: str | None = None

    def as_arguments(self) -> dict:
        """The flat keyword arguments every resolver accepts."""
        return {
            "scope": self.scope,
            "asset_id": self.asset_id,
            "path": self.path or "",
            "source_id": self.source_id,
        }


__all__ = ["Args", "InputRef"]
