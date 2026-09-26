"""Build source-disjoint development questions from the official TRAINING split.

The formal pilot runs on twelve sources drawn from the test shard. Debugging the
grader and the 900 s budget against those same sources would tune the rules to
the very samples they are later scored on, so development questions come from a
different split and from sources the formal bank never touches.

Rules inherited from the frozen generator, so a dev question behaves like a
formal one:

* only CC-BY 4.0 sources;
* sources ordered by the same seed (``forestry-grounded-v1``);
* one candidate per source: the minimum-ranked tile;
* a source whose candidate has no tree-canopy pixel is skipped, never replaced;
* the valid domain is ``rgb-not-all-zero`` for extraction and ``given-mask-tile``
  for statistics.

Usage:
    python evaluation/grounded_v1/prepare_dev.py [--limit 6]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from PIL import Image
from rasterio.io import MemoryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.grounded_v1.oam import (  # noqa: E402
    SEED, _order, _row_bytes, _write_mask, rank_sources,
)

ROOT = Path(__file__).resolve().parents[2]
TRAIN = ROOT / "data/oam_tcd/train-00000-of-00001.parquet"
TRAIN_SHA256 = "ec8fe0c79ed8286264625351c307331646c04717df186a25628fe96815bf2acb"
FORMAL_MANIFEST = ROOT / "data/oam_tcd/grounded_v1_1_private/manifest.json"
PUBLIC = ROOT / "data/oam_tcd/dev_public"
PRIVATE = ROOT / "data/oam_tcd/dev_private"

# Families mirror the formal bank: a question is an extraction task when its
# 1-based position is <= 6, otherwise a statistics task.
CANOPY_POSITIONS = {1, 2, 3}
SPATIAL_POSITIONS = {7, 8, 9}


def check_shard(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != TRAIN_SHA256:
        raise ValueError(f"training shard digest {digest.hexdigest()} != pinned {TRAIN_SHA256}")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()

    digest = check_shard(TRAIN)
    formal = json.loads(FORMAL_MANIFEST.read_text(encoding="utf-8"))
    formal_sources = {task["oam_id"] for task in formal["tasks"]}
    print(f"training shard ok: {TRAIN_SHA256[:12]}…  formal sources to avoid: {len(formal_sources)}")

    reader = pq.ParquetFile(TRAIN)
    metadata: list[dict] = []
    locations: dict[int, tuple[int, int]] = {}
    for group in range(reader.metadata.num_row_groups):
        table = reader.read_row_group(group, columns=["image_id", "oam_id", "license", "biome", "crs"])
        for offset, row in enumerate(table.to_pylist()):
            identifier = int(row["image_id"])
            locations[identifier] = (group, offset)
            if row["license"] == "CC-BY 4.0" and row["oam_id"] not in formal_sources:
                metadata.append(row)
    print(f"candidate rows outside the formal sources: {len(metadata)}")

    def candidate_of(source: str) -> dict:
        rows = [row for row in metadata if row["oam_id"] == source]
        return min(rows, key=lambda item: _order(str(item["image_id"])))

    def has_foreground(identifier: int) -> bool:
        group, offset = locations[identifier]
        payload = reader.read_row_group(group, columns=["annotation"]).slice(offset, 1).to_pylist()[0]["annotation"]
        array = np.asarray(Image.open(io.BytesIO(_row_bytes(payload, column="annotation", identifier=identifier))).convert("RGB"))
        return bool(np.any(array != 0))

    accepted: list[dict] = []
    rejected: list[dict] = []
    wanted = CANOPY_POSITIONS | SPATIAL_POSITIONS
    for source in rank_sources(metadata):
        if len(accepted) >= max(wanted):
            break
        row = candidate_of(source)
        identifier = int(row["image_id"])
        position = len(accepted) + 1
        if position not in wanted:
            # Keep the ranking faithful: the source is still consumed, it just
            # does not produce a dev question.
            accepted.append({**row, "kind": "canopy" if position <= 6 else "spatial",
                             "task_id": f"dev-{position:02d}", "skipped": True})
            continue
        if not has_foreground(identifier):
            rejected.append({"image_id": identifier, "oam_id": source,
                             "reason": "annotation has no tree-canopy pixel"})
            continue
        accepted.append({**row, "kind": "canopy" if position <= 6 else "spatial",
                         "task_id": f"dev-{position:02d}", "skipped": False})

    selected = [item for item in accepted if not item.get("skipped")]
    print(f"selected {len(selected)} dev questions; rejected candidates: {len(rejected)}")

    overlap = {item["oam_id"] for item in selected} & formal_sources
    if overlap:
        raise ValueError(f"dev sources overlap the formal bank: {overlap}")
    print("source disjointness: OK (no overlap with the 12 formal sources)")

    if PUBLIC.exists() and any(PUBLIC.iterdir()) or PRIVATE.exists() and any(PRIVATE.iterdir()):
        raise FileExistsError("dev roots must be empty; never overwrite a frozen suite")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)

    tasks = []
    for position, selection in enumerate(selected, start=1):
        task_id = f"dev-{position:02d}"
        identifier = int(selection["image_id"])
        group, offset = locations[identifier]
        row = reader.read_row_group(group, columns=["image", "annotation"]).slice(offset, 1).to_pylist()[0]
        image_bytes = _row_bytes(row["image"], column="image", identifier=identifier)
        annotation = np.asarray(Image.open(io.BytesIO(_row_bytes(row["annotation"], column="annotation", identifier=identifier))).convert("RGB"))
        with MemoryFile(image_bytes) as image_file, image_file.open() as source:
            if source.count != 3 or annotation.shape[:2] != (source.height, source.width) or source.crs is None:
                raise ValueError(f"Invalid RGB/mask pair for {task_id}")
            profile = source.profile
            stack = source.read()
            transform = [float(value) for value in source.transform[:6]]
            crs = str(source.crs)
            shape = [int(source.height), int(source.width)]
            valid = np.any(stack != 0, axis=0)
            canopy = np.any(annotation != 0, axis=2)
            kind = "canopy" if position <= 3 else "spatial"
            if kind == "canopy":
                (PUBLIC / f"{task_id}.tif").write_bytes(image_bytes)
                _write_mask(PRIVATE / f"{task_id}-gold.tif", canopy, profile)
                prompt = f"从 {task_id}.tif 提取树冠覆盖，交付与输入严格对齐的单波段二值 GeoTIFF（0/1）及覆盖率百分数。"
                domain = valid
                valid_definition = "rgb-not-all-zero"
            else:
                _write_mask(PUBLIC / f"{task_id}-input.tif", canopy, profile)
                _write_mask(PRIVATE / f"{task_id}-gold.tif", canopy, profile)
                prompt = f"统计 {task_id}-input.tif 的树冠覆盖率百分数，以及树冠像元数和总有效像元数，写成 JSON。"
                domain = np.ones_like(valid)
                valid_definition = "given-mask-tile"
        (PUBLIC / f"{task_id}-prompt.txt").write_text(prompt, encoding="utf-8")
        canopy_domain = int((canopy & domain).sum())
        valid_pixels = int(domain.sum())
        tasks.append({
            "task_id": task_id, "kind": kind, "image_id": identifier,
            "oam_id": selection["oam_id"], "biome": selection["biome"], "source_crs": selection["crs"],
            "public_input": f"{task_id}.tif" if kind == "canopy" else f"{task_id}-input.tif",
            "public_prompt": f"{task_id}-prompt.txt", "gold_mask": f"{task_id}-gold.tif",
            "grid": {"crs": crs, "shape": shape, "transform": transform},
            "valid_definition": valid_definition,
            "valid_pixels": valid_pixels, "total_pixels": int(valid.size),
            "all_zero_image_pixels": int((~valid).sum()) if kind == "canopy" else 0,
            "gold_foreground_pixels": int(canopy.sum()),
            "gold_canopy_pixels": canopy_domain,
            "gold_coverage_percent": round(100.0 * canopy_domain / valid_pixels, 6),
            "gold_coverage_percent_full_tile": round(100.0 * float(canopy.sum()) / float(canopy.size), 6),
        })

    manifest = {
        "purpose": "development only -- never counted in the formal denominator",
        "split": "train", "dataset": "restor/tcd", "seed": SEED,
        "train_shard_sha256": digest,
        "disjoint_from": "data/oam_tcd/grounded_v1_1_private/manifest.json",
        "selection_rule": {
            "license": "CC-BY 4.0 only",
            "source_order": "sha256(seed:oam_id), same seed as the formal bank",
            "candidate_tile_per_source": "the minimum-ranked tile of the source",
            "usability_gate": "skip the source when that candidate has no canopy pixel",
            "excluded_sources": sorted(formal_sources),
        },
        "rejected_candidates": rejected,
        "tasks": tasks,
    }
    (PRIVATE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {PUBLIC.relative_to(ROOT)} ({len(list(PUBLIC.iterdir()))} files), "
          f"{PRIVATE.relative_to(ROOT)} ({len(list(PRIVATE.iterdir()))} files)")
    for task in tasks:
        print(f"  {task['task_id']} {task['kind']:8} image_id={task['image_id']:5} "
              f"valid={task['valid_pixels']:8} canopy={task['gold_canopy_pixels']:8} "
              f"cover={task['gold_coverage_percent']:.6f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
