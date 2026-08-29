"""SE/Trafikverket situation parser for construction providers.

Extracted from providers.py to reduce coupling and improve testability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tripplanner.construction.parser import (
    DATEXIIConstructionZoneInternal,
    ROADWORKS_TYPES,
)
from tripplanner.construction.providers.wkt import _parse_wkt_line, _parse_wkt_point


def _parse_trafikverket_situations(
    data: dict[str, Any],
) -> list[DATEXIIConstructionZoneInternal]:
    """Map Trafikverket Situation/Deviation JSON to DATEXIIConstructionZoneInternal.

    Response shape: ``RESPONSE.RESULT[0].Situation[].Deviation[]``.
    Only Deviations whose ``MessageTypeValue`` is in ``ROADWORKS_TYPES``
    are kept (filtering out unrelated traffic messages).
    """
    zones: list[DATEXIIConstructionZoneInternal] = []

    response = data.get("RESPONSE", {})
    results: list[dict[str, Any]] = response.get("RESULT", [])

    for result in results:
        for situation in result.get("Situation", []):
            for deviation in situation.get("Deviation", []):
                msg_type_value: str = deviation.get("MessageTypeValue", "")

                if msg_type_value not in ROADWORKS_TYPES:
                    continue

                start_time = deviation.get("StartTime")
                end_time = deviation.get("EndTime")
                message = deviation.get("Message")
                geometry = deviation.get("Geometry", {})

                line_wkt = (
                    geometry.get("Line", {}).get("WGS84")
                    if isinstance(geometry.get("Line"), dict)
                    else None
                )
                point_wkt = (
                    geometry.get("Point", {}).get("WGS84")
                    if isinstance(geometry.get("Point"), dict)
                    else None
                )

                koordinaten: list[tuple[float, float]] = []
                if line_wkt:
                    parsed = _parse_wkt_line(line_wkt)
                    if parsed:
                        koordinaten = parsed
                if not koordinaten and point_wkt:
                    pt = _parse_wkt_point(point_wkt)
                    if pt:
                        koordinaten = [pt]

                gueltig_von = (
                    datetime.fromisoformat(start_time) if start_time else datetime.now(UTC)
                )
                gueltig_bis = datetime.fromisoformat(end_time) if end_time else None

                zones.append(
                    DATEXIIConstructionZoneInternal(
                        sperrungstyp=msg_type_value,
                        gueltig_von=gueltig_von,
                        gueltig_bis=gueltig_bis,
                        koordinaten=koordinaten,
                        umleitungshinweis=message or None,
                        tempolimit_kmh=None,
                        affected_direction_value=deviation.get("AffectedDirectionValue"),
                    )
                )

    return zones