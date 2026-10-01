# Offline OpenStreetMap XYZ Tiles

This document describes how to generate a local raster tileset from OpenStreetMap data for applications that cannot reach the public internet. The result is a static folder of PNG files in slippy-map layout:

```text
tiles/{z}/{x}/{y}.png
```

Many web maps (Leaflet, OpenLayers, and similar) accept that pattern as a basemap URL. The default public source is:

```text
https://tile.openstreetmap.org/{z}/{x}/{y}.png
```

That URL is not available on an air-gapped host. Do not scrape the public OSM tile servers. Download an extract from Geofabrik and render your own tiles instead.

The examples below use **Knoxville, Tennessee** as a city-scale demonstration area. Replace the extract name, bounding box, and file names for your own region.

---

## Outcome

- Local PNG tiles: `tiles/{z}/{x}/{y}.png`
- A basemap URL template your application can point at, for example:

```text
/tiles/{z}/{x}/{y}.png
```

If the application stores that template in a config file (including an ASP.NET `Web.config`), change only the tile URL. Leave any optional ArcGIS service fields empty if you are not using an ArcGIS map service.

---

## Tools used

| Role | Tool | Where it runs |
|------|------|----------------|
| Bounding box from coordinates | `bbox_from_coordinates.py` | Internet-connected workstation (Miniforge Prompt on Windows) |
| Download + clip OSM extract | `download_geofabrik_extract.py` + osmium | Same workstation |
| OSM extract source | Geofabrik (free, intended for bulk download) | Internet-connected workstation |
| Rebuild project + write PNG tiles | `generate_tiles.py` | Docker: `qgis/qgis:ltr` on Linux |
| Optional: project file only | `create_qgis_project.py` | Docker: `qgis/qgis:ltr` |
| Serve tiles | Any static web server (IIS, nginx, Apache, etc.) | Application host |

Related utilities:

- `bbox_from_coordinates.py`
- `download_geofabrik_extract.py`
- `create_qgis_project.py`
- `generate_tiles.py`
- `project_config.json` (created if missing)

The pipeline is split on purpose. Download needs the internet. Rendering is convenient in Linux Docker. The locked-down application host should receive only static PNG files.

---

## Important conventions

### Bounding box order

Most tools in this pipeline use:

```text
min_lon, min_lat, max_lon, max_lat
```

City-scale Knoxville example (approximate public city extent, not a site-specific box):

```text
-84.04,35.87,-83.80,36.07
```

QGIS tile generation uses a different order **and** expects the project CRS (Web Mercator meters), not lon/lat:

```text
xmin,xmax,ymin,ymax
```

`generate_tiles.py` performs that conversion. You do not need to do it by hand.

### Negative longitudes and argparse

On Windows / PowerShell, a leading minus sign is treated as a new flag. Always use the equals form:

```text
--bbox=-84.04,35.87,-83.80,36.07
```

Do not use:

```text
--bbox -84.04,...
```

### Run Windows OSM tools from Miniforge Prompt

`osmium` is installed in the Miniforge environment. Ordinary PowerShell will report that osmium is missing. Always run `bbox_from_coordinates.py` and `download_geofabrik_extract.py` from **Miniforge Prompt**.

### Keep the full state extract while experimenting

Use `--keep-full` so the Tennessee (or other region) file is not deleted after clipping. Re-downloading the same Geofabrik extract many times a day is unnecessary.

---

## Step 1. Decide geographic extent and zoom levels

1. Collect the latitude/longitude points that must appear on the map.
2. Compute a bounding box that covers all points, with a small buffer.
3. Choose zoom levels. For a city-scale proof of concept, **10–15** is a practical start.

Notes:

- A simple min/max of all coordinates is usually enough.
- Outliers (bad coordinates, a single distant point) can inflate the box. The bounding-box utility can drop outliers if needed.
- Tile count grows quickly with zoom. Zoom 18 over a large area produces a very large file set.

---

## Step 2. Compute the bounding box

From Miniforge Prompt, run `bbox_from_coordinates.py` against the coordinate list (CSV, JSON, or inline points).

Record the printed bbox in this form:

```text
min_lon,min_lat,max_lon,max_lat
```

Knoxville city example:

```text
-84.04,35.87,-83.80,36.07
```

Map those values into `project_config.json` as:

```json
"extent": {
  "xmin": -84.04,
  "xmax": -83.80,
  "ymin": 35.87,
  "ymax": 36.07
}
```

---

## Step 3. Download a Geofabrik extract and clip it

Install osmium in Miniforge if it is not already present:

```text
conda install -c conda-forge osmium-tool
```

From Miniforge Prompt (one line):

```text
python download_geofabrik_extract.py tennessee --bbox=-84.04,35.87,-83.80,36.07 --output knoxville_area.osm.pbf --keep-full
```

Results:

- Full state extract kept on disk (for reuse)
- Clipped PBF: `knoxville_area.osm.pbf`

---

## Step 4. Rebuild the project and generate PNG tiles (Linux / Docker)

Copy these files into the Linux working directory next to the clipped PBF:

```text
create_qgis_project.py
generate_tiles.py
project_config.json
knoxville_area.osm.pbf
```

