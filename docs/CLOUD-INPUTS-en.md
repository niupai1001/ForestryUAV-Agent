# Cloud input inspection

Version 1.48 adds an offline operator CLI: select a LAS/LAZ file, inspect its header and bounded sample, retain separately sourced declarations, and export a new JSON record. It does not start services, models or Jobs, change Tasks/grants, or write to the source. Agent tools/UI integration and PDAL/lidR processing remain stage-two work.

```powershell
uv run --locked forestry-agent inspect-cloud --path C:/data/flight.las --output workspace/flight-inspection.json
uv run --locked forestry-agent inspect-cloud --path C:/data/flight.las --declared C:/data/declared.json --hash-file --output workspace/flight-inspection-with-source.json
```

Output must be a new file. Without `--output`, JSON goes to stdout. `--hash-file` streams the entire source for SHA256; otherwise identity is its size/stat version. Inspection reads bounded metadata and at most 4,096 first points. This is not a spatial support audit. LAZ currently receives header/metadata inspection only; no decompression or complete compressed-stream validation. Changed source versions, invalid headers/coordinates/bounds, excessive metadata and truncated uncompressed point records are rejected.

Optional declaration fields are `source_note` (required provenance), `horizontal_crs`, `vertical_ref`, `z_unit` (`m`, `ft`, `unknown`), `z_semantics` (`elevation`, `height_above_ground`, `unknown`), and `classification_semantics` (`unknown`, `las_standard`, `publisher_annotations`). JSON is capped at 32 KiB.

Observed file facts and assertions remain separate under `observed` and `declared`. Horizontal CRS comes from file VLR/EVLR records. Horizontal metre units do not establish Z units, vertical datum or height above ground. The current inspector retains unknown vertical metadata and lists declarations separately. CRS conflict does not trigger automatic replacement or reprojection. Extra dimensions remain visible with a warning about possible truth labels; classification frequencies describe only first points. They are not predicted ground support. Ground support and scientific runtime remain `not_assessed`.

The implementation uses mature [laspy header/CRS parsing](https://laspy.readthedocs.io/en/latest/api/laspy.header.html), with laspy 2.7.0 and pyproj 3.8.0 locked. It adds no LAS decoder or execution backend.

The actual CLI workflow used flight02 from the [poplar UAV dataset](https://doi.org/10.57745/WAEYEX), version 2.0, Etalab 2.0. Before inspection, the smallest complete LAS member was selected by bytes for bootstrap work. It is not a formal holdout or accuracy result. Publisher README declares EPSG:2154/EGM96. Its orthophoto products do not replace raw RGB photographs for ODM reconstruction.

The complete member has 39,311,550 bytes and 1,156,172 points, LAS 1.2 / point format 3. The file header confirms EPSG:2154 and metre XY axes. Its size and CRC32 `74a62fd1` match the archive directory; local complete SHA256 is `65a0df50e96628adec8add7036f0602e7b761d8ce6a0d77db8eaf5c2f67570a5`. Bounded HTTP ranges retrieved that member, without the full 13 GB archive; the archive MD5 was not verified and CRC is not a publisher cryptographic signature. Original bytes/ranges, README, flight metadata and prior selection are retained. Flight CSV reused the saved archive tail without another download.

Actual CLI output: `workspace/evidence/poplar-flight02-inspection-20261012-v1.json`. Acquisition: `poplar-flight02-acquired-20261012-v1.json`. The data is not granted to the Agent, executed for science products, or scored for accuracy.

Verification was limited to the new behavior: preserving unknowns, exposing CRS conflicts/truth dimensions, rejecting truncation, and bounded oversized-header handling, followed by one actual CLI inspection. Existing stage-one modules, model workflows, dependencies and UI were not replayed. Next: fixed PDAL/lidR runtime, existing Jobs integration, annotation isolation, ground/DTM/DSM/HAG processing and spatial support review. Input inspection does not complete scientific processing, raw RGB acquisition, species/allometry, domain UI or stage-three acceptance.
