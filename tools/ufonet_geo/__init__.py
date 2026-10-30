"""
ufonet_geo: the georeferencing core shared by this repo's tools and the
WaterCam API server.

  geometry  rotation, lens model, rays, flat ground, ENU <-> lat/lon (numpy only)
  pose      heading / pitch / roll / position from a unit config and EXIF
  terrain   DEM sampling and ray / terrain intersection
  ortho     a fixed camera's flood mask on a north-up ground grid

Install it from this repo (pip install . or, from another project, a pinned
archive URL, see README). Only numpy is required; the `dem` extra adds
rasterio and pyproj for terrain.sample_dem_grid().
"""

__version__ = "0.1.0"
