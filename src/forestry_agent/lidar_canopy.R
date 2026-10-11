# Registered file protocol. This script runs only within the existing restricted Jobs.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 4L, args[1] == "--request", args[3] == "--result")
request <- jsonlite::fromJSON(args[2], simplifyVector = FALSE)
params <- request$parameters
# sf establishes its PROJ search context before terra/lidR initialization.
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(terra))
suppressPackageStartupMessages(library(lidR))
stopifnot(as.character(packageVersion("lidR")) == params$lidr_version)
options(lidR.progress = FALSE, lidR.raster.default = "terra")
lidR::set_lidr_threads(4L)
path <- function(role) request$inputs[[role]]$path
result <- list(stage = request$stage, method_id = request$method_id,
               R = R.version.string, lidR = as.character(packageVersion("lidR")),
               sf = as.character(packageVersion("sf")), terra = as.character(packageVersion("terra")),
               proj_search_path = sf::sf_proj_info("path"))
write_raster <- function(r, name, datatype = "FLT4S") {
  terra::writeRaster(r, name, overwrite = FALSE, datatype = datatype,
                     NAflag = if (datatype == "INT4S") -2147483648 else if (datatype == "INT1U") 255 else -9999)
}
load_chm <- function() {
  chm <- terra::rast(path("chm"))
  # The small whole-flight path is explicitly bounded; load this raster once.
  terra::values(chm) <- terra::values(chm)
  stopifnot(all(terra::res(chm) == params$chm_resolution_m))
  chm
}
if (request$stage == "chm") {
  las <- lidR::readLAS(path("working"))
  stopifnot(!lidR::is.empty(las), "SourcePointId" %in% names(las@data),
            "OriginalZ" %in% names(las@data))
  support <- terra::rast(path("terrain_support"))
  stopifnot(sf::st_crs(las) == sf::st_crs(terra::crs(support)))
  # Layout comes from the accepted terrain grid; it is not inferred separately.
  layout <- terra::rast(terra::ext(support), resolution = params$chm_resolution_m,
                        crs = terra::crs(support))
  chm <- lidR::rasterize_canopy(las, res = layout,
                               algorithm = lidR::p2r(subcircle = params$p2r_subcircle_m, na.fill = NULL))
  stopifnot(inherits(chm, "SpatRaster"), terra::compareGeom(chm, layout, stopOnError = FALSE))
  write_raster(chm, "chm-p2r-raw.tif")
  xy <- terra::xyFromCell(layout, seq_len(terra::ncell(layout)))
  terrain_ok <- terra::extract(support, xy)[, 1] == 1
  terrain_ok[is.na(terrain_ok)] <- FALSE
  sampled <- is.finite(terra::values(chm, mat = FALSE))
  joint <- terrain_ok & sampled
  terra::values(chm)[!joint] <- NA_real_
  write_raster(chm, "chm-p2r.tif")
  mask <- layout
  terra::values(mask) <- as.integer(joint)
  write_raster(mask, "canopy-support.tif", "INT1U")
  result$grid <- list(extent = as.vector(terra::ext(chm)), nrow = terra::nrow(chm),
                      ncol = terra::ncol(chm), resolution_m = terra::res(chm), crs = terra::crs(chm))
  result$sampled_cells <- sum(sampled)
  result$supported_cells <- sum(joint)
  result$valid_area_m2 <- sum(joint) * params$chm_resolution_m^2
  result$unsupported_cells <- sum(!joint)
  result$holes_filled <- FALSE
} else if (request$stage == "seeds") {
  chm <- load_chm()
  raw <- lidR::locate_trees(chm, lidR::lmf(ws = params$lmf_window_m,
                                          hmin = params$tree_min_height_m, shape = "circular"))
  sf::st_write(raw, "temporary-seeds.gpkg", layer = "temporary_seeds", quiet = TRUE)
  cells <- terra::cellFromXY(chm, sf::st_coordinates(raw)[, 1:2, drop = FALSE])
  heights <- terra::values(chm, mat = FALSE)[cells]
  valid <- is.finite(heights) & heights >= params$tree_min_height_m
  raw <- raw[valid, ]
  cells <- cells[valid]
  heights <- heights[valid]
  xy <- sf::st_coordinates(raw)[, 1:2, drop = FALSE]
  ordering <- order(cells, -heights, xy[, 1], xy[, 2])
  raw <- raw[ordering, ]
  cells <- cells[ordering]
  heights <- heights[ordering]
  keep <- !duplicated(cells)
  seeds <- raw[keep, ]
  cells <- cells[keep]
  heights <- heights[keep]
  stopifnot(nrow(seeds) <= .Machine$integer.max)
  seeds$treeID <- as.integer(seq_len(nrow(seeds)))
  seeds$Z <- heights
  seeds$tree_uid <- paste0(request$seed_namespace, ":cell:", cells)
  seeds$cell_index <- cells
  sf::st_write(seeds, "accepted-seeds.gpkg", layer = "accepted_seeds", quiet = TRUE)
  xy <- sf::st_coordinates(seeds)[, 1:2, drop = FALSE]
  mapping <- data.frame(treeID = seeds$treeID, tree_uid = seeds$tree_uid,
                        cell_index = cells, x = xy[, 1], y = xy[, 2], apex_height_m = heights)
  write.csv(mapping, "tree-id-mapping.csv", row.names = FALSE)
  reread <- sf::st_read("accepted-seeds.gpkg", layer = "accepted_seeds", quiet = TRUE)
  stopifnot(identical(as.integer(reread$treeID), seeds$treeID), identical(reread$tree_uid, seeds$tree_uid))
  result$temporary_seeds <- nrow(raw)
  result$accepted_seeds <- nrow(seeds)
  result$coordination <- "whole supported flight; one seed per raster cell; stable cell/height/XY order"
  result$id_roundtrip <- TRUE
} else if (request$stage == "instances") {
  chm <- load_chm()
  seeds <- sf::st_read(path("seeds"), layer = "accepted_seeds", quiet = TRUE)
  stopifnot(sf::st_crs(seeds) == sf::st_crs(terra::crs(chm)),
            all(seeds$treeID > 0), all(seeds$treeID <= .Machine$integer.max),
            !anyDuplicated(seeds$treeID), !anyDuplicated(seeds$tree_uid))
  algorithm <- lidR::dalponte2016(chm, seeds, th_tree = params$tree_min_height_m,
                                 th_seed = params$th_seed, th_cr = params$th_cr,
                                 max_cr = params$max_cr_pixels, ID = "treeID")
  # Evaluate Dalponte exactly once. Reuse its crown raster for point assignment.
  crowns <- algorithm()
  stopifnot(inherits(crowns, "SpatRaster"))
  crowns[is.na(chm)] <- NA
  write_raster(crowns, "crown-labels.tif", "INT4S")
  las <- lidR::readLAS(path("working"))
  cached <- function(bbox) crowns
  class(cached) <- class(algorithm)
  segments <- lidR::segment_trees(las, cached, attribute = "treeID")
  ids <- segments@data$treeID
  ids[is.na(ids) | segments@data$Z < params$tree_min_height_m] <- 0L
  stopifnot(all(ids == floor(ids)), all(ids %in% c(0L, seeds$treeID)))
  write.csv(data.frame(SourcePointId = segments@data$SourcePointId, treeID = as.integer(ids)),
             "point-labels.csv", row.names = FALSE)
  values <- terra::values(crowns, mat = FALSE)
  positive <- !is.na(values) & values > 0
  present <- sort(unique(values[positive]))
  counts <- table(factor(values[positive], levels = seeds$treeID))
  # Mark crowns touching missing support or the grid edge as clipped estimates.
  grid <- matrix(values, nrow = terra::nrow(chm), ncol = terra::ncol(chm), byrow = TRUE)
  missing <- matrix(is.na(terra::values(chm, mat = FALSE)), nrow = nrow(grid), ncol = ncol(grid), byrow = TRUE)
  edge <- missing
  edge[c(1, nrow(edge)), ] <- TRUE
  edge[, c(1, ncol(edge))] <- TRUE
  if (nrow(edge) > 1) {
    edge[-1, ] <- edge[-1, ] | missing[-nrow(edge), ]
    edge[-nrow(edge), ] <- edge[-nrow(edge), ] | missing[-1, ]
  }
  if (ncol(edge) > 1) {
    edge[, -1] <- edge[, -1] | missing[, -ncol(edge)]
    edge[, -ncol(edge)] <- edge[, -ncol(edge)] | missing[, -1]
  }
  clipped <- unique(grid[edge & !is.na(grid) & grid > 0])
  trees <- data.frame(treeID = seeds$treeID, tree_uid = seeds$tree_uid,
                      apex_height_m = seeds$Z, crown_area_m2 = as.numeric(counts) * params$chm_resolution_m^2,
                      crown_clipped = seeds$treeID %in% clipped, accepted_instance = seeds$treeID %in% present)
  trees$crown_eqdiam_m <- 2 * sqrt(trees$crown_area_m2 / pi)
  write.csv(trees, "trees.csv", row.names = FALSE)
  sf::st_write(seeds, "trees.gpkg", layer = "treetops", quiet = TRUE)
  polygons <- terra::as.polygons(crowns, aggregate = TRUE, values = TRUE, na.rm = TRUE)
  if (nrow(polygons)) {
    polygons <- sf::st_as_sf(polygons)
    names(polygons)[1] <- "treeID"
    polygons$tree_uid <- seeds$tree_uid[match(polygons$treeID, seeds$treeID)]
    sf::st_write(polygons, "trees.gpkg", layer = "crowns", quiet = TRUE)
  }
  reread <- sf::st_read("trees.gpkg", layer = "treetops", quiet = TRUE)
  stopifnot(identical(as.integer(reread$treeID), as.integer(seeds$treeID)),
            identical(reread$tree_uid, seeds$tree_uid))
  result$detected_instances <- length(present)
  result$accepted_seeds <- nrow(seeds)
  result$assigned_working_points <- sum(ids > 0)
  result$unassigned_working_points <- sum(ids == 0)
  result$crown_cells <- sum(positive)
  result$id_roundtrip <- TRUE
  result$dalponte_evaluations <- 1L
} else stop("unregistered stage")
jsonlite::write_json(result, args[4], auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA)
