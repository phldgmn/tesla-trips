"""WKT parsing helpers for construction providers.

Extracted from providers.py to reduce coupling and improve testability.
"""

from __future__ import annotations


def _parse_wkt_point(wkt: str) -> tuple[float, float] | None:
    """Parse a WKT ``POINT (lon lat)`` string into (lat, lon).

    Returns ``None`` if the WKT cannot be parsed.
    """
    try:
        wkt = wkt.strip()
        if not wkt.upper().startswith("POINT"):
            return None
        inner = wkt[5:].strip().strip("()")
        parts = inner.split()
        if len(parts) >= 2:
            lon, lat = float(parts[0]), float(parts[1])
            return (lat, lon)
    except (ValueError, IndexError):
        pass
    return None


def _parse_wkt_line(wkt: str) -> list[tuple[float, float]] | None:
    """Parse a WKT ``LINESTRING (lon lat, lon lat, ...)`` into [(lat, lon)].

    Returns ``None`` if no valid coordinates are found.
    """
    coords: list[tuple[float, float]] = []
    try:
        wkt = wkt.strip()
        if not wkt.upper().startswith("LINESTRING"):
            return None
        inner = wkt[10:].strip().strip("()")
        pairs = inner.split(",")
        for pair in pairs:
            parts = pair.strip().split()
            if len(parts) >= 2:
                try:
                    lon, lat = float(parts[0]), float(parts[1])
                    coords.append((lat, lon))
                except ValueError:
                    continue
    except (ValueError, IndexError):
        pass
    return coords if coords else None
