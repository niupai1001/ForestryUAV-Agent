"""Port of ForestryUAV-Agent/run_prosail.py; executed only by persistent Jobs.

Legacy single nearest neighbour, rectangular SRF and NDVI semantics retained.
Block processing bounds memory; no pixels are sent to the language model.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build_lut(d, output):
    import numpy as np
    import prosail

    # Iteration order is part of the legacy seeded experiment identity.
    names = ("n", "cab", "car", "cbrown", "cw", "cm", "lai", "lidfa", "hspot", "rsoil", "psoil")
    rng = np.random.default_rng(d["seed"])
    parameters = {name: rng.uniform(*d["ranges"][name], d["lut_size"]) for name in names}
    wavelengths = np.arange(400, 2501)
    response = [(wavelengths >= b["center_nm"] - b["half_width_nm"]) &
                (wavelengths <= b["center_nm"] + b["half_width_nm"]) for b in d["bands"]]
    spectra = np.empty((d["lut_size"], len(response)), dtype="float32")
    for i in range(d["lut_size"]):
        p = {name: values[i] for name, values in parameters.items()}
        spectrum = prosail.run_prosail(*(p[n] for n in names[:9]), d["solar_zenith"], d["view_zenith"],
            d["relative_azimuth"], ant=0., prospect_version="D", typelidf=2, factor="SDR",
            rsoil=p["rsoil"], psoil=p["psoil"])
        spectra[i] = [spectrum[band].mean() for band in response]
    if not np.all(np.isfinite(spectra)) or np.any((spectra < 0) | (spectra > 1)):
        raise ValueError("LUT contains nonphysical reflectance")
    path = output / "prosail_lut.npz"
    np.savez(path, reflectance=spectra, lai=parameters["lai"], cab=parameters["cab"],
             band_names=np.asarray([b["name"] for b in d["bands"]]),
             metadata_json=json.dumps(d), **{"parameter_" + n: p for n, p in parameters.items()})
    return {"lut": path.name, "rows": len(spectra), "method": "legacy_rectangular_srf_uniform_random",
            "sha256": digest(path), "declaration": d}


def inspect_raster(path):
    import rasterio

    with rasterio.open(path) as src:
        return {"width": src.width, "height": src.height, "count": src.count, "dtype": list(src.dtypes),
                "bands": list(src.descriptions), "crs": str(src.crs) if src.crs else None,
                "transform": list(src.transform), "scales": list(src.scales), "offsets": list(src.offsets),
                "colorinterp": [c.name for c in src.colorinterp],
                "pixel_data_sent_to_model": False, "reflectance_calibration": "requires_source_declaration"}


def invert(raster, lut, d, output, threads):
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    from scipy.spatial import cKDTree

    with np.load(lut, allow_pickle=False) as data:
        spectra = np.asarray(data["reflectance"], dtype="float32")
        lai, cab = np.asarray(data["lai"]), np.asarray(data["cab"])
        names = list(data["band_names"].astype(str)) if "band_names" in data else None
    # Old npz files had no names: declaration explicitly states their column order.
    if names is not None and names != d["band_names"]:
        raise ValueError("LUT band order does not match explicit declaration")
    if (spectra.ndim != 2 or spectra.shape[1] != len(d["band_names"]) or len(spectra) < 1
            or lai.shape != (len(spectra),) or cab.shape != (len(spectra),)
            or not all(np.all(np.isfinite(a)) for a in (spectra, lai, cab))
            or np.any((spectra < 0) | (spectra > 1)) or np.any(lai < 0) or np.any(cab < 0)):
        raise ValueError("Invalid legacy LUT")
    scale = spectra.std(axis=0)
    scale[scale == 0] = 1  # Exact legacy normalization; do not change to an epsilon threshold.
    tree = cKDTree(spectra / scale)
    target = output / "prosail_lai_cab_rmse.tif"
    count = 0
    stats = {n: {"minimum": float("inf"), "maximum": -float("inf"), "sum": 0.} for n in ("LAI", "Cab_ug_cm2", "spectral_RMSE")}
    with rasterio.open(raster) as src:
        bands = d["raster_bands"]
        declared_alpha = d["alpha_band"]
        alpha_channels = [i+1 for i, channel in enumerate(src.colorinterp) if channel.name == "alpha"]
        if len(alpha_channels) > 1:
            raise ValueError("Ambiguous GeoTIFF Alpha channels")
        alpha_band = declared_alpha or (alpha_channels[0] if alpha_channels else None)
        if declared_alpha and alpha_channels and declared_alpha != alpha_channels[0]:
            raise ValueError("Declared Alpha band conflicts with GeoTIFF color interpretation")
        if alpha_band in bands:
            raise ValueError("Alpha channel cannot be used as reflectance")
        if max([*bands, alpha_band or 1]) > src.count:
            raise ValueError("Band mapping exceeds raster band count")
        observed = [src.descriptions[i-1] for i in bands]
        if any(name and name != expected for name, expected in zip(observed, d["band_names"])):
            raise ValueError("Raster band descriptions conflict with declaration")
        profile = src.profile.copy()
        profile.update(driver="GTiff", dtype="float32", count=3, nodata=float("nan"), compress="deflate",
                       tiled=True, blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER")
        profile.pop("photometric", None)
        profile.pop("interleave", None)
        with rasterio.open(target, "w", **profile) as dst:
            dst.descriptions = ("LAI", "Cab_ug_cm2", "spectral_RMSE")
            dst.update_tags(method_id="imagery.prosail_legacy_nn.v1", lut_sha256=digest(lut),
                            declaration=json.dumps(d), rmse_unit="reflectance_fraction")
            # Explicit 256px windows also bound memory for strip-organized TIFFs.
            for row in range(0, src.height, 256):
                for col in range(0, src.width, 256):
                    window = Window(col, row, min(256, src.width-col), min(256, src.height-row))
                    values = src.read(bands, window=window).astype("float32")
                    for j, band in enumerate(bands):
                        values[j] = values[j] * src.scales[band-1] + src.offsets[band-1]
                    valid = (src.read_masks(bands, window=window) > 0).all(axis=0)
                    valid &= np.isfinite(values).all(axis=0) & ((values >= 0) & (values <= 1)).all(axis=0)
                    if alpha_band:
                        valid &= src.read(alpha_band, window=window) > 0
                    if d["ndvi_threshold"] is not None:
                        red, nir = (values[d["band_names"].index(d[n])] for n in ("red_name", "nir_name"))
                        ndvi = np.divide(nir-red, nir+red, out=np.zeros_like(red), where=(nir+red) != 0)
                        valid &= ndvi >= d["ndvi_threshold"]
                    result = np.full((3, int(window.height), int(window.width)), np.nan, dtype="float32")
                    if valid.any():
                        pixels = values[:, valid].T
                        _, match = tree.query(pixels / scale, workers=threads)
                        rmse = np.sqrt(np.mean((pixels - spectra[match]) ** 2, axis=1))
                        for i, (name, v) in enumerate(zip(stats, (lai[match], cab[match], rmse))):
                            result[i][valid] = v
                            stats[name]["minimum"] = min(stats[name]["minimum"], float(v.min()))
                            stats[name]["maximum"] = max(stats[name]["maximum"], float(v.max()))
                            stats[name]["sum"] += float(v.sum(dtype="float64"))
                        count += int(valid.sum())
                    dst.write(result, window=window)
        if not count:
            raise ValueError("No valid calibrated vegetation pixels; output is not a successful inversion")
        grid = {"crs": str(src.crs) if src.crs else None, "transform": list(src.transform),
                "width": src.width, "height": src.height}
    # A bounded preview is optional display data, never an analytical raster substitute.
    from PIL import Image
    with rasterio.open(target) as src:
        ratio = min(1., 1024 / max(src.width, src.height))
        preview = src.read(1, out_shape=(max(1, int(src.height*ratio)), max(1, int(src.width*ratio))))
        valid = np.isfinite(preview)
        value = np.nan_to_num(np.clip(preview / 7., 0, 1))
        rgb = np.stack((255*value, 255*np.sqrt(value), 50*(1-value)), axis=2).astype("uint8")
        rgb[~valid] = 0
        Image.fromarray(rgb).save(output / "lai_preview.png")
    return {"raster": target.name, "preview": "lai_preview.png", "valid_pixels": count, "grid": grid,
            "statistics": {n: {"minimum": v["minimum"], "maximum": v["maximum"], "mean": v["sum"]/count} for n, v in stats.items()},
            "method": "legacy_std_scaled_single_nearest_neighbor", "declaration": d,
            "limitations": ["Rectangular SRF approximation and model priors", "spectral_RMSE is reflectance fit, not field LAI accuracy"],
            "lut_sha256": digest(lut), "legacy_lut_columns_declared": names is None,
            "resolved_alpha_band": alpha_band,
            "alpha_source": "explicit_declaration" if declared_alpha else "GeoTIFF_colorinterp" if alpha_band else "none"}


def orthomosaic(inputs, declaration, output, threads, resume_job_id=None):
    import shutil
    import subprocess

    runner = Path("/code/run.sh")
    if not runner.is_file():
        raise RuntimeError("Prepared ODM image must contain /code/run.sh; no automatic substitute")
    project = output / "project"
    images = project / "images"
    if resume_job_id:
        import stat
        import zipfile
        archive_path = inputs["resume_archive"]["path"]
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                target = output / member.filename
                if (not target.resolve().is_relative_to(output.resolve()) or "\\" in member.filename
                        or stat.S_ISLNK(member.external_attr >> 16)
                        or not (member.filename.startswith("project/") or member.filename == "resume-provenance.json")):
                    raise ValueError("Unsafe resume archive entry")
            archive.extractall(output)
        provenance = json.loads((output / "resume-provenance.json").read_text(encoding="utf-8"))
        if provenance["source_job_id"] != resume_job_id:
            raise ValueError("Resume archive source job mismatch")
        for entry in provenance["files"]:
            if digest(output / entry["path"]) != entry["sha256"]:
                raise ValueError("Resume file hash mismatch")
        write_json(output / "reused-stages.json", {"source_job_id": resume_job_id,
            "policy": "report_only_no_science_recalculation" if provenance.get("resume_stage") == "odm_report"
                      else "Use ODM existing-stage checkpoints; no rerun/reset flags",
            "files": len(provenance["files"])})
    else:
        images.mkdir(parents=True)
    names = set()
    manifest = []
    for entry in ([] if resume_job_id else inputs.values()):
        name = entry["name"]
        if Path(name).name != name or name in names or Path(name).suffix.lower() not in {".jpg", ".jpeg", ".tif", ".tiff", ".dng"}:
            raise ValueError("Invalid or duplicate flight image name")
        names.add(name)
        shutil.copyfile(entry["path"], images / name)
        manifest.append({"name": name, "sha256": digest(entry["path"])})
    write_json(output / "input-manifest.json", manifest if not resume_job_id else {"reused_from_job": resume_job_id})
    inspection = inspect_dataset(images)  # noqa: F821 -- legacy definitions injected for ODM only
    problems = validate_dataset(inspection)  # noqa: F821
    write_json(output / "flight-inspection.json", {**inspection, "problems": problems})
    if problems or inspection["processing_mode"] != declaration["processing_mode"]:
        raise ValueError("Flight metadata failed validation or conflicts with declared processing mode")
    command = [str(runner), "--project-path", str(output), "--skip-3dmodel", "--max-concurrency", str(threads)]
    if declaration["processing_mode"] == "multispectral":
        command += ["--radiometric-calibration", declaration["radiometric_calibration"]]
    if declaration["orthophoto_resolution_cm"] is not None:
        command += ["--orthophoto-resolution", str(declaration["orthophoto_resolution_cm"])]
    if declaration["dsm"]:
        command += ["--dsm"]
    if resume_job_id and provenance.get("resume_stage") == "odm_report":
        # The scientific products already exist: repair only report/postprocess.
        inspect_raster(project / "odm_orthophoto" / "odm_orthophoto.tif")
        command += ["--rerun-from", "odm_report"]
    command += ["project"]
    write_json(output / "odm-command.json", command)
    # Windows DrvFS permits data writes by the sandbox UID but can reject chmod
    # on the resulting root-owned files. Limit the compatibility adjustment to
    # copy metadata for this job's project; never suppress data-copy errors.
    compatibility = output / ".odm-python"
    compatibility.mkdir(exist_ok=True)
    (compatibility / "sitecustomize.py").write_text('''import errno, functools, shutil
from pathlib import Path
def adapt(function):
    @functools.wraps(function)
    def copy_metadata(src, dst, *args, **kwargs):
        try:
            return function(src, dst, *args, **kwargs)
        except PermissionError as error:
            a, b = Path(src), Path(dst)
            root = Path('/output/project')
            if (error.errno != errno.EPERM or not a.is_file() or not b.is_file()
                or a.is_symlink() or b.is_symlink()
                or not (a.resolve().is_relative_to(root) or a.resolve().is_relative_to(Path('/code')))
                or not b.resolve().is_relative_to(root)):
                raise
            with open('/output/metadata-compat.log', 'a') as stream:
                stream.write(function.__name__ + ': ' + str(b) + '\\n')
    return copy_metadata
shutil.copymode = adapt(shutil.copymode)
shutil.copystat = adapt(shutil.copystat)
''', encoding="utf-8")
    environment = {**os.environ, "PYTHONPATH": str(compatibility), "MPLCONFIGDIR": "/tmp/matplotlib"}
    # Foreground subprocess inherits the Jobs container lifecycle and cancellation.
    # Never introduce the old detached native subprocess executor.
    with open(output / "processing.log", "w", encoding="utf-8") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, env=environment)
    ortho = project / "odm_orthophoto" / "odm_orthophoto.tif"
    inspected = inspect_raster(ortho)
    if not inspected["crs"] or not inspected["width"] or not inspected["height"]:
        raise ValueError("ODM output has no valid georeferenced grid")
    # Only job-owned staging and reconstructible caches, after successful product validation.
    # Source grants and failed jobs are untouched; original files remain readonly.
    removed = []
    for name in ("images", "opensfm", "openmvs", "odm_filterpoints", "odm_meshing", "odm_texturing",
                 "odm_25dmeshing", "odm_25dtexturing", "odm_texturing_25d", "odm_georeferencing"):
        scratch = project / name
        if scratch.exists():
            if scratch.is_symlink() or not scratch.resolve().is_relative_to(project.resolve()):
                raise ValueError("Scratch directory escaped current job project")
            if scratch.is_dir():
                shutil.rmtree(scratch)
                removed.append(name)
    return {"orthophoto": str(ortho.relative_to(output)), "metadata": inspected,
            "declaration": declaration, "radiometric_calibration_verified": False, "removed_scratch": removed}


def main(request, output=Path("/output")):
    os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/numba")
    os.environ.setdefault("PROJ_NETWORK", "OFF")
    os.environ.setdefault("OMP_NUM_THREADS", str(request["cpu_threads"]))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "request.json", request)
    inputs, declaration = request["inputs"], request["declaration"]
    operation = request["operation"]
    if operation == "inspect":
        result = inspect_raster(inputs["raster"]["path"])
    elif operation == "build_lut":
        result = build_lut(declaration, output)
    elif operation == "invert":
        result = invert(inputs["raster"]["path"], inputs["lut"]["path"], declaration, output, request["cpu_threads"])
    else:
        result = orthomosaic(inputs, declaration, output, request["cpu_threads"], request.get("resume_job_id"))
    versions = {}
    for name in ("numpy", "scipy", "rasterio", "prosail"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    write_json(output / "result.json", {"operation": operation, "method_id": request["method_id"],
        "image": request["image"], "versions": versions, "result": result})
    print(json.dumps({"status": "completed", "result": "result.json"}))


if __name__ == "__main__":
    main(REQUEST)  # noqa: F821 -- supplied by recipe, never read from ambient input
