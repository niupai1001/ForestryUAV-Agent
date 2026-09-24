"""Freeze source-disjoint OAM-TCD trials from the official CC-BY test shard.

Requires pyarrow, numpy, Pillow and rasterio. Output roots must be distinct:
``public`` is copied into the agent workspace, while ``private`` stays with
the evaluator. Neither root belongs in a knowledge index.

Two frozen versions exist:

``grounded-v1``
    The first selection: the first twelve sources in the pinned seed order, one
    minimum-ranked tile each, with no usability gate. Its samples were reviewed
    by hand against the pinned shard (``audit_sources.py``) and one was rejected
    -- see below.

``grounded-v1.1``
    The version the pilot runs. v1's ``oam-09`` source has **no canopy pixel at
    all** in its selected tile, so it cannot carry an IoU or a coverage
    statistic: the extraction task would score a constant and the spatial task
    would be answered correctly by writing ``0``. Rather than leave a vacuous
    task in the frozen bank, the source is rejected and the next-ranked source is
    admitted. That substitution is a *pre-pilot* question-bank revision, made
    before any agent ran any task, and it is applied by a declared,
    deterministic rule rather than by hand: a source whose minimum-ranked tile
    has empty tree-canopy foreground is skipped. No agent result existed when
    this rule was frozen.

The usability gate is not self-evident from a manifest. ``audit_shard.py``
measures the whole pinned shard once and writes ``oam_shard_audit.json``; the
gate consumes that evidence (``--audit``) so the expensive decode happens once
and the acceptance decision can be replayed from a stored artifact. Without
``--audit`` the annotations are decoded here, which is correct but slow.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio
from PIL import Image
from rasterio.io import MemoryFile

DATASET_REVISION = "d97d4da0ebbb6e249ae95ac5e19656babd972eb2"
TEST_SHA256 = "5c10ac4cb8afaf18dae5aad5b22cc86bb80977116a8bcd8c16d41ae48b9b13cf"
SEED = "forestry-grounded-v1"

#: Question-bank versions and the usability rule each was frozen under.
VERSIONS: dict[str, dict] = {
    "grounded-v1": {"require_nonempty_foreground": False, "source_count": 12},
    "grounded-v1.1": {"require_nonempty_foreground": True, "source_count": 12},
}
DEFAULT_VERSION = "grounded-v1.1"


def _order(value: str) -> str:
    return hashlib.sha256(f"{SEED}:{value}".encode()).hexdigest()


def _check_shard(path: Path) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != TEST_SHA256:
        raise ValueError("OAM-TCD test shard does not match the pinned revision")


def _row_bytes(value: object, *, column: str, identifier: int) -> bytes:
    """Pull the payload out of an ``image``/``annotation`` struct column.

    The shard stores these as ``struct<bytes: binary, path: string>``. A row whose
    struct is null, or whose ``bytes`` field is null, is a data defect and must be
    reported as one rather than surfacing as a bare ``KeyError``.
    """
    if isinstance(value, dict):
        payload = value.get("bytes")
    else:
        payload = value
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise ValueError(f"{column} payload is missing for image_id {identifier}")
    return bytes(payload)


def rank_sources(metadata: list[dict]) -> list[str]:
    """CC-BY sources in the frozen seed order. Ordering is the sampling rule."""
    by_source: dict[str, list[dict]] = {}
    for row in metadata:
        if row["license"] != "CC-BY 4.0":
            continue
        by_source.setdefault(row["oam_id"], []).append(row)
    return sorted(by_source, key=_order)


def _minimum_ranked_tile(by_source: dict[str, list[dict]], source: str) -> dict:
    return min(by_source[source], key=lambda item: _order(str(item["image_id"])))


def select_rows(
    metadata: list[dict], *, version: str = DEFAULT_VERSION,
    foreground_of: dict[int, bool] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pick one tile per source, with separate sources for each task family.

    Returns ``(accepted, rejected)``. ``rejected`` records every candidate the
    declared usability rule refused, with the reason, so the substitution in
    ``grounded-v1.1`` is auditable evidence rather than a silent edit.

    ``foreground_of`` maps ``image_id`` to "the annotation holds at least one
    tree-canopy pixel". Supplying it keeps the selection pure and unit-testable;
    when it is omitted the gate cannot be applied.
    """
    if version not in VERSIONS:
        raise ValueError(f"Unknown question-bank version: {version}")
    rules = VERSIONS[version]
    by_source: dict[str, list[dict]] = {}
    for row in metadata:
        if row["license"] == "CC-BY 4.0":
            by_source.setdefault(row["oam_id"], []).append(row)

    accepted: list[dict] = []
    rejected: list[dict] = []
    for source in rank_sources(metadata):
        if len(accepted) >= rules["source_count"]:
            break
        row = _minimum_ranked_tile(by_source, source)
        identifier = int(row["image_id"])
        if rules["require_nonempty_foreground"]:
            observed = None if foreground_of is None else foreground_of.get(identifier)
            if observed is None:
                raise ValueError(
                    f"No foreground measurement for image_id {identifier}; the "
                    "usability gate cannot be applied from the declared evidence"
                )
            if not observed:
                rejected.append({
                    "image_id": identifier, "oam_id": source,
                    "reason": "annotation has no tree-canopy pixel, so the sample "
                              "cannot carry an IoU or a coverage statistic",
                })
                continue
        number = len(accepted) + 1
        accepted.append({
            **row, "kind": "canopy" if number <= 6 else "spatial",
            "task_id": f"oam-{number:02d}",
        })
    if len(accepted) < rules["source_count"]:
        raise ValueError(
            f"Need {rules['source_count']} usable independent CC-BY source images, "
            f"found {len(accepted)}"
        )
    return accepted, rejected


