from __future__ import annotations

import io
import json
import uuid

from PIL import Image

from ...storage import AssetError


def _raster_preview(target, max_size: int):
    """Render a bounded RGB preview of a GeoTIFF with the raster reader.

    Pillow cannot open every GeoTIFF a raster writer produces -- a minimal
    three-band file with no photometric tag is rejected as unidentified -- so a
    preview that only tried Pillow reported "binary file" for a valid image. The
    raster reader knows the grid, and the same 2nd/98th-percentile stretch the
    domain preview uses keeps the result comparable with it.
    """
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(target, driver="GTiff") as src:
        factor = min(1.0, float(max_size) / max(src.width, src.height))
        height = max(1, round(src.height * factor))
        width = max(1, round(src.width * factor))
        bands = list(range(1, min(src.count, 3) + 1)) or [1]
        data = src.read(
            bands, out_shape=(len(bands), height, width),
            masked=True, resampling=Resampling.average,
        )
        channels = []
        for band in data:
            valid = band.compressed()
            valid = valid[np.isfinite(valid)]
            low, high = (
                np.percentile(valid, [2, 98]) if valid.size else (0.0, 1.0)
            )
            pixels = np.asarray(band.filled(float(low)), dtype=float)
            pixels = np.nan_to_num(
                (pixels - low) / max(float(high - low), 1e-9)
            )
            channels.append((np.clip(pixels, 0, 1) * 255).astype("uint8"))
        stacked = np.stack(channels if len(channels) == 3 else channels * 3)
        return Image.fromarray(np.moveaxis(stacked, 0, -1)), bands


class ArtifactCapability:
    """Bounded artifact inspection and preview generation.

    Inspection and preview resolve their input through the shared reference rule,
    so a workspace file, an attachment and a file under an authorized directory are
    all acceptable here with the same arguments the tool that produced them returned.
    Both results carry ``exact_reference`` back, which is what makes the next call a
    copy rather than a guess.
    """

    def _artifact(self, scope, path, asset_id, source_id=None):
        return self.resolve_input(
            scope=scope or "auto", path=path or "", asset_id=asset_id, source_id=source_id,
        )

    def artifacts_inspect(self, scope="auto", path="", asset_id=None, source_id=None):
        resolved = self._artifact(scope, path, asset_id, source_id)
        target = resolved.local_path
        result = resolved.metadata()
        result.update({"suffix": target.suffix.lower(), "modified": target.stat().st_mtime})
        try:
            with Image.open(target) as image:
                result["image"] = {
                    "format": image.format, "width": image.width, "height": image.height,
                    "mode": image.mode, "frames": getattr(image, "n_frames", 1),
                }
        except Exception:
            result["text_probe"] = self._read_local(target, 1, 20, 4000)
        return result

    def artifacts_preview(self, scope="auto", path="", asset_id=None, source_id=None, max_size=1024):
        resolved = self._artifact(scope, path, asset_id, source_id)
        target = resolved.local_path
        image = None
        bands_used = None
        method = "pillow"
        try:
            with Image.open(target) as source:
                source.seek(0)
                source.thumbnail((max_size, max_size))
                if source.mode not in ("RGB", "RGBA"):
                    source = source.convert("RGB")
                image = source.copy()
        except Exception:
            try:
                image, bands_used = _raster_preview(target, max_size)
                method = "raster-stretch"
            except Exception:
                image = None
        if image is None:
            return self._read_local(target, 1, 80, 12000) | {
                "artifact": resolved.metadata(),
                "exact_reference": resolved.reference,
            }
        output = io.BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        asset = self.store.put(
            output, target.stem + "_preview.png", self.owner, "image/png",
            resolved.source_asset_id(),
            metadata={
                "derived_from": dict(resolved.reference),
                "derived_from_name": resolved.name,
                "preview_of": dict(resolved.reference),
                "preview_method": method,
                "bands_used": bands_used,
                "note": "Preview is rescaled and stretched, not the quantitative data.",
            },
        )
        self.created.append(asset)
        self.allowed.add(asset["id"])
        return {
            "artifact": asset,
            "preview_asset": asset,
            "source": resolved.metadata(),
            "preview_method": method,
            "bands_used": bands_used,
            "exact_reference": {"scope": "asset", "asset_id": asset["id"]},
            "source_reference": dict(resolved.reference),
        }

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
