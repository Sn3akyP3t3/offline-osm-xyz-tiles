#!/usr/bin/env python3
"""
bbox_from_coordinates.py
========================
Compute a robust geographic bounding box (and optional tile-count estimate)
from a large set of latitude/longitude points for offline OSM tile generation.

Designed for maps where you may have hundreds or thousands of points that
form irregular shapes (clusters, stars, corridors, etc.).

Features
--------
* Accepts CSV, simple text (lat,lon per line), or JSON list of [lat, lon]
* Simple axis-aligned bounding box (AABB) – always correct and simple
* Optional outlier removal (percentile or IQR) so a few bad GPS points do not
  inflate the box
* Convex-hull area vs AABB area ratio (quantifies “empty space”)
* Geographic buffer in kilometres (great-circle aware)
* Tile count estimation for chosen zoom range (uses mercantile if available,
  otherwise a pure-Python approximation)
* Outputs ready-to-use values for Geofabrik clipping, osmium, tilemaker, etc.

Zero hard dependencies.  Optional: mercantile (better tile counts), shapely
(faster/more robust convex hull).

Usage examples
--------------
  # From CSV with columns named latitude,longitude (or lat,lon, y,x …)
  python bbox_from_coordinates.py points.csv --buffer-km 12 --min-zoom 10 --max-zoom 15

  # From a plain text file (one "lat,lon" per line)
  python bbox_from_coordinates.py points.txt --buffer-km 8 --outlier-method percentile --percentile 1

  # Quick test with inline points
  python bbox_from_coordinates.py --points "35.96,-83.92 35.97,-83.91 35.95,-83.93"

Author: utility for offline OSM raster tile generation
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]  # (lat, lon)


# ---------------------------------------------------------------------------
# Geometry helpers (pure Python)
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def expand_bbox_by_km(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float, buffer_km: float
) -> Tuple[float, float, float, float]:
    """
    Expand an axis-aligned box by approximately buffer_km on all sides.
    Uses a simple latitude-dependent longitude correction.
    """
    if buffer_km <= 0:
        return min_lat, min_lon, max_lat, max_lon

    # 1 degree latitude ≈ 111.32 km
    dlat = buffer_km / 111.32

    # Longitude degrees per km depends on latitude
    mid_lat = (min_lat + max_lat) / 2.0
    cos_lat = max(math.cos(math.radians(mid_lat)), 0.01)  # avoid div-by-zero near poles
    dlon = buffer_km / (111.32 * cos_lat)

    return (
        min_lat - dlat,
        min_lon - dlon,
        max_lat + dlat,
        max_lon + dlon,
    )


def cross(o: Point, a: Point, b: Point) -> float:
    """2-D cross product (lon/lat treated as Cartesian for hull)."""
    return (a[1] - o[1]) * (b[0] - o[0]) - (a[0] - o[0]) * (b[1] - o[1])


def convex_hull(points: Sequence[Point]) -> List[Point]:
    """
    Monotone-chain convex hull.
    Points are (lat, lon).  Returns hull in counter-clockwise order.
    """
    pts = sorted(set(points))  # unique & sorted by lat then lon
    if len(pts) <= 1:
        return list(pts)

    lower: List[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: List[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def polygon_area_km2(hull: Sequence[Point]) -> float:
    """
    Approximate area of a lat/lon polygon in km² using the shoelace formula
    with a local equirectangular projection.
    Good enough for comparing hull vs AABB; not for cadastral precision.
    """
    if len(hull) < 3:
        return 0.0
    mid_lat = sum(p[0] for p in hull) / len(hull)
    cos_lat = math.cos(math.radians(mid_lat))
    # convert to approximate km
    pts = [(p[1] * 111.32 * cos_lat, p[0] * 111.32) for p in hull]
    area = 0.0
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return abs(area) / 2.0


# ---------------------------------------------------------------------------
# Outlier filtering
# ---------------------------------------------------------------------------

def filter_outliers_percentile(
    points: Sequence[Point], low: float = 1.0, high: float = 99.0
) -> List[Point]:
    """Keep points whose lat and lon both fall inside the given percentiles."""
    if not points or low >= high:
        return list(points)
    lats = sorted(p[0] for p in points)
    lons = sorted(p[1] for p in points)
    n = len(points)
    lat_lo = lats[max(0, int(n * low / 100.0))]
    lat_hi = lats[min(n - 1, int(n * high / 100.0))]
    lon_lo = lons[max(0, int(n * low / 100.0))]
    lon_hi = lons[min(n - 1, int(n * high / 100.0))]
    return [p for p in points if lat_lo <= p[0] <= lat_hi and lon_lo <= p[1] <= lon_hi]


def filter_outliers_iqr(points: Sequence[Point], k: float = 1.5) -> List[Point]:
    """Tukey IQR fence on both latitude and longitude."""
    if len(points) < 4:
        return list(points)
    lats = sorted(p[0] for p in points)
    lons = sorted(p[1] for p in points)
    n = len(points)

    def fences(vals: List[float]) -> Tuple[float, float]:
        q1 = vals[n // 4]
        q3 = vals[(3 * n) // 4]
        iqr = q3 - q1
        return q1 - k * iqr, q3 + k * iqr

    lat_lo, lat_hi = fences(lats)
    lon_lo, lon_hi = fences(lons)
    return [p for p in points if lat_lo <= p[0] <= lat_hi and lon_lo <= p[1] <= lon_hi]


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def load_points_from_csv(path: Path, lat_col: Optional[str] = None, lon_col: Optional[str] = None) -> List[Point]:
    points: List[Point] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"No header row found in {path}")

        # Auto-detect column names
        fields_lower = {name.lower().strip(): name for name in reader.fieldnames}
        lat_candidates = ["latitude", "lat", "y", "meterlatitude", "lat_dd"]
        lon_candidates = ["longitude", "lon", "lng", "long", "x", "meterlongitude", "lon_dd"]

        if lat_col:
            lat_key = lat_col
        else:
            lat_key = next((fields_lower[c] for c in lat_candidates if c in fields_lower), None)
        if lon_col:
            lon_key = lon_col
        else:
            lon_key = next((fields_lower[c] for c in lon_candidates if c in fields_lower), None)

        if not lat_key or not lon_key:
            raise ValueError(
                f"Could not find lat/lon columns in {path}. "
                f"Columns present: {reader.fieldnames}. "
                f"Use --lat-col / --lon-col to specify."
            )

        for i, row in enumerate(reader, start=2):
            try:
                lat = float(row[lat_key].strip())
                lon = float(row[lon_key].strip())
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                points.append((lat, lon))
            except (ValueError, TypeError, KeyError):
                continue
    return points


def load_points_from_text(path: Path) -> List[Point]:
    """One 'lat,lon' or 'lat lon' pair per line.  Comments (#) and blank lines ignored."""
    points: List[Point] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        points.append((lat, lon))
                except ValueError:
                    continue
    return points


def load_points_from_json(path: Path) -> List[Point]:
    data = json.loads(path.read_text(encoding="utf-8"))
    points: List[Point] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                lat, lon = float(item[0]), float(item[1])
                points.append((lat, lon))
            elif isinstance(item, dict):
                lat = item.get("lat") or item.get("latitude")
                lon = item.get("lon") or item.get("lng") or item.get("longitude")
                if lat is not None and lon is not None:
                    points.append((float(lat), float(lon)))
    return points


def parse_inline_points(text: str) -> List[Point]:
    points: List[Point] = []
    for token in text.replace(";", " ").split():
        token = token.strip()
        if "," in token:
            a, b = token.split(",", 1)
            try:
                points.append((float(a), float(b)))
            except ValueError:
                continue
    return points


# ---------------------------------------------------------------------------
# Tile estimation
# ---------------------------------------------------------------------------

def estimate_tiles_pure(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                        min_zoom: int, max_zoom: int) -> dict:
    """Approximate tile counts without mercantile (good enough for planning)."""
    results = {}
    total = 0
    for z in range(min_zoom, max_zoom + 1):
        n = 2 ** z
        # Web Mercator tile indices
        def lon_to_x(lon):
            return int((lon + 180.0) / 360.0 * n)
        def lat_to_y(lat):
            lat = max(min(lat, 85.05112878), -85.05112878)
            rad = math.radians(lat)
            y = (1.0 - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi) / 2.0
            return int(y * n)

        x_min = lon_to_x(min_lon)
        x_max = lon_to_x(max_lon)
        y_min = lat_to_y(max_lat)  # north → smaller y
        y_max = lat_to_y(min_lat)
        # clamp
        x_min = max(0, min(n - 1, x_min))
        x_max = max(0, min(n - 1, x_max))
        y_min = max(0, min(n - 1, y_min))
        y_max = max(0, min(n - 1, y_max))
        count = (x_max - x_min + 1) * (y_max - y_min + 1)
        results[z] = count
        total += count
    results["total"] = total
    return results


def estimate_tiles_mercantile(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                              min_zoom: int, max_zoom: int) -> dict:
    import mercantile
    results = {}
    total = 0
    for z in range(min_zoom, max_zoom + 1):
        tiles = list(mercantile.tiles(min_lon, min_lat, max_lon, max_lat, zooms=z))
        count = len(tiles)
        results[z] = count
        total += count
    results["total"] = total
    return results


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def compute_bbox(
    points: Sequence[Point],
    buffer_km: float = 10.0,
    outlier_method: str = "none",
    percentile: float = 1.0,
) -> dict:
    if not points:
        raise ValueError("No valid coordinates supplied")

    original_count = len(points)

    if outlier_method == "percentile":
        filtered = filter_outliers_percentile(points, low=percentile, high=100.0 - percentile)
    elif outlier_method == "iqr":
        filtered = filter_outliers_iqr(points)
    else:
        filtered = list(points)

    if not filtered:
        raise ValueError("All points were removed by outlier filter – relax the settings")

    lats = [p[0] for p in filtered]
    lons = [p[1] for p in filtered]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    # Convex hull diagnostics
    hull = convex_hull(filtered)
    hull_area = polygon_area_km2(hull)
    # AABB area (approx)
    mid_lat = (min_lat + max_lat) / 2.0
    width_km = haversine_km(mid_lat, min_lon, mid_lat, max_lon)
    height_km = haversine_km(min_lat, min_lon, max_lat, min_lon)
    aabb_area = width_km * height_km
    empty_ratio = 1.0 - (hull_area / aabb_area) if aabb_area > 0 else 0.0

    # Apply buffer
    b_min_lat, b_min_lon, b_max_lat, b_max_lon = expand_bbox_by_km(
        min_lat, min_lon, max_lat, max_lon, buffer_km
    )

    return {
        "original_count": original_count,
        "used_count": len(filtered),
        "outliers_removed": original_count - len(filtered),
        "raw": {
            "min_lat": min_lat,
            "min_lon": min_lon,
            "max_lat": max_lat,
            "max_lon": max_lon,
        },
        "buffered": {
            "min_lat": b_min_lat,
            "min_lon": b_min_lon,
            "max_lat": b_max_lat,
            "max_lon": b_max_lon,
        },
        "width_km": width_km,
        "height_km": height_km,
        "aabb_area_km2": aabb_area,
        "hull_area_km2": hull_area,
        "approx_empty_fraction": empty_ratio,
        "hull_points": len(hull),
        "buffer_km": buffer_km,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute a robust lat/lon bounding box from many coordinates "
                    "for offline OSM tile generation (PQ Dashboard / utility maps).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Input file: CSV, .txt (lat,lon per line), or .json",
    )
    parser.add_argument(
        "--points",
        help='Inline points as "lat,lon lat,lon ..." (useful for quick tests)',
    )
    parser.add_argument("--lat-col", help="CSV latitude column name (auto-detected if omitted)")
    parser.add_argument("--lon-col", help="CSV longitude column name (auto-detected if omitted)")
    parser.add_argument(
        "--buffer-km",
        type=float,
        default=10.0,
        help="Buffer distance in kilometres added on all sides (default: 10)",
    )
    parser.add_argument(
        "--outlier-method",
        choices=["none", "percentile", "iqr"],
        default="none",
        help="How to handle extreme outliers (default: none)",
    )
    parser.add_argument(
        "--percentile",
        type=float,
        default=1.0,
        help="For --outlier-method percentile: trim this %% from each tail (default: 1)",
    )
    parser.add_argument("--min-zoom", type=int, default=10, help="Min zoom for tile estimate")
    parser.add_argument("--max-zoom", type=int, default=15, help="Max zoom for tile estimate")
    parser.add_argument(
        "--json-out",
        help="Write full result as JSON to this file",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final buffered bbox line (machine-friendly)",
    )

    args = parser.parse_args(argv)

    # Load points
    points: List[Point] = []
    if args.points:
        points = parse_inline_points(args.points)
    elif args.input:
        path = Path(args.input)
        if not path.exists():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            return 1
        suffix = path.suffix.lower()
        if suffix == ".csv":
            points = load_points_from_csv(path, args.lat_col, args.lon_col)
        elif suffix in {".json", ".geojson"}:
            points = load_points_from_json(path)
        else:
            points = load_points_from_text(path)
    else:
        parser.error("Provide either an input file or --points")

    if not points:
        print("ERROR: no valid coordinates found", file=sys.stderr)
        return 1

    result = compute_bbox(
        points,
        buffer_km=args.buffer_km,
        outlier_method=args.outlier_method,
        percentile=args.percentile,
    )

    b = result["buffered"]

    # Tile estimate
    try:
        tile_counts = estimate_tiles_mercantile(
            b["min_lon"], b["min_lat"], b["max_lon"], b["max_lat"],
            args.min_zoom, args.max_zoom,
        )
        tile_source = "mercantile"
    except ImportError:
        tile_counts = estimate_tiles_pure(
            b["min_lon"], b["min_lat"], b["max_lon"], b["max_lat"],
            args.min_zoom, args.max_zoom,
        )
        tile_source = "pure-python approximation"

    if args.quiet:
        print(f"{b['min_lon']:.6f},{b['min_lat']:.6f},{b['max_lon']:.6f},{b['max_lat']:.6f}")
        return 0

    # Human-readable report
    print("=" * 60)
    print("Bounding-box report for offline OSM tiles")
    print("=" * 60)
    print(f"Points loaded          : {result['original_count']:,}")
    print(f"Points used            : {result['used_count']:,}")
    if result["outliers_removed"]:
        print(f"Outliers removed       : {result['outliers_removed']:,}  ({args.outlier_method})")
    print()
    print("Raw extent (no buffer)")
    r = result["raw"]
    print(f"  min_lat, min_lon     : {r['min_lat']:.6f}, {r['min_lon']:.6f}")
    print(f"  max_lat, max_lon     : {r['max_lat']:.6f}, {r['max_lon']:.6f}")
    print(f"  approx size          : {result['width_km']:.1f} km × {result['height_km']:.1f} km")
    print(f"  AABB area            : {result['aabb_area_km2']:.1f} km²")
    print(f"  Convex-hull area     : {result['hull_area_km2']:.1f} km²  ({result['hull_points']} vertices)")
    print(f"  Approx empty fraction: {result['approx_empty_fraction']*100:.0f}%  "
          f"(higher = more sparse / star-like / corridor)")
    print()
    print(f"Buffered extent  (+{args.buffer_km:g} km on each side)")
    print(f"  min_lon, min_lat     : {b['min_lon']:.6f}, {b['min_lat']:.6f}")
    print(f"  max_lon, max_lat     : {b['max_lon']:.6f}, {b['max_lat']:.6f}")
    print()
    print("Copy-paste values")
    print(f"  bbox (lon_min,lat_min,lon_max,lat_max) = "
          f"{b['min_lon']:.6f},{b['min_lat']:.6f},{b['max_lon']:.6f},{b['max_lat']:.6f}")
    print(f"  osmium/osmconvert    : -b={b['min_lon']:.6f},{b['min_lat']:.6f},{b['max_lon']:.6f},{b['max_lat']:.6f}")
    print()
    print(f"Tile count estimate ({tile_source})  zooms {args.min_zoom}–{args.max_zoom}")
    for z in range(args.min_zoom, args.max_zoom + 1):
        print(f"  z{z:2d}: {tile_counts[z]:>10,} tiles")
    print(f"  TOTAL: {tile_counts['total']:>10,} tiles")
    # rough size hint for raster
    est_mb_low = tile_counts["total"] * 15 / 1024
    est_mb_high = tile_counts["total"] * 40 / 1024
    print(f"  Rough raster size    : ~{est_mb_low:.0f} – {est_mb_high:.0f} MB "
          f"(PNG, depends on style & density)")
    print("=" * 60)

    if args.json_out:
        out = {
            **result,
            "tile_counts": tile_counts,
            "min_zoom": args.min_zoom,
            "max_zoom": args.max_zoom,
        }
        # hull_points is already a count; drop the actual coordinate list if huge
        Path(args.json_out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Full JSON written to {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
