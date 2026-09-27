"""Deliverable descriptions: real URLs, and the checks that are separate from them.

The failure this replaces had four independent parts, and only one of them was the
missing image: the preview tool registered a PNG but returned only asset metadata; the
conversation pane rendered model text and never an artifact; the model wrote
``https://workspace/...``, an address that does not exist; and the real route served
``application/octet-stream`` with an attachment disposition, which is a download, not
a picture.

So a deliverable is described once, by the Runtime:

* ``preview_url``/``download_url`` are the addresses the server actually serves, built
  from the chat and asset ids rather than assembled by the model;
* four checks that were previously one word -- "done" -- are reported separately:
  a process that ran, a file that exists, a server that can read it back, and a browser
  that can display it;
* whether the output *answers the task* is deliberately reported as ``unverified``.
  The Runtime can prove a GeoTIFF's bands, grid, valid area and statistics; it cannot
  prove that a mask means "canopy", and saying otherwise is the failure this whole
  round of work exists to remove.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

#: Media types a browser will render inline. Everything else is delivered as a file.
INLINE_MEDIA_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif", "image/svg+xml",
}
GEO_SUFFIXES = {".tif", ".tiff", ".vrt"}
#: A decimated read is enough to report bands, grid and statistics, and it keeps a
#: 4,000 x 4,000 product from being read in full just to describe it.
QA_SAMPLE_EDGE = 512


def asset_urls(chat_id: str, asset_id: str, *, inline: bool = False) -> dict:
    """The addresses the server serves for one asset.

    One route, two intents: ``inline=1`` asks for a renderable response, the default
    is a download. Both are real -- the route exists and honours the parameter -- which
    is what makes "the link opens" a checkable claim instead of a hopeful one.
    """
    base = f"/files/{chat_id}/{asset_id}"
    return {
        "inline_url": f"{base}?inline=1" if inline else None,
        "download_url": f"{base}?download=1",
    }


def _media_type(asset: dict) -> str | None:
    declared = asset.get("media_type")
    if isinstance(declared, str) and declared:
        return declared
    guessed = mimetypes.guess_type(str(asset.get("name") or ""))[0]
    return guessed


def product_qa(path: Path) -> dict | None:
    """Bands, grid, valid area and statistics for a GeoTIFF, or ``None``.

    Best-effort and bounded: the point is to make a product's own metadata and numbers
    observable next to the file, not to re-derive the analysis. Every number is
    measured from the file; a field that cannot be measured is absent rather than
    guessed.
    """
    if path.suffix.lower() not in GEO_SUFFIXES:
        return None
    try:
        import numpy as np
        import rasterio
        from rasterio.enums import Resampling
    except Exception:
        return None
    try:
        with rasterio.open(path, driver="GTiff") as src:
            scale = max(1.0, max(src.width, src.height) / QA_SAMPLE_EDGE)
            height = max(1, int(src.height / scale))
            width = max(1, int(src.width / scale))
            bands = []
            for index in src.indexes:
                data = src.read(
                    index, out_shape=(height, width), masked=True,
                    resampling=Resampling.nearest,
                )
                valid = data.compressed()
                valid = valid[np.isfinite(valid)] if valid.size else valid
                bands.append({
                    "index": index,
                    "description": src.descriptions[index - 1],
                    "dtype": src.dtypes[index - 1],
                    "color_interpretation": src.colorinterp[index - 1].name,
                    "sampled_valid_pixels": int(valid.size),
                    "min": float(valid.min()) if valid.size else None,
                    "max": float(valid.max()) if valid.size else None,
                    "mean": float(valid.mean()) if valid.size else None,
                })
            sample_valid = sum(row["sampled_valid_pixels"] for row in bands[:1])
            sample_total = height * width
            qa = {
                "band_count": src.count,
                "width": src.width,
                "height": src.height,
                "dtypes": list(src.dtypes),
                "bands": bands,
                "crs": str(src.crs) if src.crs else None,
                "pixel_size": list(src.res),
                "pixel_size_units": (
                    "degrees" if src.crs and src.crs.is_geographic
                    else (src.crs.linear_units if src.crs else "unknown")
                ),
                "nodata": src.nodata,
                "transform": list(src.transform)[:6],
                "valid_fraction_sampled": (
                    sample_valid / sample_total if sample_total else None
                ),
                "validity_source": "dataset mask on a decimated read",
                "sampled": {"width": width, "height": height},
                "note": (
                    "Valid-area and statistics are measured on a decimated read of this "
                    "file, not on the full raster. They describe the product's own grid "
                    "and values; they do not establish what the values mean."
                ),
            }
            return qa
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def describe_artifact(
    asset: dict, chat_id: str, *, path: Path | None = None, product: bool = True,
) -> dict:
    """One deliverable: how to open it, and what has actually been checked.

    ``checks`` answers four separate questions, because collapsing them is what let a
    successful threshold run be reported as a finished canopy product.
    """
    identifier = str(asset.get("id") or "")
    name = str(asset.get("name") or "")
    media_type = _media_type(asset)
    viewable = bool(media_type in INLINE_MEDIA_TYPES)
    target = Path(path) if path is not None else None
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}

    exists = bool(target is not None and target.is_file())
    readable = False
    read_error = None
    if exists:
        try:
            with target.open("rb") as handle:
                handle.read(1)
            readable = True
        except OSError as exc:
            read_error = f"{type(exc).__name__}: {str(exc)[:200]}"

    checks = {
        # The process that produced it returned successfully. Recorded on the tool
        # result, not here, and repeated because a reader of this block should not have
        # to join two documents to learn it.
        "producer_reported_success": bool(asset.get("producer_ok", True)),
        "file_exists": exists,
        "server_can_read": readable,
        "browser_can_display": viewable,
        "answers_task": None,
    }
    if read_error:
        checks["read_error"] = read_error
    checks["answers_task_reason"] = (
        "Whether the product answers the task is a semantic judgment the Runtime does "
        "not make. A binary mask produced by a threshold is not evidence that the class "
        "it encodes means what the task asked for; that needs a task verifier or a "
        "recorded sampling review."
    )

    descriptor: dict[str, Any] = {
        "asset_id": identifier,
        "name": name,
        "media_type": media_type,
        "size_bytes": asset.get("size"),
        "sha256": asset.get("sha256"),
        "artifact_kind": asset.get("artifact_kind"),
        "checks": checks,
        "urls": asset_urls(chat_id, identifier, inline=viewable),
        "semantic_check": {
            "status": "unverified",
            "how": "task verifier or recorded sampling review",
        },
    }
    for key in ("derived_from", "preview_of", "preview_method", "bands_used"):
        if metadata.get(key) is not None:
            descriptor[key] = metadata[key]
    if product and exists and target is not None:
        qa = product_qa(target)
        if qa:
            descriptor["product_qa"] = qa
    return descriptor


def attach_deliveries(result: dict, created: list, *, chat_id: str, store, owner: str) -> dict:
    """Attach a delivery descriptor to every artifact a tool result mentions.

    One place, rather than each tool deciding how to describe its own output: the
    descriptor needs the store, the chat id and the file, and a tool that described
    only its own metadata is exactly how the preview arrived without a URL.
    """
    described: dict[str, dict] = {}

    def descriptor_for(asset_id: str) -> dict | None:
        if not asset_id:
            return None
        if asset_id in described:
            return described[asset_id]
        try:
            asset = store.get(asset_id, owner)
            path = store.path(asset_id, owner)
        except Exception:
            return None
        record = next(
            (item for item in created if item.get("id") == asset_id), None,
        )
        merged = dict(asset)
        if record:
            merged.update({k: v for k, v in record.items() if k != "delivery"})
        value = describe_artifact(merged, chat_id, path=path)
        described[asset_id] = value
        return value

    def walk(value, depth=0):
        if depth > 4:
            return value
        if isinstance(value, dict):
            identifier = value.get("id")
            if isinstance(identifier, str) and identifier.startswith("asset_"):
                delivery = descriptor_for(identifier)
                if delivery is not None:
                    value.setdefault("delivery", delivery)
            for key, item in value.items():
                value[key] = walk(item, depth + 1)
            return value
        if isinstance(value, list):
            return [walk(item, depth + 1) for item in value]
        return value

    if isinstance(result, dict):
        walk(result.get("data"))
    for item in created:
        identifier = str(item.get("id") or "")
        if not identifier:
            continue
        delivery = descriptor_for(identifier)
        if delivery is not None:
            item["delivery"] = delivery
    return result


__all__ = [
    "GEO_SUFFIXES",
    "INLINE_MEDIA_TYPES",
    "asset_urls",
    "attach_deliveries",
    "describe_artifact",
    "product_qa",
]
