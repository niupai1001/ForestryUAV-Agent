"""One way to name an input file, shared by every capability that opens one.

The observed failure this replaces: a workspace file could be listed by ``fs_list``
and still not reachable from ``inspect_raster``, because the domain tools accepted
only an ``asset_id`` while the file was addressed by a relative ``path``. The model
saw one file and two incompatible ways to name it, and no tool said which
conversion was legal.

So there is one reference form. It is a flat set of arguments any file-consuming
tool accepts unchanged::

    {"scope": "workspace", "path": "oam-02.tif"}
    {"scope": "asset", "asset_id": "asset_..."}
    {"scope": "source", "source_id": "grant_...", "path": "flight/2026-05/raw.tif"}

``exact_reference`` in a tool result is exactly that object, so a result can be
passed to the next tool without re-identifying the file. ``scope="auto"`` resolves
what the other arguments already imply, which keeps every previously valid call
valid.

A source file is *staged* into the workspace before it is returned. The domain
analyzers open local paths, and staging keeps the grant check in the one place the
grant lives instead of teaching each analyzer a second, network-backed reader. The
staged copy records where it came from, so provenance survives the copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import mimetypes
from pathlib import Path

from ..kernel.protocol import ToolPreconditionError
from ..storage import AssetError
from ..workspace import BridgeRequestError, is_host_path


SCOPES = ("auto", "workspace", "asset", "source")
#: Where staged copies of authorized source files live inside the workspace.
STAGED_PREFIX = ".runtime/staged/"


@dataclass(frozen=True)
class ResolvedInput:
    """A file one capability can actually open, plus how to name it again."""

    scope: str
    reference: dict
    local_path: Path
    name: str
    size: int
    media_type: str | None
    asset: dict | None = None
    provenance: dict = field(default_factory=dict)

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    def metadata(self) -> dict:
        """Measured facts about the file, and the reference that reaches it again."""
        return {
            "scope": self.scope,
            "name": self.name,
            "size_bytes": self.size,
            "suffix": self.suffix,
            "media_type": self.media_type,
            "exact_reference": dict(self.reference),
            "provenance": dict(self.provenance),
        }

    def asset_view(self) -> dict:
        """An asset-shaped mapping, for service bodies written before this existed.

        When the input *is* an asset the real record is returned untouched, so a
        tool's output keeps the ``id``/``parent_id`` fields callers already read.
        For a workspace file or a staged source file there is no asset to report,
        so the mapping carries the measured facts and the reference instead of a
        null id that would read as "an asset whose id is unknown".
        """
        if self.asset is not None:
            return dict(self.asset)
        return {
            "name": self.name,
            "size": self.size,
            "media_type": self.media_type,
            "input_reference": dict(self.reference),
            "provenance": dict(self.provenance),
        }

    def source_asset_id(self) -> str | None:
        """The parent asset a derived output belongs to, or ``None``."""
        return self.asset["id"] if self.asset else None


def implied_scope(scope: str, path: str, asset_id: str | None, source_id: str | None) -> str:
    """Which of the three roots the arguments name, when ``scope`` is ``auto``.

    Stated as one function because three capabilities used to answer this
    separately, and they disagreed: an absolute host path was routed to the
    authorized source by ``fs_list`` and rejected as a workspace path by the
    artifact tools.
    """
    chosen = str(scope or "auto").strip().casefold()
    if chosen in ("workspace", "asset", "source"):
        return chosen
    if asset_id:
        return "asset"
    if source_id:
        return "source"
    if path and is_host_path(path):
        return "source"
    return "workspace"


class InputResolution:
    """Grant and reference resolution shared by core and domain capabilities.

    Both toolboxes call this. Neither may import the other's implementation, and
    duplicating the grant rules is how the domain tools ended up unable to read a
    file the core tools had just listed.
    """

    # --------------------------------------------------------------- references

    def attachment_context(self) -> list[dict]:
        raise NotImplementedError

    def _asset_entry(self, asset_id: str) -> tuple[dict, Path]:
        if not asset_id or asset_id not in self.allowed:
            raise ToolPreconditionError(
                "An attached or generated asset_id is required.",
                code="asset_not_available", reason="inapplicable",
                missing=[{"kind": "asset_id", "value": asset_id}],
                checked_scope={"scope": "chat_assets"},
                candidates=[
                    {"asset_id": item["id"], "name": item["name"]}
                    for item in self.attachment_context()[:10]
                ],
            )
        return self.store.get(asset_id, self.owner), self.store.path(asset_id, self.owner)

    def _source_grant(self, source_id: str | None, path: str, access: str = "read") -> tuple[dict, str]:
        """The grant that authorizes *path*, creating one only when the user asked."""
        if source_id:
            grant = self.workspaces.get_grant(self.owner, self.chat_id, source_id)
            if access == "write" and grant["access"] != "write":
                raise AssetError("This source grant is read-only")
            return grant, path
        if not is_host_path(path):
            raise ToolPreconditionError(
                "A relative source path needs the source_id it belongs to.",
                code="invalid_arguments", reason="ambiguous",
                requested_path=path,
                missing=[{"kind": "source_id", "value": None}],
                candidates=[
                    {"source_id": item["id"], "host_path": item["host_path"]}
                    for item in self.workspaces.list_grants(self.owner, self.chat_id)[:10]
                ],
            )
        covered = self.workspaces.covering_read_grant(self.owner, self.chat_id, path)
        if covered:
            return covered
        grant = self.workspaces.authorize_latest_request(
            self.owner, self.chat_id, path, self.latest_user
        )
        return grant, "."

    def _stage_source(self, grant: dict, relative: str) -> dict:
        """Materialize one authorized file so a local reader can open it."""
        try:
            return self.workspaces.bridge.fs("stage", self.workspaces.bridge_payload(
                grant, path=relative, workspace=str(self.workspaces.host_workspace(self.store)),
            ))
        except BridgeRequestError as exc:
            # The bridge already decided; carry its code through unchanged so the
            # caller learns whether the file was missing, unauthorized or too large.
            raise ToolPreconditionError(
                f"Source file could not be prepared for local analysis: {exc}",
                code=exc.code or "source_stage_failed",
                reason="not_found" if exc.code == "path_not_found" else "execution_error",
                source_id=grant["id"],
                requested_path=relative,
                **({"size_bytes": exc.details.get("size_bytes"),
                    "limit_bytes": exc.details.get("limit_bytes")}
                   if exc.code == "staged_input_too_large" else {}),
            ) from exc

    # ----------------------------------------------------------------- resolve

    def resolve_input(
        self, *, scope: str = "auto", path: str = "", asset_id: str | None = None,
        source_id: str | None = None,
    ) -> ResolvedInput:
        """Resolve one input reference to a local file.

        Raises a precondition error that names what was checked when the file
        cannot be reached, rather than a bare "not found".
        """
        chosen = implied_scope(scope, path, asset_id, source_id)
        if chosen == "asset":
            asset, target = self._asset_entry(str(asset_id or ""))
            return ResolvedInput(
                scope="asset",
                reference={"scope": "asset", "asset_id": asset["id"]},
                local_path=target,
                name=str(asset.get("name") or target.name),
                size=int(asset.get("size") or target.stat().st_size),
                media_type=asset.get("media_type") or mimetypes.guess_type(str(asset.get("name") or ""))[0],
                asset=asset,
                provenance={"kind": "asset", "asset_id": asset["id"]},
            )
        if chosen == "source":
            grant, relative = self._source_grant(source_id, path)
            staged = self._stage_source(grant, relative)
            staged_path = str(staged.get("staged_path") or "")
            target = self.workspaces.workspace_path(self.store, staged_path, require_exists=True)
            return ResolvedInput(
                scope="source",
                reference={"scope": "source", "source_id": grant["id"], "path": str(staged.get("relative_path") or relative)},
                local_path=target,
                name=str(staged.get("name") or Path(staged_path).name),
                size=int(staged.get("size_bytes") or target.stat().st_size),
                media_type=mimetypes.guess_type(str(staged.get("name") or staged_path))[0],
                provenance={
                    "kind": "source",
                    "source_id": grant["id"],
                    "host_path": grant["host_path"],
                    "relative_path": staged.get("relative_path"),
                    "staged_path": staged_path,
                    "content_id": staged.get("content_id"),
                    "read_only": True,
                },
            )
        try:
            target = self.workspaces.workspace_path(self.store, path, require_exists=True)
        except AssetError as exc:
            if "does not exist" not in str(exc):
                raise
            raise ToolPreconditionError(
                "Workspace path does not exist.",
                code="workspace_path_not_found", reason="not_found",
                requested_path=path,
                checked_scope={"scope": "workspace", "path": path},
                missing=[{"kind": "workspace_file", "path": path}],
            ) from exc
        if not target.is_file():
            raise ToolPreconditionError(
                "Workspace path is not a file.",
                code="workspace_path_not_found", reason="not_found",
                requested_path=path,
                checked_scope={"scope": "workspace", "path": path},
                missing=[{"kind": "workspace_file", "path": path}],
            )
        relative = target.relative_to(self.workspaces.workspace(self.store)).as_posix()
        stat = target.stat()
        return ResolvedInput(
            scope="workspace",
            reference={"scope": "workspace", "path": relative},
            local_path=target,
            name=target.name,
            size=stat.st_size,
            media_type=mimetypes.guess_type(target.name)[0],
            provenance={
                "kind": "workspace",
                "path": relative,
                "staged": relative.startswith(STAGED_PREFIX),
            },
        )


__all__ = [
    "InputResolution",
    "ResolvedInput",
    "SCOPES",
    "STAGED_PREFIX",
    "implied_scope",
]
