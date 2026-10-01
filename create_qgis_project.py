#!/usr/bin/env python3
"""
create_qgis_project.py
======================
Create a minimal QGIS project (.qgz) from an OSM PBF file and apply a clean
baseline style (roads by importance, buildings, water; large landuse polygons
hidden).

All environment-specific paths and options are read from a JSON config file
so the script itself can be committed to source control without hardcoded
machine paths.

Usage (inside qgis/qgis:ltr container, headless):

  docker run --rm -e QT_QPA_PLATFORM=offscreen \\
    -v /path/to/data:/data \\
    qgis/qgis:ltr \\
    python3 /data/create_qgis_project.py /data/project_config.json

If the JSON file does not exist the script writes a template and exits.
Edit the template, then re-run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Template written when the config file is missing
# ---------------------------------------------------------------------------
CONFIG_TEMPLATE = {
    "input_pbf": "knoxville_area.osm.pbf",
    "output_project": "knoxville_area.qgz",
    "project_title": "Knoxville Area Basemap",
    "crs": "EPSG:3857",
    "extent": {
        "xmin": -84.04,
        "xmax": -83.80,
        "ymin": 35.87,
        "ymax": 36.07
    },
    "layers_to_add": [
        "multipolygons",
        "lines",
        "points"
    ],
    "style": {
        "hide_points": true,
        "road_colors": {
            "motorway": "#e892a2",
            "trunk": "#f9b29c",
            "primary": "#fcd6a4",
            "secondary": "#f7fabf",
            "tertiary": "#ffffff",
            "residential": "#ffffff",
            "other": "#d0d0d0"
        },
        "building_color": "#d9d0c9",
        "building_outline": "#c0b8b0",
        "water_color": "#aad3df",
        "background_note": "Large landuse polygons (farm, residential, etc.) are hidden by default. Points (POIs) are hidden by default."
    },
    "tiles": {
        "output_directory": "TilesOutput",
        "output_html": "TilesOutput/preview.html",
        "zoom_min": 10,
        "zoom_max": 15,
        "dpi": 96,
        "tile_format": "PNG"
    },
    "notes": [
        "Paths are relative to the directory that contains this JSON file unless absolute.",
        "extent is in WGS84 (EPSG:4326) lon/lat. Tile generation converts it to the project CRS.",
        "On the QGIS/Linux host run generate_tiles.py; it rebuilds the project and writes PNG tiles.",
        "create_qgis_project.py can still be run alone if you only want the .qgz."
    ]
}


def write_template(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(CONFIG_TEMPLATE, f, indent=2)
        f.write("\n")
    print(f"No config found. Wrote template to: {config_path}")
    print("Edit the file, then re-run this script.")


def load_config(config_path: Path) -> dict:
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


def resolve(base: Path, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _line_symbol(color: str, width: float):
    from qgis.core import QgsLineSymbol
    sym = QgsLineSymbol.createSimple({
        "color": color,
        "width": str(width),
        "capstyle": "round",
        "joinstyle": "round",
    })
    return sym


def _fill_symbol(fill: str, outline: str, outline_width: float = 0.2):
    from qgis.core import QgsFillSymbol
    sym = QgsFillSymbol.createSimple({
        "color": fill,
        "outline_color": outline,
        "outline_width": str(outline_width),
    })
    return sym


def style_lines(layer, style_cfg: dict) -> None:
    """Rule-based road styling roughly inspired by simple web maps."""
    from qgis.core import QgsRuleBasedRenderer

    colors = style_cfg.get("road_colors", {})
    motorway = colors.get("motorway", "#e892a2")
    trunk = colors.get("trunk", "#f9b29c")
    primary = colors.get("primary", "#fcd6a4")
    secondary = colors.get("secondary", "#f7fabf")
    tertiary = colors.get("tertiary", "#ffffff")
    residential = colors.get("residential", "#ffffff")
    other = colors.get("other", "#d0d0d0")

    root = QgsRuleBasedRenderer.Rule(None)

    rules = [
        ("Motorway", '"highway" IN (\'motorway\',\'motorway_link\')', motorway, 1.8),
        ("Trunk", '"highway" IN (\'trunk\',\'trunk_link\')', trunk, 1.5),
        ("Primary", '"highway" IN (\'primary\',\'primary_link\')', primary, 1.3),
        ("Secondary", '"highway" IN (\'secondary\',\'secondary_link\')', secondary, 1.1),
        ("Tertiary", '"highway" IN (\'tertiary\',\'tertiary_link\')', tertiary, 0.9),
        ("Residential / service", '"highway" IN (\'residential\',\'living_street\',\'service\',\'unclassified\')', residential, 0.7),
        ("Other roads", '"highway" IS NOT NULL', other, 0.5),
    ]

    for label, filt, color, width in rules:
        rule = QgsRuleBasedRenderer.Rule(_line_symbol(color, width))
        rule.setLabel(label)
        rule.setFilterExpression(filt)
        root.appendChild(rule)

    layer.setRenderer(QgsRuleBasedRenderer(root))
    layer.triggerRepaint()


def style_multipolygons(layer, style_cfg: dict) -> None:
    """Show buildings + water; hide large landuse blotches."""
    from qgis.core import QgsRuleBasedRenderer

    building_color = style_cfg.get("building_color", "#d9d0c9")
    building_outline = style_cfg.get("building_outline", "#c0b8b0")
    water_color = style_cfg.get("water_color", "#aad3df")

    root = QgsRuleBasedRenderer.Rule(None)

    # Buildings
    b_rule = QgsRuleBasedRenderer.Rule(
        _fill_symbol(building_color, building_outline, 0.15)
    )
    b_rule.setLabel("Buildings")
    b_rule.setFilterExpression("\"building\" IS NOT NULL AND \"building\" != ''")
    root.appendChild(b_rule)

    # Water
    w_rule = QgsRuleBasedRenderer.Rule(
        _fill_symbol(water_color, water_color, 0.1)
    )
    w_rule.setLabel("Water")
    w_rule.setFilterExpression(
        "\"natural\" = 'water' OR \"landuse\" = 'reservoir' OR "
        "\"landuse\" = 'basin' OR \"waterway\" IS NOT NULL"
    )
    root.appendChild(w_rule)

    # Everything else stays invisible (no symbol / filter that never matches)
    # By simply not adding a catch-all rule, other polygons are not drawn.

    layer.setRenderer(QgsRuleBasedRenderer(root))
    layer.triggerRepaint()


def style_points(layer, style_cfg: dict) -> None:
    from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer

    if style_cfg.get("hide_points", True):
        layer.setOpacity(0.0)
        return

    sym = QgsMarkerSymbol.createSimple({
        "name": "circle",
        "color": "#666666",
        "size": "1.2",
        "outline_color": "#444444",
        "outline_width": "0.1",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.triggerRepaint()


def wgs84_extent_to_project_rect(extent_cfg: dict, project_crs):
    """Convert JSON WGS84 extent to a QgsRectangle in the project CRS."""
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsCoordinateTransformContext,
        QgsRectangle,
    )

    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    rect = QgsRectangle(
        float(extent_cfg["xmin"]),
        float(extent_cfg["ymin"]),
        float(extent_cfg["xmax"]),
        float(extent_cfg["ymax"]),
    )
    xform = QgsCoordinateTransform(wgs84, project_crs, QgsCoordinateTransformContext())
    return xform.transformBoundingBox(rect)


def build_styled_project(cfg: dict, base_dir: Path):
    """
    Build and write the .qgz. QgsApplication must already be initialized.
    Returns (output_project_path, project_crs, project_extent_or_None).
    """
    from qgis.core import (
        QgsProject,
        QgsVectorLayer,
        QgsCoordinateReferenceSystem,
        QgsReferencedRectangle,
    )

    input_pbf = resolve(base_dir, cfg["input_pbf"])
    output_project = resolve(base_dir, cfg["output_project"])
    title = cfg.get("project_title", "OSM Basemap")
    crs_authid = cfg.get("crs", "EPSG:3857")
    layers_to_add = cfg.get("layers_to_add", ["multipolygons", "lines", "points"])
    extent_cfg = cfg.get("extent")
    style_cfg = cfg.get("style", {})

    if not input_pbf.is_file():
        raise FileNotFoundError(f"input PBF not found: {input_pbf}")

    project = QgsProject.instance()
    project.clear()
    project.setTitle(title)

    crs = QgsCoordinateReferenceSystem(crs_authid)
    if not crs.isValid():
        raise ValueError(f"invalid CRS '{crs_authid}'")
    project.setCrs(crs)

    order_preference = ["multipolygons", "lines", "points"]
    ordered = [n for n in order_preference if n in layers_to_add]
    ordered += [n for n in layers_to_add if n not in ordered]

    added = 0
    layers_by_name = {}
    for layer_name in ordered:
        uri = f"{input_pbf}|layername={layer_name}"
        layer = QgsVectorLayer(uri, layer_name, "ogr")
        if not layer.isValid():
            print(f"WARNING: could not load layer '{layer_name}' from {input_pbf}")
            continue
        project.addMapLayer(layer)
        layers_by_name[layer_name] = layer
        added += 1
        print(f"Added layer: {layer_name}  ({layer.featureCount()} features)")

    if added == 0:
        raise RuntimeError("no layers were added. Check the PBF and layers_to_add list.")

    if "multipolygons" in layers_by_name:
        style_multipolygons(layers_by_name["multipolygons"], style_cfg)
        print("Styled multipolygons (buildings + water only)")
    if "lines" in layers_by_name:
        style_lines(layers_by_name["lines"], style_cfg)
        print("Styled lines (roads by class)")
    if "points" in layers_by_name:
        style_points(layers_by_name["points"], style_cfg)
        print("Styled points")

    extent = None
    if extent_cfg:
        extent = wgs84_extent_to_project_rect(extent_cfg, crs)
        project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, crs))
        print(f"Set default view extent (project CRS): {extent.toString()}")

    output_project.parent.mkdir(parents=True, exist_ok=True)
    if not project.write(str(output_project)):
        raise RuntimeError(f"failed to write project to {output_project}")

    print(f"Project written: {output_project}")
    return output_project, crs, extent


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: create_qgis_project.py <path-to-project_config.json>")
        return 2

    config_path = Path(argv[1]).resolve()
    base_dir = config_path.parent

    if not config_path.is_file():
        write_template(config_path)
        return 0

    cfg = load_config(config_path)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from qgis.core import QgsApplication

    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        build_styled_project(cfg, base_dir)
        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
