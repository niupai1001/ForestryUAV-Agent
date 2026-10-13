# Explicit ROI geometry postprocessing only: no PDAL or lidR algorithm call.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 4L, args[1] == "--request", args[3] == "--result")
request <- jsonlite::fromJSON(args[2], simplifyVector = FALSE)
suppressPackageStartupMessages(library(sf))
suppressPackageStartupMessages(library(terra))
ring_matrix <- function(ring) do.call(rbind, lapply(ring, function(point) as.numeric(unlist(point))))
polygon_rings <- function(polygon) lapply(polygon, ring_matrix)
definition <- request$roi
geometry <- if (definition$geometry$type == "Polygon") {
  sf::st_polygon(polygon_rings(definition$geometry$coordinates))
} else sf::st_multipolygon(lapply(definition$geometry$coordinates, polygon_rings))
roi <- sf::st_sfc(geometry, crs = definition$horizontal_crs)
stopifnot(!sf::st_is_longlat(roi), all(sf::st_is_valid(roi)), !any(sf::st_is_empty(roi)))
chm <- terra::rast(request$chm_path)
stopifnot(sf::st_crs(roi) == sf::st_crs(terra::crs(chm)), terra::ncell(chm) <= 1000000,
          all(terra::res(chm) == .5))
pixel_area <- prod(terra::res(chm))
fraction <- numeric(terra::ncell(chm))
bbox <- sf::st_bbox(roi)
# Bound each geometry-overlay batch; retain exact polygon/pixel area, not centres.
for (start in seq.int(1L, terra::ncell(chm), by = 4096L)) {
  ids <- seq.int(start, min(start + 4095L, terra::ncell(chm)))
  xy <- terra::xyFromCell(chm, ids)
  eligible <- xy[,1]+.25 >= bbox["xmin"] & xy[,1]-.25 <= bbox["xmax"] &
              xy[,2]+.25 >= bbox["ymin"] & xy[,2]-.25 <= bbox["ymax"]
  ids <- ids[eligible]
  xy <- xy[eligible,,drop=FALSE]
  if (!length(ids)) next
  boxes <- lapply(seq_len(length(ids)), function(i) {
    x <- xy[i,1]; y <- xy[i,2]
    sf::st_polygon(list(rbind(c(x-.25,y-.25),c(x+.25,y-.25),c(x+.25,y+.25),c(x-.25,y+.25),c(x-.25,y-.25))))
  })
  cells <- sf::st_sf(cell_id=ids,geometry=sf::st_sfc(boxes,crs=sf::st_crs(roi)))
  clipped <- suppressWarnings(sf::st_intersection(cells,roi))
  if (nrow(clipped)) {
    areas <- tapply(as.numeric(sf::st_area(clipped)),clipped$cell_id,sum)
    fraction[as.integer(names(areas))] <- pmin(1,pmax(0,areas/pixel_area))
  }
}
heights <- terra::values(chm,mat=FALSE)
valid <- is.finite(heights)
weights <- fraction
weights[!valid] <- 0
weighted <- chm
terra::values(weighted) <- weights
terra::writeRaster(weighted,"roi-valid-fraction.tif",datatype="FLT8S",NAflag=-9999)
full <- chm
terra::values(full) <- fraction
terra::writeRaster(full,"roi-fraction.tif",datatype="FLT8S",NAflag=-9999)
visible <- chm
terra::values(visible)[weights<=0] <- NA_real_
terra::writeRaster(visible,"roi-chm.tif",datatype="FLT4S",NAflag=-9999)
sf::st_write(sf::st_sf(source_note=definition$source_note,geometry=roi),"roi.gpkg",layer="analysis_roi",quiet=TRUE)

tops <- sf::st_read(request$vectors_path,layer="treetops",quiet=TRUE)
stopifnot(sf::st_crs(tops)==sf::st_crs(roi),!anyDuplicated(tops$treeID),!anyDuplicated(tops$tree_uid))
table <- read.csv(request$trees_path,stringsAsFactors=FALSE)
stopifnot(identical(as.integer(table$treeID),as.integer(tops$treeID)),identical(table$tree_uid,tops$tree_uid))
inside <- lengths(sf::st_covered_by(tops,roi))>0
included <- inside & table$accepted_instance
accepted_ids <- tops$treeID[included]
roi_area <- as.numeric(sf::st_area(roi))
valid_area <- sum(weights)*pixel_area
crowns <- NULL
if (nrow(tops)) {
  crowns <- sf::st_read(request$vectors_path,layer="crowns",quiet=TRUE)
  crowns <- crowns[crowns$treeID %in% accepted_ids,]
  crowns <- suppressWarnings(sf::st_intersection(crowns,roi))
  if (nrow(crowns)) {
    sf::st_write(crowns,"roi-trees.gpkg",layer="crowns",quiet=TRUE)
  }
}
sf::st_write(tops[included,],"roi-trees.gpkg",layer="treetops",quiet=TRUE)
area_by_tree <- if (!is.null(crowns) && nrow(crowns)) tapply(as.numeric(sf::st_area(crowns)),crowns$treeID,sum) else numeric(0)
table$parent_crown_area_m2 <- table$crown_area_m2
table$roi_apex_included <- inside
table$accepted_instance <- included
table$crown_area_m2 <- as.numeric(area_by_tree[as.character(table$treeID)])
table$crown_area_m2[is.na(table$crown_area_m2)] <- 0
table$roi_clipped <- included & (table$crown_area_m2 < table$parent_crown_area_m2-1e-9)
table$crown_clipped <- table$crown_clipped | table$roi_clipped
table$crown_eqdiam_m <- 2*sqrt(table$crown_area_m2/pi)
write.csv(table,"trees.csv",row.names=FALSE)

points <- read.csv("assigned-points.csv")
if (nrow(points)) {
  geometry <- sf::st_as_sf(points,coords=c("X","Y"),crs=sf::st_crs(roi))
  keep <- lengths(sf::st_covered_by(geometry,roi))>0 & points$treeID %in% accepted_ids
} else keep <- logical(0)
write.csv(data.frame(SourcePointId=points$SourcePointId,treeID=ifelse(keep,points$treeID,0L)),"roi-point-labels.csv",row.names=FALSE)

result <- list(status="partial",method_id=request$method_id,sf=as.character(packageVersion("sf")),
               geos=unname(sf::sf_extSoftVersion()["GEOS"]),height_rule_threshold_m=2,
               terra=as.character(packageVersion("terra")),roi_area_m2=roi_area,valid_area_m2=valid_area,
               excluded_support_area_m2=max(0,roi_area-valid_area),partial_roi_cells=sum(fraction>0 & fraction<1),
               detected_instances=if(valid_area>0)sum(included) else NULL,
               density_ha=if(valid_area>0)sum(included)/(valid_area/10000) else NULL,
               canopy_height_rule_pct=if(valid_area>0)100*sum(weights[which(valid & heights>=2)])*pixel_area/valid_area else NULL,
               assigned_points=sum(keep),roi_clipped_crowns=sum(table$roi_clipped),
               boundary_policy="GEOS closed polygon; apex and point boundary included; area weights exact",
               overlay_batch_cells=4096L,independent_accuracy="not_assessed")
jsonlite::write_json(result,args[4],auto_unbox=TRUE,pretty=TRUE,null="null",digits=NA)
