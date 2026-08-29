"""Mapping-Funktionen fuer die Umwandlung zwischen Tesla-API-Daten und DB-Records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..models import (
    ChargingStation,
    ConnectorType,
    StallType,
)

# Stall-type power thresholds (kW per stall) - hoisted from TeslaChargingStationProvider
_POWER_V2_MAX = 150
_POWER_V3_MAX = 250
_POWER_V3_ULTRA_MAX = 350


def site_to_db_record(site: dict[str, Any]) -> dict[str, Any]:
    """Wandelt ein supercharge.info-Site-Dict in ein DB-Record-Dict um.

    Args:
        site: Roh-Dict von der supercharge.info-API

    Returns:
        DB-Record-Dict für replace_all_stations
    """
    from ..database import _COUNTRY_MAP

    gps = site["gps"]
    address = site.get("address", {})
    plugs = site.get("plugs", {})

    available_plugs = {"nacs", "ccs1", "ccs2", "type2", "gbt", "chademo", "tpc"}
    connector_types = json.dumps(
        [k for k, v in plugs.items() if v and v > 0 and k in available_plugs] or ["ccs2"]
    )

    return {
        "supercharge_info_id": site["id"],
        "tesla_location_id": site.get("locationId"),
        "site_name": site["name"],
        "latitude": gps["latitude"],
        "longitude": gps["longitude"],
        "country_code": _COUNTRY_MAP.get(address.get("country", ""), "XX"),
        "stalls_v2": site.get("stalls", {}).get("v2", 0),
        "stalls_v3": site.get("stalls", {}).get("v3", 0),
        "stalls_v3_ultra": 0,
        "stalls_v4": site.get("stalls", {}).get("v4", 0),
        "total_stalls": site.get("stallCount", 0),
        "power_kilowatt": site.get("powerKilowatt", 250),
        "status": site.get("status", "OPEN"),
        "connector_types": connector_types,
        "ist_24_7": 1,
        "date_opened": site.get("dateOpened"),
        "last_updated_utc": datetime.now(UTC).isoformat(),
    }


def tesla_location_to_db_record(
    loc: dict[str, Any],
    country: str,
) -> dict[str, Any]:
    """Wandelt einen Tesla-Locations-Listeneintrag in ein DB-Record-Dict.

    Wird verwendet wenn keine Detaildaten verfuegbar sind.

    Args:
        loc: Dict aus fetch_locations() (mit _slug und _uuid als Fallback)
        country: ISO-2 Laendercode

    Returns:
        DB-Record-Dict mit verfuegbaren Feldern
    """
    uuid_str: str = loc.get("_uuid", loc.get("uuid", "0"))
    supercharge_info_id = parse_int(uuid_str, 0)
    slug: str = loc.get("_slug", loc.get("location_url_slug", ""))
    lat: float = loc.get("latitude", 0.0)
    lon: float = loc.get("longitude", 0.0)
    now = datetime.now(UTC).isoformat()

    return {
        "supercharge_info_id": supercharge_info_id,
        "tesla_location_id": slug,
        "site_name": f"Tesla Supercharger - {country}",
        "latitude": lat,
        "longitude": lon,
        "country_code": country,
        "stalls_v2": 0,
        "stalls_v3": 0,
        "stalls_v3_ultra": 0,
        "stalls_v4": 0,
        "total_stalls": 0,
        "power_kilowatt": 250,
        "status": "OPEN",
        "connector_types": json.dumps(["ccs2"]),
        "ist_24_7": 1,
        "date_opened": None,
        "last_updated_utc": now,
    }


def parse_int(value: Any, default: int = 0) -> int:
    """Versucht einen Wert als int zu parsen, Fallback auf default."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def tesla_coords(
    sc: dict[str, Any],
    key_data: dict[str, Any],
) -> tuple[float, float]:
    """Extrahiert Koordinaten aus Tesla-API-Detail.

    Bevorzugt actual_latitude/actual_longitude aus supercharger_function,
    fallback auf geo_point aus key_data.
    """
    lat_str: str = sc.get("actual_latitude", "")
    lon_str: str = sc.get("actual_longitude", "")
    if lat_str and lon_str:
        try:
            return (float(lat_str), float(lon_str))
        except (ValueError, TypeError):
            pass
    geo = key_data.get("geo_point", {})
    return (geo.get("lat", 0.0), geo.get("lon", 0.0))