def _all_rows(shard: Path) -> tuple[list[dict], dict[int, tuple[int, int]]]:
    reader = pq.ParquetFile(shard)
    metadata = []
    locations = {}
    for group in range(reader.metadata.num_row_groups):
        table = reader.read_row_group(group, columns=["image_id", "oam_id", "license", "biome", "crs"])
        for offset, row in enumerate(table.to_pylist()):
            identifier = int(row["image_id"])
            if identifier in locations:
                raise ValueError(f"Duplicate image_id: {identifier}")
            metadata.append(row)
            locations[identifier] = (group, offset)
    return metadata, locations


def _write_mask(path: Path, array: np.ndarray, profile: dict) -> None:
    settings = profile.copy()
    settings.pop("photometric", None)
    settings.update(driver="GTiff", count=1, dtype="uint8", nodata=None, compress="deflate")
    with rasterio.open(path, "w", **settings) as destination:
        destination.write(array.astype("uint8"), 1)


def foreground_from_audit(audit_path: Path) -> dict[int, bool]:
    """Reuse ``audit_shard.py`` evidence as the gate's input."""
    report = json.loads(audit_path.read_text(encoding="utf-8"))
    if report.get("shard_sha256") != TEST_SHA256:
        raise ValueError("The audit was produced from a different shard revision")
    return {
        int(record["image_id"]): int(record["canopy_pixels"]) > 0
        for record in report["per_row"]
    }


def _foreground_flags(
    reader: pq.ParquetFile, metadata: list[dict], locations: dict[int, tuple[int, int]]
) -> dict[int, bool]:
    """Decode the annotations to learn which tiles hold any tree canopy."""
    flags: dict[int, bool] = {}
    for row in metadata:
        identifier = int(row["image_id"])
        group, offset = locations[identifier]
        payload = reader.read_row_group(group, columns=["annotation"]).slice(offset, 1).to_pylist()[0]["annotation"]
        array = np.asarray(Image.open(io.BytesIO(_row_bytes(payload, column="annotation", identifier=identifier))).convert("RGB"))
        flags[identifier] = bool(np.any(array != 0))
    return flags


