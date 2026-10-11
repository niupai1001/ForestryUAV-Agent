# Working cloud to canopy-instance delivery

Version 1.50 reuses the real flight02 PDAL terrain/HAG products and corrected OriginalZ working cloud. Independent persistent Jobs produce a 0.5m unfilled p2r CHM, coordinated LMF seeds, Dalponte instances, original-elevation segmented LAZ, GPKG treetops/crowns, exact integer ID/text UID mapping, metrics and a Chinese partial report. No terrain calculation or model experiment was repeated.

The real delivery has 48 canopy candidates on 2317.5m² joint sampled/terrain support. The main LAZ preserves all 1,156,172 source points and original Z: 38,025 assigned points and 1,118,147 unassigned points with treeID=0. This is an operational canopy estimate, not a complete tree census or independent field accuracy.

Use the frozen [scientific configuration](LIDAR-TERRAIN-en.md), explicitly grant files to the Task, and select method `lidar.p2r_lmf_dalponte.v1`. New CLI `lidar-canopy --task TASK_ID --stage chm --inputs INPUT_MAP.json --revision REVISION --request-id REQUEST_ID --config CONFIG.yaml` posts to `/tasks/TASK_ID/lidar/canopy`. Native `process_lidar_canopy` uses the same protocol and existing Jobs.

| Stage | Exact granted input roles |
|---|---|
| chm | working, terrain_quality, terrain_support |
| seeds | chm, chm_record |
| instances | working, original, hag, chm, chm_record, seeds, seed_record |

Input maps contain resource IDs, not paths. Record roles reference the upstream stage-result.json. Explicitly register completed products and save the next Task input revision. Observe retained Jobs and reuse successful upstream products. Equivalent same-revision scientific recipes return the original Job, including failures; changed source versions require explicit revision. There is no automatic Task acceptance or automatic recalculation of completed stages.

The registered YAML pins lidR 4.2.3, p2r(subcircle=0,na.fill=NULL), a 5m circular LMF window and 2m minimum height. Dalponte uses 2/.45/.55 thresholds and converts a 10m maximum diameter to 20 pixels. See the primary [lidR manual](https://r-lidar.r-universe.dev/lidR/doc/manual.html). Missing cells remain missing; DSM−DTM and p2r CHM retain separate identities. Seed ordering is deterministic by grid cell, height and XY, within positive int32 range. UIDs bind source/CHM identity and cell. Accepted GPKG layer names are explicit.

Dalponte is evaluated once and its saved raster supplies both point labels and crown areas. Points below 2m or outside accepted crown support remain unassigned. The full source inventory is exported separately with original elevation, HAG, HAGValid, source point index and treeID. LAZ is reread for exact IDs and original Z. Tables/vectors are checked against their ID/UID mapping. Four-neighbour missing-support/grid-edge contact flags clipped crown estimates. Zero analysis support yields unavailable metrics, not zero density. Upstream fingerprints prevent product substitution across cloud/CHM/seeds or runtime/recipe versions.

Three initial pre-computation failures are retained: opaque mount filenames, symlink resolution losing the extension, and unsupported chmod on the Windows bind volume. Temporary container copies fixed the file entry; the successful CHM was computed only once and then frozen. sf establishes the PROJ context before terra/lidR. Residual codetools startup diagnostics are preserved; the image was not rebuilt.

Independent readback verifies only the newly generated source elevation/point inventory, IDs, support/alignment and area/count/density consistency. Saved rasters supply PNG previews; scientific stages are not rerun. The workbench product view shows previews, metrics, limitations and downloads. This operator delivery made zero model requests and does not establish first-attempt automatic success.

The current whole-flight path is bounded to 5M points and 1M half-metre CHM cells. ROI fractional support, buffered partitions and cross-tile IDs, >=100GB processing, field-instance accuracy, original RGB/ODM and stage-3 release/evaluation remain open. Stage 2 and the full V1 goal are not complete.
