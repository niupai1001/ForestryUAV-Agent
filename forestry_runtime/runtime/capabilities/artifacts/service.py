from __future__ import annotations

import io
import json
import mimetypes
import uuid

from PIL import Image

from ...kernel.protocol import ToolPreconditionError
from ...storage import AssetError


class ArtifactCapability:
    """Bounded artifact inspection and preview generation."""

    def _artifact(self, scope, path, asset_id):
        if scope == "asset":
            if not asset_id or asset_id not in self.allowed:
                raise ToolPreconditionError(
                    "An attached or generated asset_id is required.",
                    code="asset_not_available", reason="inapplicable",
                    missing=[{"kind": "asset_id", "value": asset_id}],
                    checked_scope={"scope": "chat_assets"},
                    candidates=[{"asset_id": item["id"], "name": item["name"]}
                                for item in self.attachment_context()[:10]],
                )
            return self.store.path(asset_id, self.owner), self.store.get(asset_id, self.owner)
        target = self.workspaces.workspace_path(self.store, path, require_exists=True)
        if not target.is_file():
            raise AssetError("Artifact path is not a file")
        return target, {"path": target.relative_to(self.workspace).as_posix(), "name": target.name, "size": target.stat().st_size, "media_type": mimetypes.guess_type(target.name)[0]}

    def artifacts_inspect(self, scope="workspace", path="", asset_id=None):
        target, metadata = self._artifact(scope, path, asset_id)
        result = dict(metadata)
        result.update({"suffix": target.suffix.lower(), "modified": target.stat().st_mtime})
        try:
            with Image.open(target) as image:
                result["image"] = {"format": image.format, "width": image.width, "height": image.height, "mode": image.mode, "frames": getattr(image, "n_frames", 1)}
        except Exception:
            result["text_probe"] = self._read_local(target, 1, 20, 4000)
        return result

    def artifacts_preview(self, scope="workspace", path="", asset_id=None, max_size=1024):
        target, metadata = self._artifact(scope, path, asset_id)
        try:
            with Image.open(target) as image:
                image.seek(0)
                image.thumbnail((max_size, max_size))
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, format="PNG")
                output.seek(0)
            asset = self.store.put(output, target.stem + "_preview.png", self.owner, "image/png", metadata.get("id"))
            self.created.append(asset)
            self.allowed.add(asset["id"])
            return {"preview_asset": asset}
        except Exception:
            return self._read_local(target, 1, 80, 12000) | {"artifact": metadata}
    def store_tool_result(self, value: dict) -> str:
        result_id = "result_" + uuid.uuid4().hex
        directory = self.workspace / ".runtime" / "tool-results"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{result_id}.json").write_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        return result_id

    def tool_result_read(self, result_id, offset=0, max_chars=12000):
        target = self.workspace / ".runtime" / "tool-results" / f"{result_id}.json"
        if not target.is_file():
            raise AssetError("Complete tool result does not exist in this Workspace")
        content = target.read_text(encoding="utf-8")
        chunk = content[offset:offset + max_chars]
        next_offset = offset + len(chunk)
        return {
            "result_id": result_id, "offset": offset, "content": chunk,
            "next_offset": next_offset, "has_more": next_offset < len(content),
            "total_chars": len(content),
        }
