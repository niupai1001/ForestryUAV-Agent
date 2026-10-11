# Original UAV cloud to terrain and HAG

Version 1.49 adds `lidar.smrf_idw.v1`: granted original LAS and sourced declarations, existing persistent Jobs, actual PDAL classification/terrain/support/HAG, a separate normalized R working cloud, and a Chinese partial-delivery report. Generic Python keeps its original image. Only an operator-configured immutable scientific image is frozen into new Tasks. There is no second executor, automatic installation, or Agent web search.

The same original flight02 source produced aligned 1 m DTM/DSM, true within-cell ground counts, nearest-ground distances, support mask, DSM−DTM, HAG LAS retaining raw original Z, and a working LAS with Z=valid HAG, OriginalZ and SourcePointId. Original classifications/oracle extras were isolated. Delivery remains partial: single-tree processing, independent accuracy and stage two are incomplete.

Configure `science_image` alongside the existing generic `code_image`, then start current source. The tested local image is `sha256:eae1217fee43492a6b837fcdac35f493b790dcb1b8a7aa3dd51990d64114a6a8`: PDAL 2.9.3, Python 3.12.15, R 4.4.3, lidR 4.2.3. Actual resolved packages are locked in `config/science/conda-linux-64.lock.txt`; micromamba 2.9.0-0 publisher SHA256 is `366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3`. Reuse the current image. Locked rebuild instructions are provided by `config/science/Dockerfile`; rebuild/package distribution remains stage-three work. Main service 8769 was not reloaded/configured this turn; delivery used the actual application API and existing restricted Jobs with separate persisted state. Domain UI integration remains.

Register the original cloud and declaration JSON as readonly resources, include both in a new Task, and optionally request `lidar.smrf_idw.v1`. Unknown/infeasible methods are rejected rather than replaced. Tasks without a frozen science image cannot gain it implicitly.

```powershell
uv run --locked forestry-agent lidar-terrain --config config/app.science.example.yaml --task TASK_ID --cloud-ref CLOUD_RESOURCE_ID --declaration-ref DECLARATION_RESOURCE_ID --revision 1 --request-id terrain-one
```

Observe the returned durable job_id through existing Jobs/logs/artifact APIs or workbench. The Task is not auto-accepted. Scientific Tasks expose `process_lidar_terrain` followed by existing `wait_job`. Equivalent Task/revision/code/image requests return the original Job/producer, including failure, without silent retry. Concrete code/input/method changes establish a new identity.

Current scope is an entire-flight bootstrap: at most five million points and one million 1 m grid cells, uncompressed LAS only. File XY must be projected metres; declarations must identify elevation, metre Z, vertical datum and no CRS conflict. Unknown or already-normalized inputs fail. Valid recorded return number/count is required; no fabricated returns or automatic thinning. Original classifications/extras are isolated; withheld points are excluded. A fresh statistical outlier recipe (`mean_k=8`, `multiplier=3`) identifies noise 7 before SMRF and excludes it from DSM. SMRF uses `last,only`, cell 1, scalar 1.25, slope .15, threshold .5, window 18. These are bootstrap values, not proven optimal parameters.

DTM uses 1 m IDW, √2 radius and no hole filling; DSM uses the same grid and within-cell max. Support requires at least one new ground point in the cell, nearest ground to its centre ≤√2 m, and finite DTM. Raw DTM/diagnostics remain available; unsupported terrain/CHM are NoData −9999. DSM is not a canopy classification. HAG retains original Z, uses NoData −9999 and `zero_ground=false`; negative residuals remain. A separate working cloud changes Z semantics to valid non-noise HAG while retaining OriginalZ/SourcePointId. Options follow official [SMRF](https://pdal.io/en/stable/stages/filters.smrf.html), [GDAL writer](https://pdal.io/en/stable/stages/writers.gdal.html), and [HAG DEM](https://pdal.io/en/stable/stages/filters.hag_dem.html) documentation.

Actual failures and repairs are retained. The first Job failed before classification on a laspy scalar assignment. Array assignment/data-path fixes enabled the original Task's actual computation. After surfaces/HAG were written, LAS 1.2 Extra Bytes were ignored by default, losing SourcePointId. New inputs use LAS 1.4 and readers explicitly use `use_eb_vlr=true`. Existing results were reused, with full point-order checks repairing identity. Four retained points verified only corrected transport; no SMRF/terrain/HAG replay. Seven raster products remained byte-identical.

Recovery requires `resume_job_id` and all `resume_refs` mapping names to explicitly registered current inputs. Only matching published products of that Task's failed Job/same image are accepted; original source/declaration/method and hashes are preserved. Other granted files cannot substitute for these products. Required names are `lidar_terrain.RESUME_FILES`.

```powershell
uv run --locked forestry-agent lidar-terrain --config config/app.science.example.yaml --task TASK_ID --cloud-ref CLOUD_RESOURCE_ID --declaration-ref DECLARATION_RESOURCE_ID --revision 2 --request-id terrain-postprocess --resume-job FAILED_JOB_ID --resume-refs workspace/authorized-resume-refs.json
```

Independent review also found OriginalZ differed by 5.6843×10⁻¹⁴ m due to PDAL forwarded-header rounding. The criterion was not weakened: only metadata was repaired by copying actual original elevations using source IDs; working HAG Z and calculations stayed unchanged. The original strict mismatch remains; only that item was rechecked and passed.

Actual Task `67ac3172-547b-449f-82d1-28d437a31e83` has 1,073,175 newly classified ground points, 2,380 supported cells of a 71×56 rectangular flight grid, and 1,134,887 valid non-noise HAG points. That denominator is not a surveyed plot ROI; residuals are internal diagnostics. `workspace/evidence/lidar-terrain-final-20261012-v1.json` binds failures/calculation/reuse/metadata correction/latest working cloud. It is not first-attempt success or model validation; model requests were zero. Actual containers retain network none, nonroot, readonly root/input, all capabilities dropped, 6 GiB and 128 PID limits.

Next: actual lidR CHM/detection/segmentation using the final working cloud, IDs/unassigned semantics, retained R/PROJ startup diagnostics, then tiling/boundary, supported metrics, delivery validation and the same UI. Raw RGB/ODM, independent science accuracy and stage-three formal evaluation/scale/release remain incomplete.

Input-version correction: equivalent scientific requests reject changed source mount versions and require an explicit Task revision. The existing Job is retained without automatic recalculation. The captured package lock is preserved; config/science/Dockerfile is a future rebuild recipe, not a verified release build.
