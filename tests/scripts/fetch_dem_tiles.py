"""Bulk-download Copernicus GLO-30 DEM tiles for Germany, Denmark, and Sweden.

Pre-populates a local disk cache (`CopernicusDEMDataSource`'s `cache_dir`, see
`src/tripplanner/elevation/providers.py`) with every DEM tile covering the
trip-planner's focus region, so route calculation never has to fetch tiles
over the network (or write them to cache on the request path) at all for
trips within that region.

Idempotent: already-downloaded tiles (same file size as the remote object)
are skipped, so the script can be re-run to resume an interrupted run or to
extend coverage later.

Usage:
    uv run python scripts/fetch_dem_tiles.py
    uv run python scripts/fetch_dem_tiles.py --output-dir data/dem-tiles --concurrency 6
    uv run python scripts/fetch_dem_tiles.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
from pathlib import Path

import httpx
from tripplanner.elevation.providers import copernicus_tile_name

logger = logging.getLogger("fetch_dem_tiles")

_BUCKET_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
_HTTP_OK = 200
_HTTP_NOT_FOUND = 404

# Generous bounding boxes (lat_min, lat_max, lon_min, lon_max) — half a degree
# of margin beyond each country's mainland extent so routes that skirt a
# border (or a ferry leg just offshore) still land on a pre-downloaded tile.
_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "de": (46.5, 55.5, 5.5, 15.5),
    "dk": (54.0, 58.5, 7.5, 15.5),
    "se": (55.0, 69.5, 10.0, 24.5),
}


def _tile_names_for_bbox(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float
) -> set[str]:
    """Enumerate every 1x1-degree Copernicus tile name overlapping a bounding box.

    Args:
        lat_min: Southern bound (degrees).
        lat_max: Northern bound (degrees).
        lon_min: Western bound (degrees).
        lon_max: Eastern bound (degrees).

    Returns:
        Set of tile names (no de-duplication needed by the caller).
    """
    names: set[str] = set()
    lat_band = math.floor(lat_min)
    while lat_band <= math.floor(lat_max):
        lon_band = math.floor(lon_min)
        while lon_band <= math.floor(lon_max):
            # Sample the cell centre — matches how CopernicusDEMDataSource
            # resolves a tile name for any coordinate within the 1x1 cell.
            names.add(copernicus_tile_name(lat_band + 0.5, lon_band + 0.5))
            lon_band += 1
        lat_band += 1
    return names


def tile_names_for_regions(regions: list[str]) -> list[str]:
    """Compute the de-duplicated, sorted tile-name list for the given regions.

    Args:
        regions: Region keys from `_REGIONS` (e.g. ``["de", "dk", "se"]``).

    Returns:
        Sorted list of unique Copernicus tile names.
    """
    names: set[str] = set()
    for region in regions:
        names |= _tile_names_for_bbox(*_REGIONS[region])
    return sorted(names)


async def _download_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    name: str,
    output_dir: Path,
) -> str:
    """Download a single tile if not already present locally.

    Args:
        client: Shared `httpx.AsyncClient`.
        semaphore: Concurrency limiter.
        name: Copernicus tile name.
        output_dir: Local cache directory to write into.

    Returns:
        One of ``"downloaded"``, ``"skipped-cached"``, ``"skipped-missing"``
        (no tile at this cell — e.g. open ocean), or ``"failed"``.
    """
    url = f"{_BUCKET_BASE}/{name}/{name}.tif"
    dest = output_dir / f"{name}.tif"
    tmp_dest = output_dir / f"{name}.tif.part"

    async with semaphore:
        try:
            head = await client.head(url, timeout=30.0)
        except httpx.HTTPError:
            logger.warning("HEAD failed for %s — skipping", name)
            return "failed"

        if head.status_code == _HTTP_NOT_FOUND:
            return "skipped-missing"
        if head.status_code != _HTTP_OK:
            logger.warning("Unexpected HEAD status %s for %s", head.status_code, name)
            return "failed"

        remote_size = int(head.headers.get("content-length", "0"))
        if dest.exists() and dest.stat().st_size == remote_size and remote_size > 0:
            return "skipped-cached"

        try:
            async with client.stream("GET", url, timeout=120.0) as resp:
                resp.raise_for_status()
                with tmp_dest.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
            tmp_dest.replace(dest)
        except (httpx.HTTPError, OSError):
            logger.warning("Download failed for %s", name, exc_info=True)
            tmp_dest.unlink(missing_ok=True)
            return "failed"

    logger.info("Downloaded %s (%.1f MB)", name, remote_size / 1024 / 1024)
    return "downloaded"


async def fetch_all(
    regions: list[str],
    output_dir: Path,
    concurrency: int,
    dry_run: bool,
) -> dict[str, int]:
    """Download every tile covering *regions* into *output_dir*.

    Args:
        regions: Region keys to cover (see `_REGIONS`).
        output_dir: Local cache directory (created if missing).
        concurrency: Max simultaneous downloads.
        dry_run: If True, only report the tile count/names, no network I/O.

    Returns:
        Counts per outcome (``downloaded``, ``skipped-cached``,
        ``skipped-missing``, ``failed``).
    """
    names = tile_names_for_regions(regions)
    logger.info("%d candidate tile(s) for region(s) %s", len(names), ", ".join(regions))

    if dry_run:
        for name in names:
            print(name)
        return {"candidates": len(names)}

    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    counts: dict[str, int] = {
        "downloaded": 0,
        "skipped-cached": 0,
        "skipped-missing": 0,
        "failed": 0,
    }

    async with httpx.AsyncClient() as client:
        tasks = [_download_one(client, semaphore, name, output_dir) for name in names]
        for i, coro in enumerate(asyncio.as_completed(tasks), start=1):
            outcome = await coro
            counts[outcome] += 1
            if i % 10 == 0 or i == len(tasks):
                logger.info(
                    "Progress: %d/%d (downloaded=%d, cached=%d, missing=%d, failed=%d)",
                    i,
                    len(tasks),
                    counts["downloaded"],
                    counts["skipped-cached"],
                    counts["skipped-missing"],
                    counts["failed"],
                )

    return counts


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/dem-tiles"),
        help="Local directory to store downloaded tiles (default: data/dem-tiles)",
    )
    parser.add_argument(
        "--regions",
        default="de,dk,se",
        help="Comma-separated region keys to cover (default: de,dk,se)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="Max simultaneous downloads (default: 6)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print the tile list/count, no network I/O",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    regions = [r.strip().lower() for r in args.regions.split(",") if r.strip()]
    unknown = set(regions) - set(_REGIONS)
    if unknown:
        logger.error("Unknown region(s): %s (known: %s)", ", ".join(unknown), ", ".join(_REGIONS))
        return 1

    counts = asyncio.run(fetch_all(regions, args.output_dir, args.concurrency, args.dry_run))
    logger.info("Done: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
