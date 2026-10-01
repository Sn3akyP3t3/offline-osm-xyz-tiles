# Offline OpenStreetMap XYZ Tiles

Build a static OpenStreetMap basemap as `{z}/{x}/{y}.png` files for web apps that cannot call a public tile server.

This is not a tile server and not a replacement for tilemaker or Planetiler. Those tools emit vector tiles. This workflow emits raster PNGs that IIS, nginx, Apache, or any static host can serve. Point Leaflet, OpenLayers, or a config key such as `BaseLayer` at:

```text
/tiles/{z}/{x}/{y}.png
```

Do not scrape the public OpenStreetMap tile servers. Their rules are here:

https://operations.osmfoundation.org/policies/tiles/

Download an extract from Geofabrik and render your own tiles.

## Why the pipeline is split

The work usually crosses three machines:

1. **Internet-connected workstation** — download and clip OSM data (Windows + Miniforge works; osmium lives in that environment).
2. **Linux host with Docker** — style the extract and write PNG tiles with `qgis/qgis:ltr`.
3. **Application host** — receive only the `tiles/` tree. No QGIS, no Docker, no Geofabrik access required.

Keep the steps separate. A single script that assumes one machine will fight air-gapped and approval-constrained environments.

Software required on each host is listed in `PREREQUISITES.md`.

## What's in the box

| Item | Role |
|------|------|
| `bbox_from_coordinates.py` | Bounding box (and optional tile-count estimate) from a list of lat/lon points |
| `download_geofabrik_extract.py` | Download a Geofabrik `.osm.pbf` and clip it with osmium |
| `create_qgis_project.py` | Headless QGIS project + baseline style (roads, buildings, water) |
| `generate_tiles.py` | Rebuild the project, convert extent to Web Mercator, write XYZ PNGs |
| `offline-osm-xyz-tiles.md` | Full step-by-step, gotchas, and command checklist |
| `PREREQUISITES.md` | Software required on each host |
| `project_config.json` | Created on first run if missing; paths, extent, style, zoom |

You still provide the runtime around the box: Miniforge + osmium on the download host, and Docker with `qgis/qgis:ltr` on the render host.

Examples use **Knoxville, Tennessee** as a public city-scale demo (`knoxville_area`). Swap the extract, bbox, and file names for your region.

## Quick path

On the download host (Miniforge Prompt on Windows). Use the equals form for `--bbox` when longitudes are negative:

```text
python bbox_from_coordinates.py points.csv --buffer-km 8 --min-zoom 10 --max-zoom 15
python download_geofabrik_extract.py tennessee --bbox=-84.04,35.87,-83.80,36.07 --output knoxville_area.osm.pbf --keep-full
```

On the Linux/Docker host:

```text
docker run --rm -e QT_QPA_PLATFORM=offscreen -v /path/to/data:/data qgis/qgis:ltr python3 /data/generate_tiles.py /data/project_config.json
```

Copy `TilesOutput/` to `tiles/` on the application host and set the basemap URL to `/tiles/{z}/{x}/{y}.png`.

Details, warnings to ignore, and style notes are in `offline-osm-xyz-tiles.md`.

## License and attribution

The scripts and documentation in this repository are licensed under the MIT License. See `LICENSE`.

OpenStreetMap data is © OpenStreetMap contributors and licensed under the Open Database License (ODbL). MIT on this code does not change that. Keep visible map attribution when you serve the tiles.