def tesla_detail_to_db_record(
    detail: dict[str, Any],
    country: str,
) -> dict[str, Any]:
    """Wandelt ein Tesla-API-Detail-Dict in ein DB-Record-Dict um.

    Args:
        detail: Detail-Dict von fetch_location_details()
        country: ISO-2 Ländercode

    Returns:
        DB-Record-Dict für replace_all_stations
    """
    sc = detail.get("supercharger_function", {})
    marketing = detail.get("marketing", {})
    key_data = detail.get("key_data", {})

    # ID (uuid), stall count, power
    supercharge_info_id = parse_int(detail.get("_uuid", "0"), 0)
    total_stalls = parse_int(sc.get("num_charger_stalls", "0"), 0)
    power = parse_int(sc.get("installed_full_power", "250"), 250)

    # Coordinates
    latitude, longitude = tesla_coords(sc, key_data)

    # Site name: display_name from marketing
    site_name: str = marketing.get("display_name", marketing.get("common_name", ""))

    # Status
    #
    # Tesla's detail payload carries the same lifecycle state redundantly in
    # three places (see docs/Tesla-Supercharger-API.md): `key_data.status.name`
    # (Title Case, e.g. "Open"), `supercharger_function.project_status` (same
    # vocabulary), and `supercharger_function.site_status` (snake_case, e.g.
    # "open"). Prefer them in that order and normalize case instead of
    # trusting only `key_data.status.name` and defaulting to "Open" whenever
    # it is absent - a site still being built may simply not populate that
    # field yet, and silently assuming "Open" falsely marks it usable for
    # route planning (`TeslaChargingStationProvider.get_all_stations` filters
    # on `status == "OPEN"`). An unrecognized/missing status therefore falls
    # back to "CONSTRUCTION" (not physically usable), never "OPEN".
    status_map: dict[str, str] = {
        "open": "OPEN",
        "closed": "TEMP_CLOSED",
        "coming soon": "CONSTRUCTION",
        "permit": "PERMIT",
    }
    raw_status = (
        key_data.get("status", {}).get("name")
        or sc.get("project_status")
        or str(sc.get("site_status", "")).replace("_", " ")
        or ""
    )
    status: str = status_map.get(raw_status.strip().lower(), "CONSTRUCTION")
    # 0 kW installed power means no charging hardware is live yet, regardless
    # of what the (undocumented, inconsistently populated) status fields
    # claim - never report such a site as operational (observed for real:
    # Ladbergen, Wolfhagen with power=0 but a status field saying "Open").
    if power <= 0:
        status = "CONSTRUCTION"

    # Stall type distribution: derive from power
    stalls_v2 = total_stalls if power <= _POWER_V2_MAX else 0
    stalls_v3 = total_stalls if _POWER_V2_MAX < power <= _POWER_V3_MAX else 0
    stalls_v3_ultra = total_stalls if _POWER_V3_MAX < power <= _POWER_V3_ULTRA_MAX else 0
    stalls_v4 = total_stalls if power > _POWER_V3_ULTRA_MAX else 0

    # Date opened from functions
    functions = detail.get("functions", [])
    date_opened: str | None = None
    for fn in functions:
        od = fn.get("opening_date")
        if od:
            date_opened = od
            break

    # Connector types: default CCS2 for European superchargers
    connector_types = json.dumps(["ccs2"])
    if sc.get("open_to_non_tesla", False):
        connector_types = json.dumps(["ccs2", "nacs"])

    now = datetime.now(UTC).isoformat()

    return {
        "supercharge_info_id": supercharge_info_id,
        "tesla_location_id": detail.get("_slug", ""),
        "site_name": site_name or f"Tesla Supercharger - {country}",
        "latitude": latitude,
        "longitude": longitude,
        "country_code": country,
        "stalls_v2": stalls_v2,
        "stalls_v3": stalls_v3,
        "stalls_v3_ultra": stalls_v3_ultra,
        "stalls_v4": stalls_v4,
        "total_stalls": total_stalls,
        "power_kilowatt": power,
        "status": status,
        "connector_types": connector_types,
        "ist_24_7": 1,
        "date_opened": date_opened,
        "last_updated_utc": now,
    }


def db_record_to_charging_station(
    record: dict[str, Any],
) -> ChargingStation:
    """Wandelt ein DB-Record-Dict in ein ChargingStation-Modell um.

    Args:
        record: DB-Record-Dict aus load_stations()

    Returns:
        ChargingStation-Modell
    """
    power = max(record.get("power_kilowatt", 250), 1)
    stalls_v3 = record.get("stalls_v3", 0)

    _V3_MAX = _POWER_V3_MAX
    _V3_ULTRA_MAX = _POWER_V3_ULTRA_MAX
    stalls: dict[StallType, int] = {
        StallType.V2: record.get("stalls_v2", 0),
        StallType.V3: stalls_v3 if power <= _V3_MAX else 0,
        StallType.V3_ULTRA: stalls_v3 if _V3_MAX < power <= _V3_ULTRA_MAX else 0,
        StallType.V4: record.get("stalls_v4", 0),
    }

    # Parse connector types from JSON
    connector_raw: str = record.get("connector_types", "[]")
    try:
        plug_names: list[str] = json.loads(connector_raw)
    except (json.JSONDecodeError, TypeError):
        plug_names = ["ccs2"]

    connector_map: dict[str, ConnectorType] = {
        "nacs": ConnectorType.NACS,
        "ccs1": ConnectorType.CCS1,
        "ccs2": ConnectorType.CCS2,
        "type2": ConnectorType.TYPE2,
        "gbt": ConnectorType.GB_T,
        "chademo": ConnectorType.CHADEMO,
        "tpc": ConnectorType.TESLA,
    }
    connector_types = [connector_map.get(p, ConnectorType.CCS2) for p in plug_names]

    # Status-Mapping (supercharge.info -> ChargingStation)
    status_map: dict[str, str] = {
        "OPEN": "online",
        "CONSTRUCTION": "wartung",
        "PERMIT": "online",
        "TEMP_CLOSED": "temporaer_geschlossen",
        "PLAN": "online",
    }
    status: str = status_map.get(record.get("status", "OPEN"), "online")

    total_stalls = max(record.get("total_stalls", 0), 1)
    country_code: str = record.get("country_code", "XX")

    # max_ladeleistung_kw is capped at 5000 to match ChargingStation validation
    max_power: float = min(float(total_stalls * power), 5000.0)

    return ChargingStation(
        station_id=record.get("tesla_location_id") or str(record["supercharge_info_id"]),
        name=f"Tesla Supercharger - {record['site_name']}",
        coordinate=(record["latitude"], record["longitude"]),
        stalls=stalls,
        max_ladeleistung_kw=max_power,
        connector_types=connector_types,
        country=country_code,
        ist_24_7=bool(record.get("ist_24_7", 1)),
        status=status,
        letzte_datenAktualisierung=datetime.now(UTC),
    )