def prepare(
    shard: Path, public: Path, private: Path, *,
    version: str = DEFAULT_VERSION, audit: Path | None = None,
) -> dict:
    if public.resolve() == private.resolve() or public.resolve() in private.resolve().parents or private.resolve() in public.resolve().parents:
        raise ValueError("Public and private roots must be disjoint")
    if version not in VERSIONS:
        raise ValueError(f"Unknown question-bank version: {version}")
    _check_shard(shard)
    metadata, locations = _all_rows(shard)
    reader = pq.ParquetFile(shard)
    rules = VERSIONS[version]

    flags: dict[int, bool] | None = None
    if rules["require_nonempty_foreground"]:
        flags = (
            foreground_from_audit(audit) if audit is not None
            else _foreground_flags(reader, metadata, locations)
        )
    selected, rejected = select_rows(metadata, version=version, foreground_of=flags)

    if public.exists() and any(public.iterdir()) or private.exists() and any(private.iterdir()):
        raise FileExistsError("Evaluation roots must be empty; never overwrite a frozen suite")
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)

    tasks = []
    for selection in selected:
        task_id = selection["task_id"]
        identifier = int(selection["image_id"])
        group, offset = locations[identifier]
        row = reader.read_row_group(group, columns=["image", "annotation"]).slice(offset, 1).to_pylist()[0]
        image_bytes = _row_bytes(row["image"], column="image", identifier=identifier)
        annotation = np.asarray(Image.open(io.BytesIO(_row_bytes(row["annotation"], column="annotation", identifier=identifier))).convert("RGB"))
        with MemoryFile(image_bytes) as image_file, image_file.open() as source:
            if source.count != 3 or annotation.shape[:2] != (source.height, source.width) or source.crs is None:
                raise ValueError(f"Invalid RGB/mask pair for {task_id}")
            if source.crs.is_geographic:
                raise ValueError(f"Geographic pixel area requires a different rule for {task_id}")
            profile = source.profile
            stack = source.read()
            transform = [float(value) for value in source.transform[:6]]
            crs = str(source.crs)
            shape = [int(source.height), int(source.width)]
            # Valid domain: a pixel is valid when the RGB observation is not entirely
            # zero. Nine of the twelve v1 samples contain such gaps; treating them as
            # ground would silently dilute every coverage figure.
            valid = np.any(stack != 0, axis=0)
            if not valid.any():
                raise ValueError(f"{task_id} has no valid observation pixels")
            canopy = np.any(annotation != 0, axis=2)
            if not canopy.any():
                raise ValueError(f"{task_id} has empty canopy foreground under {version}")
            if selection["kind"] == "canopy":
                (public / f"{task_id}.tif").write_bytes(image_bytes)
                _write_mask(private / f"{task_id}-gold.tif", canopy, profile)
                prompt = f"从 {task_id}.tif 提取树冠覆盖，交付与输入严格对齐的单波段二值 GeoTIFF（0/1）及覆盖率百分数。"
            else:
                _write_mask(public / f"{task_id}-input.tif", canopy, profile)
                # The provided mask is also the frozen truth for this family: it is
                # the only observable domain, so the grader reads the same bytes the
                # agent was given. No second truth source exists for these tasks.
                _write_mask(private / f"{task_id}-gold.tif", canopy, profile)
                prompt = f"统计 {task_id}-input.tif 的树冠覆盖率百分数，以及树冠像元数和总有效像元数，写成 JSON。"
        (public / f"{task_id}-prompt.txt").write_text(prompt, encoding="utf-8")
        # The valid domain differs by family, and must be one the agent can see:
        #
        # * extraction -- the valid domain is "RGB not all zero" in the *input*
        #   image the agent was given. It can derive that domain itself, so the
        #   IoU denominator is knowable and the coverage figure is checkable.
        # * statistics -- the agent is given the canopy mask and nothing else, so
        #   the only denominator it can see is the mask itself. Asking it to
        #   exclude the original no-observation pixels would demand a domain it
        #   cannot observe, and a competent answer would be failed for a
        #   definitional reason. The full mask tile is therefore the domain.
        if selection["kind"] == "canopy":
            domain = valid
            valid_definition = "rgb-not-all-zero"
        else:
            domain = np.ones_like(valid)
            valid_definition = "given-mask-tile"
        canopy_domain = int((canopy & domain).sum())
        valid_pixels = int(domain.sum())
        tasks.append({
            "task_id": task_id, "kind": selection["kind"], "image_id": identifier,
            "oam_id": selection["oam_id"], "biome": selection["biome"], "source_crs": selection["crs"],
            "public_input": f"{task_id}.tif" if selection["kind"] == "canopy" else f"{task_id}-input.tif",
            "public_prompt": f"{task_id}-prompt.txt",
            "gold_mask": f"{task_id}-gold.tif",
            "grid": {"crs": crs, "shape": shape, "transform": transform},
            "valid_definition": valid_definition,
            "valid_pixels": valid_pixels,
            "total_pixels": int(valid.size),
            "all_zero_image_pixels": int((~valid).sum()) if selection["kind"] == "canopy" else 0,
            "gold_foreground_pixels": int(canopy.sum()),
            "gold_canopy_pixels": canopy_domain,
            "gold_coverage_percent": round(100.0 * canopy_domain / valid_pixels, 6),
            "gold_coverage_percent_full_tile": round(100.0 * float(canopy.sum()) / float(canopy.size), 6),
        })
    version_slug = version.replace("grounded-", "").replace(".", "_")
    manifest = {
        "version": version, "version_slug": version_slug, "dataset": "restor/tcd",
        "revision": DATASET_REVISION, "test_shard_sha256": TEST_SHA256, "seed": SEED,
        "selection_rule": {
            "require_nonempty_foreground": bool(rules["require_nonempty_foreground"]),
            "source_count": rules["source_count"],
            "candidate_tile_per_source": "the minimum-ranked tile of the source",
            "evidence": str(audit) if audit is not None else "annotations decoded during selection",
            "note": "One candidate per source. A source is skipped when that candidate is "
                    "rejected; it is never replaced by another tile of the same source, so "
                    "the sampling rule stays a function of the source ranking alone.",
        },
        "rejected_candidates": rejected,
        "valid_domain_definition": {
            "rule": "rgb-not-all-zero",
            "statement": "A pixel is valid when at least one of the three RGB bands is non-zero. "
                         "Pixels that are zero in all bands carry no observation and are excluded "
                         "from the coverage denominator and from the IoU domain. The full-tile "
                         "coverage is reported alongside it so the two are never confused.",
        },
        "tasks": tasks,
    }
    (private / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--version", default=DEFAULT_VERSION, choices=sorted(VERSIONS))
    parser.add_argument("--audit", type=Path, default=None,
                        help="oam_shard_audit.json from audit_shard.py; required for the "
                             "foreground gate unless the annotations are decoded here")
    args = parser.parse_args()
    result = prepare(args.shard, args.public, args.private, version=args.version, audit=args.audit)
    print(json.dumps({
        "version": result["version"],
        "tasks": len(result["tasks"]),
        "sources": len({task["oam_id"] for task in result["tasks"]}),
        "rejected": [candidate["image_id"] for candidate in result["rejected_candidates"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
