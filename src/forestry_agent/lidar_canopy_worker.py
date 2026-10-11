"""Trusted staged recipe, rendered only into the existing restricted Jobs worker."""
import csv
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import laspy
import numpy as np
import rasterio

REQUEST: dict
R_SCRIPT: str


def save(name, value):
    Path(name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    if Path(path).stat().st_size > 131072:
        raise ValueError("stage record exceeds 128KiB")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def working_facts(path):
    cloud = laspy.read(path)
    if not 0 < len(cloud.points) <= 5_000_000:
        raise ValueError("whole-flight working cloud must contain 1..5 million points; no automatic thinning")
    dimensions = set(cloud.header.point_format.extra_dimension_names)
    if not {"SourcePointId", "OriginalZ", "HeightAboveGround"} <= dimensions:
        raise ValueError("normalized working cloud lacks explicit source/HAG lineage")
    ids = np.asarray(cloud.SourcePointId)
    crs = cloud.header.parse_crs()
    if crs is None or not crs.is_projected or any(a.unit_name != "metre" for a in crs.axis_info[:2]):
        raise ValueError("canopy requires projected metric XY")
    if np.unique(ids).size != len(ids):
        raise ValueError("duplicate source point mapping")
    heights = np.asarray(cloud.HeightAboveGround)
    if not np.all(np.isfinite(heights)) or np.any(heights == -9999):
        raise ValueError("working cloud includes unsupported heights")
    if not np.allclose(np.asarray(cloud.z), heights, rtol=0, atol=cloud.header.scales[2]):
        raise ValueError("working Z is not the declared HAG; do not normalize again")
    return {"points": len(ids), "crs": crs.to_string(), "max_source_id": int(ids.max()),
            "negative_hag_points": int((heights < 0).sum())}


def run(request):
    os.environ.update(PROJ_DATA="/opt/science/share/proj", PROJ_LIB="/opt/science/share/proj",
                      GDAL_DATA="/opt/science/share/gdal", PROJ_NETWORK="OFF")
    inputs = request["inputs"]
    path = lambda role: inputs[role]["path"]
    fingerprints = {role: sha(item["path"]) for role, item in inputs.items()}
    stage = request["stage"]
    record = {"schema_version": 1, "stage": stage, "method_id": request["method_id"],
              "task_id": request["task_id"], "spec_revision": request["spec_revision"],
              "science_image": request["science_image"], "parameters": request["parameters"],
              "input_sha256": fingerprints, "status": "partial", "independent_accuracy": "not_assessed",
              "scope": "whole supported flight; no user ROI or boundary tiling acceptance"}
    if stage == "chm":
        terrain = read_json(path("terrain_quality"))
        if terrain.get("normalized_working", {}).get("z_semantics") != "height_above_ground":
            raise ValueError("normalized working semantics not established")
        if terrain["normalized_working"].get("unit") != "m":
            raise ValueError("HAG unit must be declared metres")
        facts = working_facts(path("working"))
        if facts["max_source_id"] >= terrain["original_points"]:
            raise ValueError("source mapping outside original inventory")
        with rasterio.open(path("terrain_support")) as support:
            if support.crs != rasterio.crs.CRS.from_user_input(facts["crs"]) or support.count != 1:
                raise ValueError("terrain support CRS/bands mismatch")
            if support.width * support.height * 4 > 1_000_000:
                raise ValueError("0.5m CHM exceeds whole-flight grid budget; tiling required")
            grid = terrain["grid"]
            if support.shape != (grid["height"], grid["width"]) or support.res != (1, 1):
                raise ValueError("terrain support does not match recorded one-metre grid")
            if support.bounds.left != grid["origin_x"] or support.bounds.bottom != grid["origin_y"]:
                raise ValueError("terrain grid origin mismatch")
            if not set(np.unique(support.read(1))) <= {0, 1, 255}:
                raise ValueError("invalid terrain support codes")
        record.update(source_sha256=terrain["source_sha256"], working_facts=facts,
                      vertical_ref=grid["vertical_ref"], vertical_basis=grid["vertical_basis"])
    else:
        previous = read_json(path("chm_record"))
        if (previous.get("stage") != "chm" or previous["method_id"] != request["method_id"]
                or previous["science_image"] != request["science_image"]
                or previous["parameters"] != request["parameters"]
                or previous["outputs"]["chm-p2r.tif"]["sha256"] != fingerprints["chm"]):
            raise ValueError("CHM recipe/image/byte lineage mismatch")
        record.update(source_sha256=previous["source_sha256"],
                      vertical_ref=previous["vertical_ref"], vertical_basis=previous["vertical_basis"],
                      analysis=previous["result"])
        if stage == "seeds":
            request["seed_namespace"] = "tree:" + hashlib.sha256(
                (record["source_sha256"] + fingerprints["chm"] + request["method_id"]).encode()).hexdigest()[:24]
        else:
            seeds = read_json(path("seed_record"))
            if (seeds.get("stage") != "seeds" or seeds["input_sha256"]["chm"] != fingerprints["chm"]
                    or seeds["outputs"]["accepted-seeds.gpkg"]["sha256"] != fingerprints["seeds"]
                    or seeds["parameters"] != request["parameters"] or seeds["science_image"] != request["science_image"]
                    or fingerprints["working"] != previous["input_sha256"]["working"]
                    or fingerprints["original"] != previous["source_sha256"]):
                raise ValueError("instances must reuse the same original/working/CHM/accepted seed lineage")
            working_facts(path("working"))
    save("request.json", request)
    # Jobs mount granted resources by opaque ID. rlas additionally requires a
    # LAS/LAZ filename extension even after normalizePath resolves symlinks.
    # Make temporary copies in the bounded output volume, then remove
    # them after R exits. The granted original mounts remain read-only.
    aliases = {"working": ".las", "chm": ".tif", "seeds": ".gpkg", "terrain_support": ".tif"}
    r_request = json.loads(json.dumps(request))
    aliases_created = []
    for role, suffix in aliases.items():
        if role in inputs:
            alias = Path("_read_" + role + suffix)
            shutil.copyfile(inputs[role]["path"], alias)
            aliases_created.append(alias)
            r_request["inputs"][role]["path"] = str(alias.resolve())
    save("r-request.json", r_request)
    Path("lidar-canopy.R").write_text(globals()["R_SCRIPT"], encoding="utf-8")
    try:
        with Path("lidr-stage.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(["Rscript", "--vanilla", "lidar-canopy.R", "--request", "r-request.json",
                                        "--result", "r-result.json"], stdout=log, stderr=subprocess.STDOUT, check=False)
    finally:
        for alias in aliases_created:
            alias.unlink()
    if completed.returncode:
        raise RuntimeError(f"lidR {stage} exited {completed.returncode}; retain outputs and inspect lidr-stage.log")
    record["result"] = read_json("r-result.json")
    if stage == "instances":
        export_instances(request, record)
    output_names = {
        "chm": ["chm-p2r-raw.tif", "chm-p2r.tif", "canopy-support.tif"],
        "seeds": ["temporary-seeds.gpkg", "accepted-seeds.gpkg", "tree-id-mapping.csv"],
        "instances": ["crown-labels.tif", "point-labels.csv", "trees.csv", "trees.gpkg", "segmented.laz",
                      "instance-quality.json", "metrics.json", "metrics.csv", "单木处理报告.md"],
    }[stage]
    record["outputs"] = {name: {"sha256": sha(name), "size_bytes": Path(name).stat().st_size} for name in output_names}
    save("stage-result.json", record)
    print(json.dumps({"stage": stage, "status": "partial", "outputs": output_names,
                      "result": record["result"]}, ensure_ascii=False))


