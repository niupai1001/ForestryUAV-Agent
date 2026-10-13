"""ROI derivation inside existing scientific Jobs; no canopy/terrain recomputation."""
import csv
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import laspy
import numpy as np

REQUEST: dict
R_SCRIPT: str


def save(name, value):
    Path(name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    if Path(path).stat().st_size > 131072:
        raise ValueError("ROI/lineage record exceeds128KiB")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(request):
    os.environ.update(PROJ_DATA="/opt/science/share/proj", PROJ_LIB="/opt/science/share/proj",
                      GDAL_DATA="/opt/science/share/gdal", PROJ_NETWORK="OFF")
    paths = {role: item["path"] for role, item in request["inputs"].items()}
    fingerprints = {role: sha(path) for role, path in paths.items()}
    parent = read(paths["instance_record"])
    quality = read(paths["quality"])
    if read(paths["roi"]) != request["roi"]:
        raise ValueError("mounted ROI changed after the submitted definition")
    if (parent["stage"] != "instances" or parent["method_id"] != "lidar.p2r_lmf_dalponte.v1"
            or parent["science_image"] != request["science_image"]
            or parent["input_sha256"]["chm"] != fingerprints["chm"]):
        raise ValueError("ROI must reuse the recorded scientific cloud/CHM lineage")
    for role, name in (("segmented", "segmented.laz"), ("trees", "trees.csv"),
                       ("vectors", "trees.gpkg"), ("quality", "instance-quality.json")):
        if parent["outputs"][name]["sha256"] != fingerprints[role]:
            raise ValueError("ROI parent product byte identity changed: " + role)
    cloud = laspy.read(paths["segmented"])
    if not 0 < len(cloud.points) <= 5_000_000 or len(cloud.points) != quality["original_points"]:
        raise ValueError("ROI whole-flight postprocessing requires bounded complete original inventory")
    if not np.array_equal(cloud.SourcePointId, np.arange(len(cloud.points))):
        raise ValueError("source inventory no longer maps original point order")
    labels = np.asarray(cloud.treeID).copy()
    positive = labels > 0
    with Path("assigned-points.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["SourcePointId", "treeID", "X", "Y"])
        writer.writerows(zip(np.asarray(cloud.SourcePointId)[positive], labels[positive],
                             np.asarray(cloud.x)[positive], np.asarray(cloud.y)[positive], strict=True))
    save("request.json", request)
    r_request = {"method_id": request["method_id"], "roi": request["roi"],
                 "chm_path": "read-chm.tif", "vectors_path": "read-vectors.gpkg", "trees_path": paths["trees"]}
    aliases = {"read-chm.tif": paths["chm"], "read-vectors.gpkg": paths["vectors"]}
    for name, source in aliases.items():
        shutil.copyfile(source, name)
    save("r-request.json", r_request)
    Path("lidar-roi.R").write_text(globals()["R_SCRIPT"], encoding="utf-8")
    try:
        with Path("roi-stage.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(["Rscript", "--vanilla", "lidar-roi.R", "--request", "r-request.json",
                                     "--result", "roi-result.json"], stdout=log, stderr=subprocess.STDOUT, check=False)
    finally:
        for name in aliases:
            Path(name).unlink()
    if result.returncode:
        raise RuntimeError("ROI overlay failed; inspect roi-stage.log and retain completed products")
    overlay = read("roi-result.json")
    mapping = np.loadtxt("roi-point-labels.csv", delimiter=",", skiprows=1, dtype=np.int64, ndmin=2) if positive.any() else np.empty((0, 2), np.int64)
    if not np.array_equal(mapping[:, 0], np.asarray(cloud.SourcePointId)[positive]):
        raise ValueError("ROI point label map changed source order")
    if np.any((mapping[:, 1] != 0) & (mapping[:, 1] != labels[positive])):
        raise ValueError("ROI cannot relabel an existing instance")
    labels[positive] = mapping[:, 1].astype(np.uint32)
    cloud.treeID = labels
    cloud.write("roi-segmented.laz")
    reread = laspy.read("roi-segmented.laz")
    original = laspy.read(paths["segmented"])
    if not np.array_equal(reread.treeID, labels) or len(reread.points) != len(original.points):
        raise ValueError("ROI LAZ point/ID roundtrip failed")
    if any(not np.array_equal(reread[dimension], original[dimension])
           for dimension in original.header.point_format.dimension_names if dimension != "treeID"):
        raise ValueError("ROI changed a frozen point attribute other than treeID")
    del original, reread
    limitations = quality["limitations"] + ["ROI为明确声明的处理边界，不是独立测量样地", "面积按投影像元与多边形精确相交；没有填洞或重新分割",
                                          "只有树顶覆盖于ROI内的原接受实例计数，原整数ID不重排", "边界和ROI外点保留原始高程/HAG，ROI外实例标签为0"]
    derived = {**quality, "method_id": request["method_id"], "parent_method_id": quality["method_id"],
               "roi_sha256": fingerprints["roi"], "roi_source_note": request["roi"]["source_note"],
               "detected_instances": overlay["detected_instances"], "valid_area_m2": overlay["valid_area_m2"],
               "assigned_points": int((labels > 0).sum()), "unassigned_points": int((labels == 0).sum()),
               "limitations": limitations, "roi": overlay, "analysis_area_basis": "exact_polygon_pixel_intersection",
               "only_treeID_changed": True, "scientific_stages_replayed": False}
    save("instance-quality.json", derived)
    support_id = "roi:" + fingerprints["roi"] + ":chm:" + fingerprints["chm"]
    metrics = []
    area = overlay["valid_area_m2"]
    for name, value, unit in (("valid_area_ha.v1", area / 10000 if area else None, "ha"),
                              ("detected_trees_count.v1", overlay["detected_instances"], "count"),
                              ("detected_tree_density_ha.v1", overlay["density_ha"], "count/ha"),
                              ("canopy_height_rule_pct.v1", overlay["canopy_height_rule_pct"], "%")):
        metrics.append({"result_id": hashlib.sha256((support_id + name).encode()).hexdigest(),
                        "metric_spec_ref": name, "spatial_support_id": support_id, "estimate": value,
                        "value_ref": None, "unit": unit, "method_ref": request["method_id"],
                        "input_refs": {k: v["resource_id"] for k, v in request["inputs"].items()},
                        "quality_ref": "instance-quality.json", "uncertainty_kind": "not_estimated",
                        "uncertainty_payload_ref": None, "validation_status": "internal_geometry_invariants_only",
                        "availability_status": "available" if area else "unavailable",
                        "availability_reason": None if area else "zero joint valid ROI support", "limitations": limitations})
    for name, unit in (("tree_apex_height_m.v1", "m"), ("crown_area_m2.v1", "m2"), ("crown_eqdiam_m.v1", "m")):
        metrics.append({**metrics[0], "result_id": hashlib.sha256((support_id + name).encode()).hexdigest(),
                        "metric_spec_ref": name, "estimate": None, "value_ref": "trees.csv", "unit": unit})
    save("metrics.json", {"schema_version": 1, "results": metrics, "tree_count_filter": "accepted_instance == TRUE"})
    with Path("metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(metrics[0]))
        writer.writeheader()
        for row in metrics:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                             if isinstance(v, (dict, list)) else v for k, v in row.items()})
    count_text = "不可用" if overlay["detected_instances"] is None else str(overlay["detected_instances"])
    Path("ROI处理报告.md").write_text(
        f"# ROI冠层实例交付\n\n原科学方法 `{quality['method_id']}`，本次只执行 `{request['method_id']}` 的几何/指标后处理。\n\n"
        f"ROI声明：{request['roi']['source_note']}。ROI面积 {overlay['roi_area_m2']:.6f} 平方米，"
        f"共同有效支持 {area:.6f} 平方米，排除/缺失支持 {overlay['excluded_support_area_m2']:.6f} 平方米。\n\n"
        f"树顶覆盖于ROI内的接受候选实例计数：{count_text}；ROI裁剪树冠 {overlay['roi_clipped_crowns']} 个；"
        f"{overlay['partial_roi_cells']} 个边界像元使用相交面积权重。树冠面积/等效直径按裁剪后几何计算。\n\n"
        f"保留全部 {len(cloud.points)} 点的原始高程/HAG/分类与源点映射，仅过滤treeID；未分配 {derived['unassigned_points']} 点标记0。\n\n"
        "**部分交付，现场准确性尚未验证。**\n\n" + "\n".join("- " + text for text in limitations) + "\n", encoding="utf-8")
    names = ["roi-segmented.laz", "roi-chm.tif", "roi-fraction.tif", "roi-valid-fraction.tif", "roi.gpkg", "roi-trees.gpkg",
             "trees.csv", "metrics.csv", "metrics.json", "instance-quality.json", "ROI处理报告.md"]
    save("roi-stage-result.json", {"schema_version": 1, "method_id": request["method_id"], "status": "partial",
         "science_image": request["science_image"], "input_sha256": fingerprints, "roi": request["roi"],
         "result": overlay, "outputs": {name: {"sha256": sha(name), "size_bytes": Path(name).stat().st_size} for name in names},
         "only_treeID_changed": True, "scientific_stages_replayed": False})
    print(json.dumps(overlay, ensure_ascii=False))


if __name__ == "__main__":
    try:
        run(globals()["REQUEST"])
    except Exception as error:
        save("roi-failure.json", {"error": str(error), "instruction": "Reuse completed geometry/products; repair failed postprocessing only"})
        raise
