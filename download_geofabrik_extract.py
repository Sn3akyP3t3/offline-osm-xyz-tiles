#!/usr/bin/env python3
"""
download_geofabrik_extract.py
=============================
Download a free OpenStreetMap extract from Geofabrik (the standard reputable
source) and optionally clip it to a bounding box with osmium or osmconvert
if those tools are available on PATH.

This is the second step after you have computed a good bounding box with
bbox_from_coordinates.py.

IMPORTANT NOTES (Windows + Miniforge)
-------------------------------------
* Always run this script from the **Miniforge Prompt**.
  osmium is installed inside the Miniforge environment and is not on the
  normal system PATH.  If you run from ordinary PowerShell/CMD you will
  see the warning "neither 'osmium' nor 'osmconvert' found on PATH".

* When the first number of the bbox is negative (western longitudes),
  you MUST use the equals-sign form so argparse does not treat the
  minus sign as another flag:

      --bbox=-84.04,35.87,-83.80,36.07

  The space-separated form  --bbox -84.07,...  will fail.

* While you are experimenting, keep the full state extract with
  --keep-full.  Re-downloading the same file many times a day is
  unnecessary and the full extract is useful for trying different
  bounding boxes.

Geofabrik policy & notes
------------------------
* https://download.geofabrik.de/ is free and explicitly intended for bulk
  downloads of OSM data (unlike the public tile servers).
* Files are updated daily.
* Prefer the smallest extract that still covers your area (state > country >
  continent) to keep download size and later processing time down.
* After download you should clip to your exact bbox so tile generation only
  works on the needed data.

Usage examples (run from Miniforge Prompt)
------------------------------------------
  # List common US extracts
  python download_geofabrik_extract.py --list-us

  # Recommended while experimenting – keep the full state file
  python download_geofabrik_extract.py tennessee --bbox=-84.04,35.87,-83.80,36.07 --output knoxville_area.osm.pbf --keep-full

  # Same command with a different region
  python download_geofabrik_extract.py minnesota --bbox=-93.5,44.8,-92.9,45.2 --output my_city.osm.pbf --keep-full

Zero hard Python dependencies (only stdlib).  Clipping requires the external
tools osmium-tool or osmconvert to be installed and on PATH
(use Miniforge Prompt after  conda install -c conda-forge osmium-tool ).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional, Sequence, Tuple

GEOFABRIK_BASE = "https://download.geofabrik.de"

# A few convenient shortcuts (path relative to download.geofabrik.de)
COMMON_US = {
    "alabama": "north-america/us/alabama",
    "alaska": "north-america/us/alaska",
    "arizona": "north-america/us/arizona",
    "arkansas": "north-america/us/arkansas",
    "california": "north-america/us/california",
    "colorado": "north-america/us/colorado",
    "connecticut": "north-america/us/connecticut",
    "delaware": "north-america/us/delaware",
    "district-of-columbia": "north-america/us/district-of-columbia",
    "florida": "north-america/us/florida",
    "georgia": "north-america/us/georgia",
    "hawaii": "north-america/us/hawaii",
    "idaho": "north-america/us/idaho",
    "illinois": "north-america/us/illinois",
    "indiana": "north-america/us/indiana",
    "iowa": "north-america/us/iowa",
    "kansas": "north-america/us/kansas",
    "kentucky": "north-america/us/kentucky",
    "louisiana": "north-america/us/louisiana",
    "maine": "north-america/us/maine",
    "maryland": "north-america/us/maryland",
    "massachusetts": "north-america/us/massachusetts",
    "michigan": "north-america/us/michigan",
    "minnesota": "north-america/us/minnesota",
    "mississippi": "north-america/us/mississippi",
    "missouri": "north-america/us/missouri",
    "montana": "north-america/us/montana",
    "nebraska": "north-america/us/nebraska",
    "nevada": "north-america/us/nevada",
    "new-hampshire": "north-america/us/new-hampshire",
    "new-jersey": "north-america/us/new-jersey",
    "new-mexico": "north-america/us/new-mexico",
    "new-york": "north-america/us/new-york",
    "north-carolina": "north-america/us/north-carolina",
    "north-dakota": "north-america/us/north-dakota",
    "ohio": "north-america/us/ohio",
    "oklahoma": "north-america/us/oklahoma",
    "oregon": "north-america/us/oregon",
    "pennsylvania": "north-america/us/pennsylvania",
    "rhode-island": "north-america/us/rhode-island",
    "south-carolina": "north-america/us/south-carolina",
    "south-dakota": "north-america/us/south-dakota",
    "tennessee": "north-america/us/tennessee",
    "texas": "north-america/us/texas",
    "utah": "north-america/us/utah",
    "vermont": "north-america/us/vermont",
    "virginia": "north-america/us/virginia",
    "washington": "north-america/us/washington",
    "west-virginia": "north-america/us/west-virginia",
    "wisconsin": "north-america/us/wisconsin",
    "wyoming": "north-america/us/wyoming",
    "us": "north-america/us",
    "north-america": "north-america",
}


def resolve_path(name: str) -> str:
    """Turn a short name or partial path into the Geofabrik relative path."""
    key = name.strip().lower().replace(" ", "-")
    if key in COMMON_US:
        return COMMON_US[key]
    # already a path?
    if "/" in name:
        return name.strip().lstrip("/")
    # try prepending north-america/us/
    return f"north-america/us/{key}"


def download_file(url: str, dest: Path, show_progress: bool = True) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading:\n  {url}\n  → {dest}")

    def reporthook(block_num, block_size, total_size):
        if not show_progress or total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = min(100.0, downloaded * 100.0 / total_size)
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        sys.stdout.write(f"\r  {pct:5.1f}%  ({mb:.1f} / {total_mb:.1f} MB)")
        sys.stdout.flush()

    try:
        urllib.request.urlretrieve(url, dest, reporthook=reporthook if show_progress else None)
        if show_progress:
            print()
    except Exception as exc:
        if dest.exists():
            dest.unlink()
        raise RuntimeError(f"Download failed: {exc}") from exc

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"Finished: {dest}  ({size_mb:.1f} MB)")


def find_clip_tool() -> Optional[Tuple[str, str]]:
    """Return (tool_name, executable) if osmium or osmconvert is on PATH."""
    if shutil.which("osmium"):
        return "osmium", "osmium"
    if shutil.which("osmconvert"):
        return "osmconvert", "osmconvert"
    if shutil.which("osmconvert64"):
        return "osmconvert", "osmconvert64"
    return None


def clip_pbf(
    input_pbf: Path,
    output_pbf: Path,
    bbox: str,
    tool: Tuple[str, str],
) -> None:
    """
    bbox format: min_lon,min_lat,max_lon,max_lat
    """
    name, exe = tool
    output_pbf.parent.mkdir(parents=True, exist_ok=True)
    print(f"Clipping with {name} to bbox {bbox} …")

    if name == "osmium":
        # osmium extract -b min_lon,min_lat,max_lon,max_lat
        cmd = [
            exe, "extract",
            "-b", bbox,
            "-o", str(output_pbf),
            "--overwrite",
            str(input_pbf),
        ]
    else:
        # osmconvert file.pbf -b=min_lon,min_lat,max_lon,max_lat -o=out.pbf
        cmd = [
            exe,
            str(input_pbf),
            f"-b={bbox}",
            f"-o={output_pbf}",
        ]

    print("  " + " ".join(cmd))
    subprocess.check_call(cmd)
    size_mb = output_pbf.stat().st_size / (1024 * 1024)
    print(f"Clipped file: {output_pbf}  ({size_mb:.1f} MB)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download a Geofabrik OSM extract and optionally clip it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "region",
        nargs="?",
        help="Region name or path, e.g. 'tennessee', 'us/tennessee', "
             "'north-america/us/tennessee', 'germany/bayern'",
    )
    parser.add_argument(
        "--list-us",
        action="store_true",
        help="Print common US state extract names and exit",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory to store the downloaded file (default: current dir)",
    )
    parser.add_argument(
        "--bbox",
        help="Optional clip bbox: min_lon,min_lat,max_lon,max_lat.  "
             "IMPORTANT: when the first number is negative use the form "
             "--bbox=-84.07,35.84,-83.76,36.07  (equals sign, no space).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Final output path for the (possibly clipped) PBF. "
             "Default: <region>-latest.osm.pbf or clipped name",
    )
    parser.add_argument(
        "--keep-full",
        action="store_true",
        help="When clipping, also keep the full downloaded extract.  "
             "Recommended while experimenting so you do not re-download "
             "the same state file repeatedly.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable download progress bar",
    )

    args = parser.parse_args(argv)

    if args.list_us:
        print("Common US Geofabrik extracts (use the key as the region argument):\n")
        for k in sorted(COMMON_US):
            if k in ("us", "north-america"):
                continue
            print(f"  {k:25s} → {COMMON_US[k]}-latest.osm.pbf")
        return 0

    if not args.region:
        parser.error("region is required unless --list-us is used")

    rel = resolve_path(args.region)
    filename = rel.split("/")[-1] + "-latest.osm.pbf"
    url = f"{GEOFABRIK_BASE}/{rel}-latest.osm.pbf"
    download_path = args.output_dir / filename

    try:
        download_file(url, download_path, show_progress=not args.no_progress)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("\nTip: check the exact path on https://download.geofabrik.de/", file=sys.stderr)
        print(f"Tried: {url}", file=sys.stderr)
        return 1

    final_path = download_path

    if args.bbox:
        # validate bbox quickly
        parts = [p.strip() for p in args.bbox.split(",")]
        if len(parts) != 4:
            print("ERROR: --bbox must be min_lon,min_lat,max_lon,max_lat", file=sys.stderr)
            return 1
        try:
            [float(x) for x in parts]
        except ValueError:
            print("ERROR: --bbox values must be numbers", file=sys.stderr)
            return 1

        tool = find_clip_tool()
        if not tool:
            print(
                "\nWARNING: neither 'osmium' nor 'osmconvert' found on PATH.\n"
                "The full extract was downloaded, but clipping was skipped.\n"
                "\n"
                "Most common cause on Windows: you are not running inside the\n"
                "Miniforge Prompt.  Open 'Miniforge Prompt' from the Start menu\n"
                "and re-run the command from there.\n"
                "\n"
                "If you are already in Miniforge Prompt, install osmium with:\n"
                "  conda install -c conda-forge osmium-tool\n",
                file=sys.stderr,
            )
        else:
            if args.output:
                clipped_path = args.output
            else:
                clipped_path = args.output_dir / (download_path.stem + "-clipped.osm.pbf")
            clip_pbf(download_path, clipped_path, args.bbox, tool)
            final_path = clipped_path
            if not args.keep_full:
                print(f"Removing full extract {download_path} (use --keep-full to retain it)")
                download_path.unlink()
            else:
                print(f"Keeping full extract: {download_path}")

    print("\nDone.")
    print(f"PBF ready for tile generation: {final_path}")
    print("\nNext typical step (example with tilemaker Docker):")
    print(f"  docker run --rm -v /path/to/data:/data "
          f"ghcr.io/systemed/tilemaker:master "
          f"/data/{final_path.name} --output /data/tiles.mbtiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
