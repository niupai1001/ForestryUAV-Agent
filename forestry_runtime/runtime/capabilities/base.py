"""Shared strict parameter base for capability declarations."""

from pydantic import BaseModel, ConfigDict


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


__all__ = ["Args"]
