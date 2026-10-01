# Prerequisites

This workflow is three hosts on purpose. You do not install everything on one machine.

## Download host (internet-connected)

Used for `bbox_from_coordinates.py` and `download_geofabrik_extract.py`.

| Software | Why |
|----------|-----|
| Python 3.10+ | Runs the download and bbox scripts |
| Miniforge (or another conda-forge distribution) | Puts `osmium` on PATH without a separate Windows installer |
| `osmium-tool` | Clips the Geofabrik extract to your bounding box |

Install osmium from the Miniforge Prompt:

```text
conda install -c conda-forge osmium-tool
```

Run those two Python scripts from the **Miniforge Prompt**, not from ordinary PowerShell. Otherwise Windows will report that `osmium` is missing.

Optional on this host:

| Software | Why |
|----------|-----|
| `mercantile` | More accurate tile-count estimates in the bbox script |
| `shapely` | Faster convex-hull checks for odd point clouds |

```text
pip install mercantile shapely
```

Neither is required to compute a usable bounding box.

## Render host (Linux + Docker)

Used for `generate_tiles.py` and, if you want the project file alone, `create_qgis_project.py`.

| Software | Why |
|----------|-----|
| Docker | Runs QGIS headless without a local QGIS install |
| `qgis/qgis:ltr` | Official QGIS Long Term Release image; provides PyQGIS and `native:tilesxyzdirectory` |

Pull the image once:

```text
docker pull qgis/qgis:ltr
```

Tile generation must set `QT_QPA_PLATFORM=offscreen`. The documented `docker run` line already does that.

## Application host

Used only to serve the finished PNG files.

| Software | Why |
|----------|-----|
| Any static web server | IIS, nginx, Apache, or the static-file support already on the site |

No Python, Miniforge, osmium, Docker, or QGIS on this host. Copy the `tiles/{z}/{x}/{y}.png` tree and point the map client at `/tiles/{z}/{x}/{y}.png`.

## What you do not install

- A public OSM tile scraper or a “download tiles from openstreetmap.org” tool. Use Geofabrik extracts. Tile usage policy: https://operations.osmfoundation.org/policies/tiles/
- tilemaker, Planetiler, or TileServer GL. Those are a different (vector) path.
- QGIS Desktop. Useful if you want to inspect a `.qgz`, not required to generate tiles.

Command-level gotchas (`--bbox=` on Windows, Web Mercator extent, ignored QGIS warnings) are in `offline-osm-xyz-tiles.md`.
