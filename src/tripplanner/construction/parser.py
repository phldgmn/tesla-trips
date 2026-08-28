"""DATEX II XML Parser für Baustelleninformationen.

Nutzt xmlschema (v3.3) für Validierung und Parsing. Falls xmlschema nicht verfügbar,
fällt auf xml.etree.ElementTree zurück (für Unit-Tests ohne externe Abhängigkeiten).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from tripplanner.construction.models import Land


class DATEXIIConstructionZoneInternal(NamedTuple):
    """Internes Modell für DATEX II Parse-Ergebnis (nicht exportiert)."""

    sperrungstyp: str
    gueltig_von: datetime
    gueltig_bis: datetime | None
    koordinaten: list[tuple[float, float]]
    umleitungshinweis: str | None
    tempolimit_kmh: int | None
    affected_direction_value: str | None = None


NAMESPACES = {
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "gml": "http://www.opengis.net/gml",
    "datex": "http://datex2.eu/schema/2/2_0",
}


def parse_datexii_xml(xml_content: str, land: Land) -> list[DATEXIIConstructionZoneInternal]:
    """Parse DATEX II XML und extrahiert Baustelleninformationen.

    Args:
        xml_content: Raw XML string von DATEX II Feed.
        land: Land (für spezifische Mapping-Logik).

    Returns:
        Liste von DATEXIIConstructionZoneInternal (internal).
    """
    return _parse_with_elementtree(xml_content, land)


def _parse_with_elementtree(
    xml_content: str,
    land: Land,
) -> list[DATEXIIConstructionZoneInternal]:
    """Parse DATEX II XML mit xml.etree.ElementTree (für Unit-Tests)."""
    root = ET.fromstring(xml_content)
    zones: list[DATEXIIConstructionZoneInternal] = []

    for sr in _find_situation_records(root):
        zone = _parse_situation_record(sr, land)
        if zone is not None:
            zones.append(zone)

    return zones


def _find_situation_records(root: ET.Element) -> list[ET.Element]:
    """Find all situationRecord elements in the XML tree."""
    # Try with namespaces first
    records = root.findall(".//situationRecord", NAMESPACES)
    if records:
        return records

    records = root.findall(".//{http://datex2.eu/schema/2/2_0}SituationRecord", NAMESPACES)
    if records:
        return records

    return [
        elem
        for elem in root.iter()
        if elem.tag.rsplit("}", 1)[-1] in ("SituationRecord", "situationRecord")
    ]


def _find_element(parent: ET.Element, tag: str) -> ET.Element | None:
    """Find an element matching *tag* by local name, ignoring namespaces.

    This is deliberately loose so it works across DATEX II v2.2 and v3.x
    payloads, whose elements carry different namespace URIs but the same
    local tag names (``validity``, ``overallStartTime``, etc.).
    """
    # Strip leading .// or // if present
    clean_tag = tag
    if clean_tag.startswith(".//"):
        clean_tag = clean_tag[3:]
    elif clean_tag.startswith("//"):
        clean_tag = clean_tag[2:]

    tag_no_ns = clean_tag.rsplit("}", 1)[-1] if "}" in clean_tag else clean_tag

    # Walk every element under *parent*, match on local tag name.
    for elem in parent.iter():
        if elem.tag.rsplit("}", 1)[-1] == tag_no_ns:
            return elem

    return None


# Datex II xsi:type values that indicate roadworks/maintenance
ROADWORKS_TYPES = frozenset(
    {
        "Roadworks",
        "MaintenanceWorks",
        "ConstructionWorks",
        "LaneClosed",
        "LaneClosure",
        "TemporarySpeedLimit",
        "SpeedManagement",
        "RoadOrCarriagewayOrLaneManagement",
    }
)


def _parse_situation_record(sr: ET.Element, land: Land) -> DATEXIIConstructionZoneInternal | None:
    """Parse ein einzelnes situationRecord-Element."""
    xsi_type = sr.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")

    # Check if it's a roadworks/maintenance related type
    if not any(rt in xsi_type for rt in ROADWORKS_TYPES):
        return None

    creation_time_elem = _find_element(sr, ".//situationRecordCreationTime")
    creation_time = (
        _parse_datetime(
            creation_time_elem.text,
        )
        if creation_time_elem is not None and creation_time_elem.text
        else datetime.now(UTC)
    )

    validity_elem = _find_element(sr, ".//validity")
    gueltig_von = creation_time
    gueltig_bis: datetime | None = None

    if validity_elem is not None:
        time_spec = _find_element(validity_elem, ".//validityTimeSpecification")
        if time_spec is not None:
            start_elem = _find_element(time_spec, ".//overallStartTime")
            if start_elem is not None and start_elem.text:
                gueltig_von = _parse_datetime(start_elem.text)

            end_elem = _find_element(time_spec, ".//overallEndTime")
            if end_elem is not None and end_elem.text:
                gueltig_bis = _parse_datetime(end_elem.text)

    impact_elem = _find_element(sr, ".//impact")
    tempolimit_kmh: int | None = None

    if impact_elem is not None:
        delays = _find_element(impact_elem, ".//delays")
        if delays is not None:
            delay_band = _find_element(delays, ".//delayBand")
            if delay_band is not None and delay_band.text:
                tempolimit_kmh = _delay_band_to_speed(delay_band.text)

    locations = _find_element(sr, ".//groupOfLocations")
    koordinaten: list[tuple[float, float]] = []

    if locations is not None:
        # v2.2: gml:coordinates string ("lon lat" pairs, comma-separated)
        coords_elem = _find_element(locations, ".//gml:coordinates")
        coords_elem = coords_elem or _find_element(locations, ".//coordinates")
        if coords_elem is not None and coords_elem.text:
            koordinaten = _parse_coordinates(coords_elem.text, land)
        else:
            # v2.2 fallback: <geographicPosition>/<latitude> + <longitude>
            for gp in locations.iter():
                if "geographicPosition" not in gp.tag:
                    continue
                lat_elem = _find_element(gp, ".//latitude")
                lon_elem = _find_element(gp, ".//longitude")
                if (
                    lat_elem is not None
                    and lat_elem.text
                    and lon_elem is not None
                    and lon_elem.text
                ):
                    try:
                        lat = float(lat_elem.text)
                        lon = float(lon_elem.text)
                        koordinaten.append((lat, lon))
                    except ValueError:
                        continue

    # v3 DATEX II: locationReference (LinearLocation or SingleSnapshotLocation)
    loc_ref = _find_element(sr, ".//locationReference")
    if loc_ref is not None:
        # Prefer line geometry: gmlLineString → posList (space-separated "lat lon" pairs)
        pos_list_elem = _find_element(loc_ref, ".//posList")
        if pos_list_elem is not None and pos_list_elem.text:
            koordinaten = _parse_v3_pos_list(pos_list_elem.text)
        else:
            # Fall back to point geometry: coordinatesForDisplay → latitude + longitude
            coords_for_display = _find_element(loc_ref, ".//coordinatesForDisplay")
            if coords_for_display is not None:
                lat_elem = _find_element(coords_for_display, ".//latitude")
                lon_elem = _find_element(coords_for_display, ".//longitude")
                if (
                    lat_elem is not None
                    and lat_elem.text
                    and lon_elem is not None
                    and lon_elem.text
                ):
                    try:
                        lat = float(lat_elem.text)
                        lon = float(lon_elem.text)
                        koordinaten = [(lat, lon)]
                    except ValueError:
                        pass

    # NRW Mobilitätsdaten schema variant: posList nested directly under
    # groupOfLocations (e.g. groupOfLocations/linearExtension/linearExtended/
    # gmlLineString/posList) rather than under locationReference.
    if not koordinaten and locations is not None:
        pos_list_in_group = _find_element(locations, ".//posList")
        if pos_list_in_group is not None and pos_list_in_group.text:
            koordinaten = _parse_v3_pos_list(pos_list_in_group.text)

    source_elem = _find_element(sr, ".//source")
    umleitungshinweis: str | None = None

    if source_elem is not None:
        source_name = _find_element(source_elem, ".//sourceName/value")
        if source_name is not None and source_name.text:
            umleitungshinweis = source_name.text

    sperrungstyp = xsi_type
    if "Roadworks" in sperrungstyp:
        sperrungstyp = sperrungstyp.split(":")[-1] if ":" in sperrungstyp else "Roadworks"
    elif "MaintenanceWorks" in sperrungstyp:
        sperrungstyp = sperrungstyp.split(":")[-1] if ":" in sperrungstyp else "MaintenanceWorks"

    return DATEXIIConstructionZoneInternal(
        sperrungstyp=sperrungstyp,
        gueltig_von=gueltig_von,
        gueltig_bis=gueltig_bis,
        koordinaten=koordinaten,
        umleitungshinweis=umleitungshinweis,
        tempolimit_kmh=tempolimit_kmh,
        affected_direction_value=None,
    )


def _parse_datetime(dt_str: str | None) -> datetime:
    """Parse ISO 8601 datetime string (DATEX II standard)."""
    if not dt_str:
        return datetime.now(UTC)

    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)


def _delay_band_to_speed(delay_band: str | None) -> int | None:
    """Mappe delayBand auf tempolimit_kmh (Konfiguration für feine Anpassung)."""
    if not delay_band:
        return None

    band_map = {
        "upToTenMinutes": 100,
        "tenToTwentyMinutes": 80,
        "twentyToFortyMinutes": 60,
        "overFortyMinutes": 40,
    }
    return band_map.get(delay_band, 60)


def _parse_coordinates(
    coord_str: str,
    land: Land,
) -> list[tuple[float, float]]:
    """Parse gml:coordinates string in Liste von (lat, lon) Tupeln."""
    coords: list[tuple[float, float]] = []
    if not coord_str:
        return coords

    separator = ","
    for pt in coord_str.split(separator):
        pt_clean = pt.strip()
        if not pt_clean:
            continue

        parts = pt_clean.split()
        if len(parts) >= 2:
            try:
                if land == "DE":
                    lat = float(parts[0])
                    lon = float(parts[1])
                else:
                    lat = float(parts[1])
                    lon = float(parts[0])
                coords.append((lat, lon))
            except ValueError:
                continue

    return coords


def _parse_v3_pos_list(pos_str: str) -> list[tuple[float, float]]:
    """Parse a DATEX II v3 ``posList`` string into (lat, lon) pairs.

    Format: space-separated numeric pairs, first number = latitude (for
    EPSG:4258 in Denmark / GML default order).  E.g.
    ``"55.66038 12.49383 55.66040 12.49385"`` →
    ``[(55.66038, 12.49383), (55.66040, 12.49385)]``.
    """
    coords: list[tuple[float, float]] = []
    parts = pos_str.split()
    i = 0
    while i + 1 < len(parts):
        try:
            lat = float(parts[i])
            lon = float(parts[i + 1])
            coords.append((lat, lon))
        except ValueError:
            break
        i += 2
    return coords
