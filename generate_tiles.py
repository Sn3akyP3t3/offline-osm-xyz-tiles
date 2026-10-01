#!/usr/bin/env python3
"""
generate_tiles.py
=================
QGIS/Linux-host step: rebuild the styled .qgz from project_config.json,
convert the WGS84 extent to the project CRS, and write XYZ PNG tiles.

This replaces the previous manual sequence:
  create_qgis_project.py
  convert lon/lat to EPSG:3857 by hand
  qgis_process run native:tilesxyzdirectory ...

It does NOT download OSM data. That stays on the internet-connected
Windows host (download_geofabrik_extract.py).

Usage (inside qgis/qgis:ltr, one line):

  docker run --rm -e QT_QPA_PLATFORM=offscreen \\
    -v /path/to/data:/data \\
    qgis/qgis:ltr \\
    python3 /data/generate_tiles.py /data/project_config.json

If the JSON file does not exist a template is written and the script exits.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _enable_processing_plugin() -> bool:
    """Put QGIS's Processing plugin on sys.path.

    qgis/qgis:ltr installs qgis.core where python3 can import it, but
    Processing lives in /usr/share/qgis/python/plugins and is not on the
    default path. qgis_process adds that path itself; a bare python3 does not.
    """
    candidates: list[Path] = []
    prefix = os.environ.get("QGIS_PREFIX_PATH", "").strip()
    if prefix:
        candidates.append(Path(prefix) / "share" / "qgis" / "python" / "plugins")
    candidates.append(Path("/usr/share/qgis/python/plugins"))
    for path in candidates:
        if (path / "processing").is_dir():
            entry = str(path)
            if entry not in sys.path:
                sys.path.insert(0, entry)
            return True
    print(
        "ERROR: QGIS Processing plugin not found. "
        "Expected /usr/share/qgis/python/plugins inside qgis/qgis:ltr."
    )
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: generate_tiles.py <path-to-project_config.json>")
        return 2

    # Allow "python3 /data/generate_tiles.py /data/project_config.json"
    # when both scripts live in the same mounted folder.
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    from create_qgis_project import (
        load_config,
        resolve,
        write_template,
        build_styled_project,
        wgs84_extent_to_project_rect,
    )

    config_path = Path(argv[1]).resolve()
    base_dir = config_path.parent

    if not config_path.is_file():
        write_template(config_path)
        print("No config found. Wrote template. Edit it, then re-run generate_tiles.py.")
        return 0

    cfg = load_config(config_path)
    extent_cfg = cfg.get("extent")
    if not extent_cfg:
        print("ERROR: project_config.json is missing the extent block.")
        return 1

    tiles_cfg = cfg.get("tiles", {})
    output_dir = resolve(base_dir, tiles_cfg.get("output_directory", "TilesOutput"))
    output_html = resolve(base_dir, tiles_cfg.get("output_html", str(output_dir / "preview.html")))
    zoom_min = int(tiles_cfg.get("zoom_min", 10))
    zoom_max = int(tiles_cfg.get("zoom_max", 15))
    dpi = int(tiles_cfg.get("dpi", 96))
    tile_format = str(tiles_cfg.get("tile_format", "PNG")).upper()
    tile_format_code = 0 if tile_format == "PNG" else 1

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QGIS_PREFIX_PATH", "/usr")

    from qgis.core import QgsApplication, QgsCoordinateReferenceSystem

    QgsApplication.setPrefixPath(os.environ["QGIS_PREFIX_PATH"], True)
    if not _enable_processing_plugin():
        return 1

    qgs = QgsApplication([], False)
    qgs.initQgis()

    from processing.core.Processing import Processing
    import processing

    Processing.initialize()

    try:
        print("=== Rebuilding styled project ===")
        project_path, project_crs, extent = build_styled_project(cfg, base_dir)
        if extent is None:
            extent = wgs84_extent_to_project_rect(
                extent_cfg,
                project_crs or QgsCoordinateReferenceSystem(cfg.get("crs", "EPSG:3857")),
            )

        # native:tilesxyzdirectory wants xmin,xmax,ymin,ymax
        extent_str = f"{extent.xMinimum()},{extent.xMaximum()},{extent.yMinimum()},{extent.yMaximum()}"
        print(f"Project CRS extent (xmin,xmax,ymin,ymax): {extent_str}")
        print(f"Zoom {zoom_min}–{zoom_max}  format={tile_format}  dpi={dpi}")
        print(f"Output directory: {output_dir}")

        output_dir.mkdir(parents=True, exist_ok=True)
        output_html.parent.mkdir(parents=True, exist_ok=True)

        print("=== Generating XYZ tiles ===")
        result = processing.run(
            "native:tilesxyzdirectory",
            {
                "EXTENT": extent_str,
                "ZOOM_MIN": zoom_min,
                "ZOOM_MAX": zoom_max,
                "DPI": dpi,
                "TILE_FORMAT": tile_format_code,
                "OUTPUT_DIRECTORY": str(output_dir),
                "OUTPUT_HTML": str(output_html),
            },
        )

        print("=== Done ===")
        print(f"Tiles: {result.get('OUTPUT_DIRECTORY', output_dir)}")
        print(f"Preview HTML: {result.get('OUTPUT_HTML', output_html)}")
        print(f"Project: {project_path}")
        print("Copy the numbered zoom folders into a tiles/ directory your web server can serve.")
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