`project_config.json` holds paths, WGS84 extent, style, and tile options (`zoom_min`, `zoom_max`, `output_directory`). If the JSON is missing, `generate_tiles.py` writes a template and exits.

One command rebuilds the styled project, converts the extent to the project CRS, and runs QGIS `native:tilesxyzdirectory`:

```text
docker run --rm -e QT_QPA_PLATFORM=offscreen -v /path/to/data:/data qgis/qgis:ltr python3 /data/generate_tiles.py /data/project_config.json
```

What that command does:

- Loads OSM layers from the PBF (`multipolygons`, `lines`, `points`)
- Forces draw order: multipolygons (bottom), lines, points (top)
- Applies the baseline style (classified roads, buildings, water; large landuse polygons and POI points hidden)
- Converts the JSON WGS84 extent into the project CRS
- Writes `{z}/{x}/{y}.png` under `TilesOutput/` (or whatever `tiles.output_directory` says)

`create_qgis_project.py` still exists if you only want the `.qgz`. You do not need to run it before `generate_tiles.py`.

Expected output folder:

```text
TilesOutput/
  10/
  11/
  ...
  15/
  preview.html
```

### Warnings that can be ignored

- `File <extent> could not be found` — QGIS sometimes treats the extent string as a path. If tiles are written, ignore it.
- `Non closed ring detected` — common with OSM multipolygons. A few polygons may be skipped.
- `featureCount() = -1` when creating the project — OGR often cannot count OSM PBF features without a full scan.

### Tile count

A city-scale box at zoom 10–15 may only produce tens of tiles. That is normal for a small area. Count grows quickly if you raise max zoom or enlarge the box.

### preview.html

Opening `preview.html` as a `file://` URL on Windows often shows a blank map because browsers restrict local tile loads. That does not mean the PNGs are empty. Open individual PNG files to inspect content, or serve the folder over HTTP.

---

## Step 5. Publish the tile folder

For a small prototype set, keep the individual PNG files.

Recommended layout:

```text
tiles/
  10/
  11/
  ...
  15/
```

Point the map client at a single template:

```text
/tiles/{z}/{x}/{y}.png
```

On the web host:

1. Place the `tiles` folder where the site can serve it at that URL.
2. Confirm a tile opens in the browser, for example:
   `http://<server>/tiles/15/<x>/<y>.png`
3. If that URL 404s, fix the static path before changing application config.

A zip/tar archive is better later if the set grows to thousands of files. Git LFS is another option if the organization supports it.

---

## Step 6. Point the application at the local tiles

Wherever the app stores its basemap URL, replace the public OSM template with a local one.

Leaflet-style example:

```javascript
L.tileLayer('/tiles/{z}/{x}/{y}.png', {
  maxZoom: 15,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);
```

Generic config-file example (ASP.NET and similar):

```xml
<add name="BaseLayer" value="/tiles/{z}/{x}/{y}.png" />
```

If the application lives in a virtual directory, include that prefix:

```text
/MyApp/tiles/{z}/{x}/{y}.png
```

Then recycle or restart the site and hard-refresh the browser so the old public tile URL is not cached.

Overlay markers (assets, sites, meters, and so on) still come from the application database. Only the basemap images are local files.

OpenStreetMap data is licensed under the Open Database License (ODbL). Keep visible attribution on the map.

---

## Style customization

`project_config.json` controls colors and whether points are shown. Baseline defaults:

- `hide_points`: `true` (OSM POIs are usually noise under your own markers)
- Road colors by highway class
- Building fill / outline
- Water fill

After changing JSON, re-run `generate_tiles.py`. It rebuilds the project first, so style changes are not left behind.

To refine further, open the `.qgz` in QGIS Desktop (LTR recommended so it matches `qgis/qgis:ltr`), save, copy the project back, and regenerate tiles.

---

## Approaches considered and not used for the first prototype

| Approach | Why not used for the first prototype |
|----------|--------------------------------------|
| TileServer GL + crawl to PNG | Extra long-running service; good later option |
| Serve vector `.mbtiles` directly | Many existing apps expect raster XYZ PNGs |
| tilemaker only | Produces vector MBTiles, not the PNG folder a static site can serve |
| Docker on the locked-down application host | New executables often need security review |

Vector MBTiles from tilemaker remain a useful intermediate if a later option (TileServer GL on another host, or a frontend that can consume vector tiles) is approved.

---

## Quick command checklist

All Docker commands assume the working directory is mounted at `/data`.

1. Bounding box (Miniforge Prompt): run `bbox_from_coordinates.py`
2. Download + clip (Miniforge Prompt):

```text
python download_geofabrik_extract.py tennessee --bbox=-84.04,35.87,-83.80,36.07 --output knoxville_area.osm.pbf --keep-full
```

3. Rebuild project and generate tiles (Linux / Docker):

```text
docker run --rm -e QT_QPA_PLATFORM=offscreen -v /path/to/data:/data qgis/qgis:ltr python3 /data/generate_tiles.py /data/project_config.json
```

4. Copy `TilesOutput` contents into `tiles/`, deploy, and point the app at `/tiles/{z}/{x}/{y}.png`.
