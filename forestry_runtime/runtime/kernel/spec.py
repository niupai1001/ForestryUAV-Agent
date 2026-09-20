from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .protocol import inline_schema


class SideEffect(str, Enum):
    NONE = "none"
    FILE_WRITE = "file_write"
    DURABLE_JOB = "durable_job"
    EXTERNAL = "external"


class Scope(BaseModel):
    reads: list[Literal["workspace", "asset", "source"]] = Field(
        default_factory=lambda: ["workspace"]
    )
    writes: list[Literal["workspace", "source_file"]] = Field(default_factory=list)
    network: Literal["none", "registry_only"] = "none"
    host_path_args: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: type[BaseModel]
    returns: str
    scope: Scope
    side_effect: SideEffect
    equivalence_group: str
    verification: str | None = None
    plugin: str = "core"
    keywords: tuple[str, ...] = ()
    deferred: bool = False
    handler: Callable | None = None

    def model_schema(self) -> dict:
        return inline_schema(self.params.model_json_schema())

    def trace_summary(self) -> dict:
        return {
            "side_effect": self.side_effect.value,
            "equivalent": self.equivalence_group,
            "scope": self.scope.model_dump(),
            "plugin": self.plugin,
            "returns": self.returns,
            "verification": self.verification,
        }


ARTIFACT_TYPES = {
    "TextArtifact": {"suffixes": {".txt", ".md", ".json", ".csv"}, "verifier": "text"},
    "TableArtifact": {"suffixes": {".csv", ".tsv"}, "verifier": "csv"},
    "RasterArtifact": {"suffixes": {".tif", ".tiff", ".vrt"}, "verifier": "raster"},
    "ImageArtifact": {"suffixes": {".png", ".jpg", ".jpeg"}, "verifier": "image"},
    "ModelArtifact": {"suffixes": {".npz"}, "verifier": "lut"},
    "VectorArtifact": {"suffixes": {".geojson", ".shp"}, "verifier": "vector"},
    "EvidenceArtifact": {"suffixes": None, "verifier": "provenance"},
}
