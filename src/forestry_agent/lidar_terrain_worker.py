"""Trusted recipe body rendered into a restricted Jobs Python worker, never host-run."""
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

import laspy
import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy.spatial import cKDTree

REQUEST: dict  # assigned by the trusted renderer before this body
NODATA = -9999.0


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def command(name, argv):
    with Path(name + ".log").open("w", encoding="utf-8") as log:
        result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} exited {result.returncode}; inspect {name}.log")


def pipeline(name, stages):
    save_json(name + ".pipeline.json", stages)
    command(name, ["pdal", "pipeline", name + ".pipeline.json", "--metadata", name + ".metadata.json"])


def write_raster(path, array, profile, dtype="float32", nodata=NODATA):
    with rasterio.open(path, "w", **{**profile, "count": 1, "dtype": dtype, "nodata": nodata}) as result:
        result.write(array.astype(dtype), 1)


def run(request):
    # Conda activation normally exports these. Jobs deliberately uses no shell
    # activation, so resolve data directories explicitly inside the container.
    os.environ.update(PROJ_DATA="/opt/science/share/proj", PROJ_LIB="/opt/science/share/proj",
                      GDAL_DATA="/opt/science/share/gdal", PROJ_NETWORK="OFF")
    started = time.time()
    stages = []
    save_json("request.json", request)

    def checkpoint(name, files):
        stages.append({"stage": name, "outputs": [{"file": f, "bytes": Path(f).stat().st_size,
                                                      "sha256": digest(f)} for f in files]})
        save_json("completed-stages.json", {"method_id": request["method_id"], "stages": stages,
                                           "source_ref": request["cloud_ref"]})

    try:
        reused = request.get("reuse")
        if reused:
            for name, reference in reused["files"].items():
                if digest(reference["path"]) != reference["sha256"]:
                    raise ValueError("reused stage artifact byte identity changed: " + name)
                shutil.copyfile(reference["path"], name)
            checkpoint("reused-computed-products", list(reused["files"]))
        else:
            command("versions-pdal", ["pdal", "--version"])
            command("versions-r", ["Rscript", "--vanilla", "-e",
                ("library(lidR); cat(jsonlite::toJSON(list(R=R.version.string,"
                "lidR=as.character(packageVersion('lidR')),terra=as.character(packageVersion('terra')),"
                "sf=as.character(packageVersion('sf'))),auto_unbox=TRUE))")])
            lock = Path("/opt/science/explicit-lock.txt")
            Path("science-explicit-lock.txt").write_bytes(lock.read_bytes())
        source = Path(request["cloud_path"])
        original_sha = digest(source)
        declaration = json.loads(Path(request["declaration_path"]).read_text(encoding="utf-8"))
        if (declaration.get("z_semantics") != "elevation" or declaration.get("z_unit") != "m"
                or declaration.get("vertical_ref") != request["vertical_ref"]):
            raise ValueError("mounted declaration does not match frozen elevation/vertical recipe")
        if not reused:
            cloud = laspy.read(source)
            if len(cloud.points) != request["expected_point_count"] or len(cloud.points) > 5_000_000:
                raise ValueError("point count differs from inspected bounded source")
            crs = cloud.header.parse_crs()
            if crs is None or crs.to_string() != request["crs"]:
                raise ValueError("file CRS differs from inspected source")
            retained = ~np.asarray(cloud.withheld, dtype=bool)
            rn, nr = np.asarray(cloud.return_number)[retained], np.asarray(cloud.number_of_returns)[retained]
            if not len(rn) or np.any(rn < 1) or np.any(nr < 1) or np.any(rn > nr):
                raise ValueError("SMRF last/only strategy requires valid recorded return number/count; no fabricated returns")
            header = laspy.LasHeader(point_format=cloud.header.point_format.id, version="1.4")
            header.scales, header.offsets = cloud.header.scales.copy(), cloud.header.offsets.copy()
            header.add_crs(crs)
            header.add_extra_dim(laspy.ExtraBytesParams(name="SourcePointId", type="uint32"))
            clean = laspy.LasData(header)
            clean.points = laspy.ScaleAwarePointRecord.zeros(int(retained.sum()), header=header)
            isolated = {"classification", "synthetic", "key_point", "withheld", "user_data"}
            for dim in header.point_format.standard_dimension_names:
                if dim not in isolated:
                    clean[dim] = np.asarray(cloud[dim])[retained]
            clean.SourcePointId = np.flatnonzero(retained).astype("uint32")
            clean.classification = np.zeros(len(clean.points), dtype="uint8")
            isolated_dims = list(cloud.header.point_format.extra_dimension_names)
            clean.write("clean-input.las")
            input_count = len(cloud.points)
            del cloud
            save_json("annotation-isolation.json", {"original_sha256": original_sha, "original_points": input_count,
                "retained_points": len(clean.points), "excluded_withheld_points": int((~retained).sum()),
                "classification_reset": True, "removed_extra_dimensions": isolated_dims,
                "isolated_standard_fields": sorted(isolated), "mapping": "SourcePointId = original zero-based point index",
                "ground_prediction": "new SMRF, no reuse of original Classification or oracle extras"})
            checkpoint("input-isolation", ["clean-input.las", "annotation-isolation.json"])
        else:
            isolation = json.loads(Path("annotation-isolation.json").read_text(encoding="utf-8"))
            if isolation["original_sha256"] != original_sha or isolation["original_points"] != request["expected_point_count"]:
                raise ValueError("reused products belong to a different original source")
            clean = laspy.read("clean-input.las")
            input_count = isolation["original_points"]
        if not reused:
            pipeline("classification", [{"type": "readers.las", "filename": "clean-input.las", "use_eb_vlr": True},
                {"type": "filters.outlier", "method": "statistical", "mean_k": 8, "multiplier": 3.0},
                {"type": "filters.smrf", "cell": 1.0, "scalar": 1.25, "slope": 0.15,
                 "threshold": 0.5, "window": 18.0, "returns": "last,only", "ignore": "Classification[7:7]"},
                {"type": "writers.las", "filename": "classified.las", "extra_dims": "all",
                 "forward": "all"}])
        classified = laspy.read("classified.las")
        if len(classified.points) != len(clean.points):
            raise ValueError("classification changed point inventory")
        if reused and "SourcePointId" not in classified.header.point_format.extra_dimension_names:
            for dimension in clean.header.point_format.standard_dimension_names:
                if (dimension not in {"classification", "synthetic", "key_point", "withheld", "user_data"}
                        and not np.array_equal(clean[dimension], classified[dimension])):
                    raise ValueError("cannot prove original point order for exact mapping repair: " + dimension)
            classified.add_extra_dim(laspy.ExtraBytesParams(name="SourcePointId", type="uint32"))
            classified.SourcePointId = np.asarray(clean.SourcePointId).copy()
            classified.write("classified.las")
            # Verify only the corrected extra-byte transport protocol, with four
            # retained points. No classification, terrain or height calculation.
            probe_header = laspy.LasHeader(point_format=clean.header.point_format.id, version="1.4")
            probe_header.scales, probe_header.offsets = clean.header.scales.copy(), clean.header.offsets.copy()
            probe_header.add_crs(clean.header.parse_crs())
            probe_header.add_extra_dim(laspy.ExtraBytesParams(name="SourcePointId", type="uint32"))
            probe = laspy.LasData(probe_header)
            probe.points = clean.points[:4].copy()
            probe.write("mapping-probe.las")
            pipeline("mapping-transport", [{"type": "readers.las", "filename": "mapping-probe.las", "use_eb_vlr": True},
                {"type": "writers.las", "filename": "mapping-probe-returned.las", "extra_dims": "all", "forward": "all"}])
            returned = laspy.read("mapping-probe-returned.las")
            if not np.array_equal(returned.SourcePointId, probe.SourcePointId):
                raise ValueError("corrected extra-byte source mapping transport failed")
        del clean
        x, y = np.asarray(classified.x), np.asarray(classified.y)
        ground = np.asarray(classified.classification) == 2
        noise = np.asarray(classified.classification) == 7
        if not np.any(ground):
            raise ValueError("SMRF found no ground points; height products unavailable")
        resolution = 1.0
        ox, oy = math.floor(x.min()), math.floor(y.min())
        width, height = math.floor(x.max() - ox) + 1, math.floor(y.max() - oy) + 1
        grid = {"resolution": resolution, "origin_x": ox, "origin_y": oy, "width": width, "height": height}
        common = {"type": "writers.gdal", **grid, "dimension": "Z", "window_size": 0,
                  "nodata": NODATA, "data_type": "float32"}
        if not reused:
            pipeline("dtm", [{"type": "readers.las", "filename": "classified.las", "use_eb_vlr": True},
                {"type": "filters.expression", "expression": "Classification == 2"},
                {**common, "filename": "dtm-raw.tif", "output_type": "idw", "radius": math.sqrt(2)}])
            pipeline("dsm", [{"type": "readers.las", "filename": "classified.las", "use_eb_vlr": True},
                {"type": "filters.expression", "expression": "Classification != 7"},
                {**common, "filename": "dsm.tif", "output_type": "max", "binmode": True}])
            checkpoint("classification-and-surfaces", ["classified.las", "dtm-raw.tif", "dsm.tif"])
        with rasterio.open("dtm-raw.tif") as dtm, rasterio.open("dsm.tif") as dsm:
            expected = from_origin(ox, oy + height, resolution, resolution)
            if dtm.transform != dsm.transform or dtm.transform != expected or dtm.shape != (height, width):
                raise ValueError("DTM/DSM do not match the declared common grid")
            if dtm.crs != dsm.crs or dtm.crs != rasterio.crs.CRS.from_user_input(request["crs"]):
                raise ValueError("surface CRS mismatch")
            dtm_values, dsm_values = dtm.read(1), dsm.read(1)
            profile = dtm.profile
        if not reused:
            rows = height - 1 - np.floor(y[ground] - oy).astype(int)
            cols = np.floor(x[ground] - ox).astype(int)
            counts = np.zeros((height, width), dtype="uint32")
            np.add.at(counts, (rows, cols), 1)
            gy, gx = np.indices((height, width))
            centres = np.column_stack((ox + gx.ravel() + .5, oy + height - gy.ravel() - .5))
            distance = cKDTree(np.column_stack((x[ground], y[ground]))).query(centres, workers=1)[0].reshape(height, width)
            supported = (counts >= 1) & (distance <= math.sqrt(2)) & (dtm_values != NODATA) & np.isfinite(dtm_values)
            write_raster("ground-cell-count.tif", counts, profile, "uint32", None)
            write_raster("nearest-ground-distance.tif", distance, profile)
            write_raster("terrain-support.tif", supported.astype("uint8"), profile, "uint8", 255)
            masked = np.where(supported, dtm_values, NODATA)
            write_raster("dtm.tif", masked, profile)
            common_valid = supported & (dsm_values != NODATA) & np.isfinite(dsm_values)
            write_raster("chm-demdiff.tif", np.where(common_valid, dsm_values - dtm_values, NODATA), profile)
            checkpoint("ground-support", ["ground-cell-count.tif", "nearest-ground-distance.tif",
                         "terrain-support.tif", "dtm.tif", "chm-demdiff.tif"])
        else:
            with rasterio.open("terrain-support.tif") as support_file:
                supported = support_file.read(1) == 1
            with rasterio.open("dtm.tif") as supported_dtm:
                masked = supported_dtm.read(1)
            common_valid = supported & (dsm_values != NODATA) & np.isfinite(dsm_values)
        if not reused:
            pipeline("normalization", [{"type": "readers.las", "filename": "classified.las", "use_eb_vlr": True},
                {"type": "filters.hag_dem", "raster": "dtm.tif", "nodata_hag": NODATA,
                 "zero_ground": False, "min_clamp": -1e12, "max_clamp": 1e12},
                {"type": "writers.las", "filename": "hag.las", "extra_dims": "all", "forward": "all"}])
            hag = laspy.read("hag.las")
        else:
            hag = laspy.read("hag.las")
            if "SourcePointId" not in hag.header.point_format.extra_dimension_names:
                if any(not np.array_equal(hag[d], classified[d]) for d in ("X", "Y", "Z", "return_number", "number_of_returns", "intensity", "gps_time")):
                    raise ValueError("cannot prove unchanged point order for HAG mapping repair")
                hag.add_extra_dim(laspy.ExtraBytesParams(name="SourcePointId", type="uint32"))
                hag.SourcePointId = np.asarray(classified.SourcePointId).copy()
                hag.write("hag.las")
        if len(hag.points) != len(classified.points) or not np.array_equal(hag.SourcePointId, classified.SourcePointId):
            raise ValueError("normalization lost source point mapping")
        if not np.array_equal(hag.Z, classified.Z):
            raise ValueError("HAG silently changed original raw Z elevation")
        heights = np.asarray(hag.HeightAboveGround)
        valid = np.isfinite(heights) & (heights != NODATA) & ~noise
        valid_ground = valid & ground
        residual = heights[valid_ground]
        # Separate R working cloud changes only its Z semantics, retaining original Z/map.
        working = laspy.LasData(hag.header.copy())
        working.points = hag.points[valid].copy()
        working.add_extra_dim(laspy.ExtraBytesParams(name="OriginalZ", type="float64"))
        # PDAL metadata forwarding can round header offsets by a few float64
        # ULPs. The OriginalZ field must copy the actual source observation,
        # using the already-verified source point map, rather than a forwarded
        # header's reconstructed elevation.
        source_cloud = laspy.read(source)
        original_z = np.asarray(source_cloud.z)[np.asarray(working.SourcePointId)].copy()
        del source_cloud
        working.OriginalZ = original_z
        working.header.offsets[2] = 0
        working.points.offsets[2] = 0
        working.z = heights[valid]
        working.write("normalized-working.las")
        checkpoint("height-above-ground", ["hag.las", "normalized-working.las"])
        quality = {"schema_version": 1, "method_id": request["method_id"],
            "status": "partial", "interpretation": "terrain products inspected; no independent accuracy or single-tree acceptance",
            "source_sha256": original_sha, "reused_from_job": reused["job_id"] if reused else None, "original_points": input_count,
            "classified_points": len(classified.points), "ground_points": int(ground.sum()), "noise_points": int(noise.sum()),
            "grid": {**grid, "crs": request["crs"], "vertical_ref": request["vertical_ref"], "vertical_basis": "publisher_declared"},
            "support_rule": {"minimum_ground_points_in_own_cell": 1, "maximum_nearest_ground_distance_m": math.sqrt(2),
                             "status": "bootstrap_not_accuracy_threshold", "no_hole_fill": True},
            "terrain_supported_cells": int(supported.sum()), "grid_cells": int(supported.size),
            "common_dsm_dtm_cells": int(common_valid.sum()), "valid_hag_points": int(valid.sum()),
            "invalid_or_noise_hag_points": int((~valid).sum()), "negative_valid_hag_points": int((heights[valid] < 0).sum()),
            "ground_residual_diagnostic_m": {"count": len(residual), "median": float(np.median(residual)) if len(residual) else None,
                                            "quantiles": np.quantile(residual, [.01,.05,.95,.99]).tolist() if len(residual) else None,
                                            "independent_accuracy": False, "zero_ground": False},
            "normalized_working": {"z_semantics": "height_above_ground", "unit": "m", "original_z_dimension": "OriginalZ",
                                   "source_point_id": "SourcePointId", "negative_residuals_retained": True},
            "elapsed_seconds": time.time()-started, "python": platform.python_version(),
            "limitations": request["limitations"] + ["单木检测/分割与lidR CHM尚未执行", "DSM包含接受的非噪声表面，不能等同林冠",
                                                      "支持比例分母是整架次矩形网格，不是独立样地ROI"]}
        save_json("terrain-quality.json", quality)
        report = (f"# 无人机点云地形处理\n\n方法：{request['method_id']}；任务：{request['task_id']}；修订：{request['spec_revision']}。\n\n"
            f"输入SHA256：`{original_sha}`。原始{input_count}点，重新分类地面{int(ground.sum())}点；原分类/额外标签未用于预测。\n\n"
            f"输出1米对齐DTM/DSM、支持诊断、DSM−DTM及保留原Z的HAG；R工作文件另将Z定义为离地高度，并保留OriginalZ/SourcePointId。\n\n"
            f"整架次矩形网格{supported.size}像元，地形支持{int(supported.sum())}像元；有效非噪声HAG {int(valid.sum())}点。无支持保留NoData，没有填洞。\n\n"
            "**状态：部分交付。** 单木检测/分割、lidR CHM和独立精度未完成；地面残差是内部诊断，不是树高或地形独立验证。\n\n"
            + "\n".join("- " + x for x in quality["limitations"]) + "\n")
        Path("地形处理报告.md").write_text(report, encoding="utf-8")
        checkpoint("quality-and-report", ["terrain-quality.json", "地形处理报告.md"])
        print(json.dumps({"status": "partial", "ground_points": int(ground.sum()),
                          "terrain_supported_cells": int(supported.sum()), "valid_hag_points": int(valid.sum())}))
    except Exception as error:
        save_json("terrain-failure.json", {"status": "failed", "method_id": request["method_id"],
                  "error": str(error), "completed_stages": stages, "elapsed_seconds": time.time()-started,
                  "instruction": "Inspect and reuse completed stage outputs; do not rerun completed calculations"})
        raise


if __name__ == "__main__":
    run(globals()["REQUEST"])