def export_instances(request, record):
    inputs = request["inputs"]
    original = laspy.read(inputs["original"]["path"])
    hag = laspy.read(inputs["hag"]["path"])
    working = laspy.read(inputs["working"]["path"])
    if not 0 < len(original.points) <= 5_000_000:
        raise ValueError("original inventory exceeds declared whole-flight budget")
    mapping = np.loadtxt("point-labels.csv", delimiter=",", skiprows=1, dtype=np.int64, ndmin=2)
    working_ids = np.asarray(working.SourcePointId, dtype=np.int64)
    source_ids = np.asarray(hag.SourcePointId, dtype=np.int64)
    if (len(mapping) != len(working_ids) or not np.array_equal(mapping[:, 0], working_ids)
            or np.any(working_ids < 0) or np.any(working_ids >= len(original.points))
            or np.unique(source_ids).size != len(source_ids) or np.any(source_ids < 0)
            or np.any(source_ids >= len(original.points))):
        raise ValueError("R/HAG source-point map corrupted")
    if not np.array_equal(np.asarray(working.OriginalZ), np.asarray(original.z)[working_ids]):
        raise ValueError("working OriginalZ does not exactly match original elevations")
    if any(not np.array_equal(np.asarray(working[d]), np.asarray(original[d])[working_ids]) for d in ("X", "Y")):
        raise ValueError("working horizontal source mapping changed")
    for dimension in ("X", "Y", "Z"):
        if not np.array_equal(np.asarray(hag[dimension]), np.asarray(original[dimension])[source_ids]):
            raise ValueError("HAG raw coordinates changed")
    with Path("trees.csv").open(encoding="utf-8", newline="") as stream:
        trees = list(csv.DictReader(stream))
    allowed = {int(row["treeID"]) for row in trees}
    labels = mapping[:, 1]
    if not set(np.unique(labels)) <= allowed | {0} or np.any(labels < 0) or np.any(labels > 2_147_483_647):
        raise ValueError("exported tree labels not exact positive int32 mapping or zero")
    header = laspy.LasHeader(point_format=original.header.point_format.id, version="1.4")
    header.scales, header.offsets = original.header.scales.copy(), original.header.offsets.copy()
    header.add_crs(original.header.parse_crs())
    for name, dtype in (("SourcePointId", "uint32"), ("HeightAboveGround", "float64"),
                        ("HAGValid", "uint8"), ("treeID", "uint32")):
        header.add_extra_dim(laspy.ExtraBytesParams(name=name, type=dtype))
    output = laspy.LasData(header)
    output.points = laspy.ScaleAwarePointRecord.zeros(len(original.points), header=header)
    for dimension in header.point_format.standard_dimension_names:
        if dimension not in {"classification", "user_data"}:
            output[dimension] = np.asarray(original[dimension])
    output.SourcePointId = np.arange(len(original.points), dtype=np.uint32)
    # New classification only. Publisher labels/extras are not used as predictions.
    output.classification = np.zeros(len(original.points), dtype=np.uint8)
    classification = np.asarray(output.classification).copy()
    classification[source_ids] = np.asarray(hag.classification)
    output.classification = classification
    heights = np.full(len(original.points), -9999.0)
    heights[source_ids] = np.asarray(hag.HeightAboveGround)
    if not np.array_equal(heights[working_ids], np.asarray(working.HeightAboveGround)):
        raise ValueError("original HAG artifact differs from working height lineage")
    output.HeightAboveGround = heights
    valid = np.zeros(len(original.points), dtype=np.uint8)
    valid[working_ids] = 1
    output.HAGValid = valid
    full_labels = np.zeros(len(original.points), dtype=np.uint32)
    full_labels[working_ids] = labels.astype(np.uint32)
    output.treeID = full_labels
    output.write("segmented.laz")
    reread = laspy.read("segmented.laz")
    if (len(reread.points) != len(original.points) or not np.array_equal(reread.treeID, full_labels)
            or not np.array_equal(reread.SourcePointId, np.arange(len(original.points)))
            or not np.array_equal(np.asarray(reread.z), np.asarray(original.z))
            or not np.array_equal(reread.HeightAboveGround, heights)):
        raise ValueError("final LAZ source elevations/point inventory/HAG/ID roundtrip failed")
    with rasterio.open("crown-labels.tif") as raster:
        crown_ids = raster.read(1, masked=True).compressed()
        if not set(np.unique(crown_ids)) <= allowed:
            raise ValueError("crown raster has unmapped IDs")
    analysis = record["analysis"]
    area_m2 = analysis["valid_area_m2"]
    count = record["result"]["detected_instances"]
    limitations = ["高冠层候选实例，不是完整林木清查；被遮挡/被压木可能漏检", "无独立地形/树高/实例准确性基准",
                  "垂直基准来自发布者声明", "当前整架次有效支持范围，无独立ROI/分块边界验收",
                  "树冠面积为0.5米分割栅格投影，接触缺失或边缘标记裁剪", "不提供物种/生物量或NDVI"]
    quality = {"status": "partial", "method_id": request["method_id"], "source_sha256": record["source_sha256"],
               "original_points": len(original.points), "working_points": len(working_ids),
               "assigned_points": int((full_labels > 0).sum()), "unassigned_points": int((full_labels == 0).sum()),
               "unsupported_or_noise_points": int((valid == 0).sum()), "treeID_zero": "unassigned",
               "point_assignment": request["parameters"]["point_assignment"], "valid_area_m2": area_m2,
               "detected_instances": count, "source_elevation_preserved": True, "id_roundtrip": True,
               "parameters": request["parameters"], "limitations": limitations}
    save("instance-quality.json", quality)
    support_id = "supported-chm:" + record["input_sha256"]["chm"]
    metrics = []
    for metric, value, unit in (("valid_area_ha.v1", area_m2 / 10000 if area_m2 else None, "ha"),
                                 ("detected_trees_count.v1", count if area_m2 else None, "count"),
                                 ("detected_tree_density_ha.v1", count / (area_m2 / 10000) if area_m2 else None, "count/ha")):
        metrics.append({"result_id": hashlib.sha256((support_id + metric).encode()).hexdigest(),
                        "metric_spec_ref": metric, "spatial_support_id": support_id, "estimate": value,
                        "value_ref": None, "unit": unit, "method_ref": request["method_id"],
                        "input_refs": {k: v["resource_id"] for k, v in inputs.items()}, "quality_ref": "instance-quality.json",
                        "uncertainty_kind": "not_estimated", "uncertainty_payload_ref": None,
                        "validation_status": "internal_invariants_only", "availability_status": "available" if area_m2 else "unavailable",
                        "availability_reason": None if area_m2 else "zero supported analysis area", "limitations": limitations})
    for metric, unit in (("tree_apex_height_m.v1", "m"), ("crown_area_m2.v1", "m2"), ("crown_eqdiam_m.v1", "m")):
        metrics.append({**metrics[0], "result_id": hashlib.sha256((support_id + metric).encode()).hexdigest(),
                        "metric_spec_ref": metric, "estimate": None, "value_ref": "trees.csv", "unit": unit})
    save("metrics.json", {"schema_version": 1, "results": metrics,
                          "tree_table": "trees.csv", "tree_count_filter": "accepted_instance == TRUE"})
    with Path("metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(metrics[0]))
        writer.writeheader()
        for metric in metrics:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                             if isinstance(value, (list, dict)) else value for key, value in metric.items()})
    Path("单木处理报告.md").write_text(
        f"# 无人机点云冠层实例交付\n\n方法 `{request['method_id']}`；lidR {record['result']['lidR']}。\n\n"
        f"复用原地形/HAG，执行0.5米无填洞p2r CHM、5米LMF与Dalponte；最大树冠直径10米解析为20像素。\n\n"
        f"有效分析面积 {area_m2:.2f} 平方米，接受冠层实例 {count} 个。此面积是采样与地形支持共同有效像元，不是整架次矩形。\n\n"
        f"主分割LAZ保留原始 {len(original.points)} 点及原始高程Z；含HAG、HAGValid和treeID。"
        f"已分配 {quality['assigned_points']} 点，未分配 {quality['unassigned_points']} 点以treeID=0保留。"
        "树顶/树冠在trees.gpkg，精确整数ID到文本tree_uid在trees.csv，指标见metrics.csv/JSON。\n\n"
        "**部分交付，独立现场准确性未验证。**\n\n" + "\n".join("- " + x for x in limitations) + "\n",
        encoding="utf-8")


if __name__ == "__main__":
    try:
        run(globals()["REQUEST"])
    except Exception as error:
        save("stage-failure.json", {"stage": globals()["REQUEST"]["stage"], "error": str(error),
                                   "instruction": "Preserve completed outputs; only repair the failed stage/postprocessing"})
        raise
