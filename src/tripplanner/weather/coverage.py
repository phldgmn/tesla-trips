"""Best-effort country lookup for weather-provider coverage decisions.

Some weather providers only publish data for a single country (SMHI: Sweden,
DMI: Denmark). To avoid querying a national provider for a location it does
not cover, `detect_country` maps a coordinate to one of the project's three
focus countries (DE/DK/SE, see `AGENTS.md`) using coarse bounding boxes.

This is a soft heuristic, not a legal border lookup: it only gates which
*additional* national provider joins the candidate pool for a coordinate.
Globally-covering providers (Open-Meteo, MET Norway, OpenWeather) remain
candidates for every coordinate regardless of this classification, so a
misclassification at a border only means a national provider is used, or
skipped, roughly a few kilometres from the true border - never a hard
failure.
"""

from tripplanner.geo import Coordinate

# Approximate bounding boxes (lat_min, lat_max, lon_min, lon_max), WGS84.
_GERMANY_BBOX = (47.27, 55.06, 5.87, 15.05)
_DENMARK_BORNHOLM_BBOX = (54.9, 55.35, 14.5, 15.3)
_DENMARK_NORTH_BBOX = (55.1, 57.75, 8.0, 12.9)
_SWEDEN_BBOX = (55.2, 69.07, 10.9, 24.17)

# Denmark's southern islands (Lolland/Falster/southern Zealand) and
# Germany's northernmost strip (Schleswig-Holstein, incl. Flensburg/Kiel)
# both sit in the ~54.4-55.1 degN band, so a plain bounding box cannot tell
# them apart. Approximate the border with a longitude split at 10.8 degE:
# Danish islands sit east of it (Rødby ~11.34 degE), German Baltic coast
# sits west of it (Flensburg ~9.43 degE, Kiel ~10.13 degE). Inevitably
# imprecise within a few km of the actual land border.
_DE_DK_BORDER_BAND = (54.4, 55.1, 8.0, 12.9)
_DE_DK_BORDER_LON_SPLIT = 10.8


def detect_country(coordinate: Coordinate) -> str | None:
    """Classifies a coordinate as one of the project's focus countries.

    Args:
        coordinate: WGS84 `(lat, lon)`.

    Returns:
        `"DE"`, `"DK"`, or `"SE"` for a coordinate inside the corresponding
        approximate bounding box, `None` if it falls outside all three
        (in that case only globally-covering providers are eligible).
    """
    lat, lon = coordinate

    if _in_bbox(lat, lon, _DENMARK_BORNHOLM_BBOX):
        return "DK"

    if _in_bbox(lat, lon, _DE_DK_BORDER_BAND):
        return "DK" if lon >= _DE_DK_BORDER_LON_SPLIT else "DE"

    if _in_bbox(lat, lon, _DENMARK_NORTH_BBOX):
        return "DK"

    if _in_bbox(lat, lon, _SWEDEN_BBOX):
        return "SE"

    if _in_bbox(lat, lon, _GERMANY_BBOX):
        return "DE"

    return None


def _in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    """Checks whether `(lat, lon)` falls inside `bbox` (lat_min/max, lon_min/max)."""
    lat_min, lat_max, lon_min, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
