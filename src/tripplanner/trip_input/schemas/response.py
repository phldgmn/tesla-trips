"""API-Response-Schemata für den /trips-Endpunkt.

Enthält die Response-Modelle (``FrameAPI`` … ``TripSimulationResultAPI``)
sowie die Response-Builder-Helfer ``_build_construction_zones_api`` und
``_attach_charging_pricing``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from tripplanner.charging_infrastructure import ChargingStationProvider
from tripplanner.charging_infrastructure.pricing import select_owner_rate_for_time
from tripplanner.charging_infrastructure.providers import (
    TeslaChargingStationProvider,
)
from tripplanner.construction.models import ConstructionZone
from tripplanner.geo import haversine_distance_m
from tripplanner.routing.models import Coordinate, RouteSegment
from tripplanner.simulation.models import (
    ChargingCostByCurrency,
    ChargingStopSummary,
    TripSimulationResult,
)
from tripplanner.trip_input.schemas.base import CamelCaseResponseAPI

# Distance below which two nearby construction-zone markers are merged into a
# single marker (with multiple `events`) for map display, so the user isn't
# shown near-duplicate pins for closely-spaced roadwork records on the same
# stretch of road. Deliberately larger than
# `construction.matching.MAX_DISTANCE_M` (which answers "is
# this roadwork actually on the route at all") - this constant instead
# answers "are two on-route roadworks close enough to show as one marker".
_CONSTRUCTION_ZONE_MERGE_DISTANCE_M = 5000.0


def _build_construction_zones_api(
    zones: list[ConstructionZone],
    route_segments: list[RouteSegment],
) -> list[ConstructionZoneAPI]:
    """Build grouped ConstructionZoneAPI entries from raw construction zones.

    Zones are sorted by their first affected segment index, then consecutive
    zones within ``_CONSTRUCTION_ZONE_MERGE_DISTANCE_M`` metres (haversine)
    of each other are merged into a single marker with multiple events.

    Args:
        zones: Raw construction zones from the provider.
        route_segments: Route segments for position resolution.

    Returns:
        List of ConstructionZoneAPI markers, each potentially merging nearby
        events.
    """
    valid_zones: list[ConstructionZone] = [z for z in zones if z.betroffene_segmente]
    valid_zones.sort(key=lambda z: z.betroffene_segmente[0] if z.betroffene_segmente else 0)

    construction_zones_api: list[ConstructionZoneAPI] = []
    last_position: Coordinate | None = None
    for zone in valid_zones:
        first_idx = zone.betroffene_segmente[0]
        if first_idx < 0 or first_idx >= len(route_segments):
            continue

        zone_position = route_segments[first_idx].geometrie[0]

        if (
            last_position is not None
            and haversine_distance_m(last_position, zone_position)
            <= _CONSTRUCTION_ZONE_MERGE_DISTANCE_M
        ):
            construction_zones_api[-1].events.append(
                ConstructionZoneEventAPI(
                    closure_type=zone.sperrungstyp.value,
                    speed_limit_kmh=zone.tempolimit_kmh,
                    detour_notice=zone.umleitungshinweis,
                    country=zone.land.value,
                    valid_from=zone.gueltig_von,
                    valid_to=zone.gueltig_bis,
                )
            )
            last_position = zone_position
            continue

        # Start a new group
        construction_zones_api.append(
            ConstructionZoneAPI(
                position=zone_position,
                events=[
                    ConstructionZoneEventAPI(
                        closure_type=zone.sperrungstyp.value,
                        speed_limit_kmh=zone.tempolimit_kmh,
                        detour_notice=zone.umleitungshinweis,
                        country=zone.land.value,
                        valid_from=zone.gueltig_von,
                        valid_to=zone.gueltig_bis,
                    )
                ],
                length_m=zone.laenge_m,
            ),
        )
        last_position = zone_position

    return construction_zones_api


class FrameAPI(CamelCaseResponseAPI):
    """Einzelner Simulationsframe in der API-Response."""

    timestamp: str = Field(..., description="ISO-8601 Zeitpunkt")
    position: tuple[float, float] = Field(
        ..., description="(lat, lon), konsistent mit Domänenmodell"
    )
    distance_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz vom Reisebeginn entlang der Route in Metern"
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0)
    state: str = Field(..., description="'FAHREN', 'LADEN' oder 'PAUSE'")
    speed_kmh: float = Field(..., ge=0.0)
    temperature_c: float | None = Field(
        default=None,
        description=(
            "Fuer diesen Streckenpunkt angenommene Temperatur in Grad Celsius "
            "(None wenn Wetter bei der Berechnung nicht beruecksichtigt wurde)."
        ),
    )
    wind_speed_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Fuer diesen Streckenpunkt angenommene Windgeschwindigkeit in m/s "
        "(None wie temperatur_c).",
    )
    wind_direction_deg: float | None = Field(
        default=None,
        ge=0.0,
        le=360.0,
        description="Fuer diesen Streckenpunkt angenommene Windrichtung in Grad "
        "(0° = N, 90° = O; None wie temperatur_c).",
    )
    precipitation_mm: float | None = Field(
        default=None,
        ge=0.0,
        description="Fuer diesen Streckenpunkt angenommener Niederschlag in mm/h "
        "(None wie temperatur_c).",
    )


class ChargingStopAPI(CamelCaseResponseAPI):
    """Ladehalt in der API-Response, ein Eintrag pro tatsaechlichem Halt."""

    name: str = Field(..., description="Name der Ladestation")
    station_id: str = Field(..., description="Eindeutige ID der Ladestation")
    position: tuple[float, float] = Field(..., description="(lat, lon) der Ladestation")
    distance_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz entlang der Route, an der abgebogen wird"
    )
    detour_geometry: list[Coordinate] = Field(
        default_factory=list,
        description=(
            "Echte, ueber GraphHopper geroutete Geometrie von der Route zur Ladestation und "
            "zurueck (leer, falls die Detour-Route nicht ermittelt werden konnte)"
        ),
    )
    route_index_before: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `route_geometrie`, ab dem `detour_geometrie` die Hauptroute ersetzt "
            "(None, falls `detour_geometrie` leer ist)"
        ),
    )
    route_index_after: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `route_geometrie`, bis zu dem (inklusive) `detour_geometrie` die "
            "Hauptroute ersetzt (None, falls `detour_geometrie` leer ist)"
        ),
    )
    detour_station_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `detour_geometrie`, an dem die Ladestation tatsaechlich erreicht wird "
            "(None, falls `detour_geometrie` leer ist)"
        ),
    )
    arrival_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Ankunft in %")
    target_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC nach dem Laden in %")
    charging_duration_s: int = Field(..., ge=0, description="Ladedauer in Sekunden")
    energy_charged_kwh: float = Field(..., ge=0.0, description="Geladene Energiemenge in kWh")
    arrival_time: str = Field(..., description="ISO-8601 Ankunftszeitpunkt an der Station")
    departure_time: str = Field(..., description="ISO-8601 Abfahrtszeitpunkt von der Station")
    price_per_kwh: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Applicable Tesla-owner rate per kWh at arrival time, from cached "
            "pricing data. None if no pricing data is cached yet for this station."
        ),
    )
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description=(
            "ISO-4217 currency of `price_per_kwh`/`estimated_cost`. None iff those are None."
        ),
    )
    estimated_cost: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Estimated cost of this charging stop (`energie_geladen_kwh * "
            "price_per_kwh`). None if no pricing data is cached yet for this station."
        ),
    )
    pricing_updated_utc: str | None = Field(
        default=None,
        description=(
            "ISO-8601 timestamp of the cached pricing data used for `price_per_kwh`. "
            "None if no pricing data has ever been scraped for this station."
        ),
    )


class FaehrSegmentAPI(CamelCaseResponseAPI):
    """API-Response für eine in der berechneten Route erkannte Fährverbindung."""

    name: str = Field(..., description="Fährname (aus GraphHopper street_name oder Fallback)")
    length_m: float = Field(..., ge=0, description="Länge der Fährverbindung in Metern")
    bbox_sw: tuple[float, float] = Field(
        ..., description="Südwest-Ecke der gepufferten Bounding Box"
    )
    bbox_ne: tuple[float, float] = Field(
        ..., description="Nordost-Ecke der gepufferten Bounding Box"
    )
    abfahrt: str | None = Field(
        default=None,
        description="Vom Nutzer vorgegebene Abfahrtszeit (ISO-8601), sofern vorhanden",
    )
    ankunft: str | None = Field(
        default=None,
        description="Vom Nutzer vorgegebene Ankunftszeit (ISO-8601), sofern vorhanden",
    )


class ChargingCostByCurrencyAPI(CamelCaseResponseAPI):
    """Aggregated estimated charging cost in a single currency."""

    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code")
    amount: float = Field(..., ge=0.0, description="Summed cost in `currency`")


class ConstructionZoneEventAPI(CamelCaseResponseAPI):
    """One underlying construction/roadwork event merged into a ConstructionZoneAPI marker."""

    closure_type: str = Field(..., description="Art der Sperrung/Baustelle")
    speed_limit_kmh: int | None = Field(
        default=None, description="Reduziertes Tempolimit in km/h (None wenn keine Beschränkung)"
    )
    detour_notice: str | None = Field(
        default=None, description="Freitext-Information zur Umleitung (optional)"
    )
    country: str = Field(..., description="Land, in dem die Baustelle liegt")
    valid_from: datetime = Field(..., description="Startzeitpunkt der Baustelle (ISO 8601)")
    valid_to: datetime | None = Field(
        default=None, description="Endzeitpunkt der Baustelle (ISO 8601), None wenn unbestimmt"
    )


class ConstructionZoneAPI(CamelCaseResponseAPI):
    """API-repräsentation eines Baustellen-Markers, der mehrere nahe Events zusammenfasst."""

    position: Coordinate = Field(
        ..., description="Repräsentative (lat, lon) Position (erstes Event entlang der Route)"
    )
    events: list[ConstructionZoneEventAPI] = Field(
        ..., description="Zusammengefasste Events (Länge > 1 = mehrere nahe Events gemerged)"
    )
    length_m: float | None = Field(
        default=None,
        description=(
            "Geschätzte Länge der betroffenen Straßenstrecke in Metern "
            "(None wenn nicht berechenbar)."
        ),
    )


class WaypointStopAPI(CamelCaseResponseAPI):
    """Zwischenstopp-Aufenthalt in der API-Response, ein Eintrag pro Aufenthalt."""

    position: tuple[float, float] = Field(..., description="(lat, lon) des Zwischenstopps")
    distance_m: float = Field(
        ..., ge=0.0, description="Kumulierte Distanz entlang der Route bei diesem Zwischenstopp"
    )
    arrival_time: str = Field(..., description="ISO-8601 Ankunftszeitpunkt am Zwischenstopp")
    departure_time: str = Field(..., description="ISO-8601 Zeitpunkt der (erzwungenen) Abfahrt")
    ladeleistung_kw: float | None = Field(
        default=None, ge=0.0, description="Genutzte Ladeleistung in kW, None falls nicht geladen"
    )
    arrival_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Ankunft in %")
    target_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC bei Abfahrt in %")
    energy_charged_kwh: float = Field(
        ..., ge=0.0, description="Waehrend des Aufenthalts geladene Energiemenge in kWh"
    )


class TripSimulationResultAPI(CamelCaseResponseAPI):
    """API-Response für /trips-Endpunkt."""

    total_distance_km: float = Field(..., description="Gesamtdistanz in km")
    total_driving_time_min: float = Field(..., description="Gesamtfahrzeit in Minuten")
    total_charging_time_min: float = Field(..., description="Gesamtladezeit in Minuten")
    total_waiting_time_min: float = Field(
        default=0.0,
        description=(
            "Erzwungene Wartezeit an Zwischenstopps OHNE Ladung, in Minuten "
            "(nicht in gesamt_fahrzeit_min/gesamt_ladezeit_min enthalten)"
        ),
    )
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start-SoC in %")
    target_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC in %")
    frames: list[FrameAPI] = Field(..., description="Liste von Simulationsframes")
    charging_stops: list[ChargingStopAPI] = Field(
        default_factory=list, description="Ein Eintrag pro Ladehalt, fuer die Kartendarstellung"
    )
    waypoint_stops: list[WaypointStopAPI] = Field(
        default_factory=list,
        description="Ein Eintrag pro Zwischenstopp-Aufenthalt, fuer die Kartendarstellung",
    )
    route_geometry: list[Coordinate] = Field(
        ...,
        description=(
            "Vollstaendige Streckengeometrie der berechneten Route (dichte GraphHopper-"
            "Polyline, nicht auf Simulationsframes reduziert) fuer eine winkeltreue "
            "Kartendarstellung."
        ),
    )
    detected_ferries: list[FaehrSegmentAPI] = Field(
        default_factory=list,
        description="In der berechneten Route erkannte Fährverbindungen (leer, falls keine)",
    )
    total_charging_cost: list[ChargingCostByCurrencyAPI] = Field(
        default_factory=list,
        description=(
            "Sum of estimated charging costs across all charging stops, grouped by "
            "currency (empty if no stop has cached pricing data; multiple entries if "
            "stops span several currencies, e.g. a DE-DK-SE trip)."
        ),
    )
    charging_stops_missing_pricing: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of charging stops excluded from `total_charging_cost` "
            "because no pricing data is cached yet for their station."
        ),
    )
    construction_zones: list[ConstructionZoneAPI] = Field(
        default_factory=list,
        description="Baustellen entlang der Route fuer die Kartendarstellung (leer, falls keine)",
    )


def _attach_charging_pricing(
    simulation_result: TripSimulationResult,
    charging_provider: ChargingStationProvider | None,
) -> TripSimulationResult:
    """Step 12: attaches cached pricing to charging stops.

    Also queues stale/missing stations for a background pricing re-scrape.

    Runs at every route finalization (both `/trips` and the `ttp trips` CLI,
    which both call `create_trip_simulation`). For each charging stop actually
    used by this route:

    1. Reads cached pricing from the database (`TeslaChargingStationProvider.
       get_cached_pricing`) and, if available, selects the applicable
       Tesla-owner rate for the stop's arrival time (`select_owner_rate_for_
       time`), attaching `price_per_kwh`/`currency`/`estimated_cost`/
       `pricing_updated_utc` to the returned `ChargingStopSummary`.
    2. Queues the station for a pricing re-scrape (`enqueue_stations_for_
       pricing_refresh`) if its cached pricing is missing or older than
       `TeslaChargingStationProvider.PRICING_MAX_AGE` - fresh stations are
       left untouched to avoid unnecessary Tesla-API/WAF traffic. The actual
       re-scrape happens out-of-band (see the `charger scrape-pricing` CLI
       command), NEVER synchronously here: a curl-equivalent request per
       station is too slow and WAF-risky to run inline in the request/
       response cycle.

    Also computes `TripSimulationResult.total_charging_cost` (summed per
    currency, since a DE/DK/SE trip can span several) and
    `charging_stops_missing_pricing`.

    Only `TeslaChargingStationProvider` supports cached pricing (SQLite-
    backed) - other providers (`Fake`/`LocalFile`, used in tests and as
    defaults) leave stops unpriced, and this step becomes a no-op.

    Args:
        simulation_result: The simulation result produced by `simulate_trip`.
        charging_provider: The charging provider used for this simulation.

    Returns:
        `simulation_result` with priced `charging_stops` and populated
        `total_charging_cost`/`charging_stops_missing_pricing` (unchanged if
        there are no charging stops or `charging_provider` doesn't support
        cached pricing).
    """
    if not simulation_result.charging_stops or not isinstance(
        charging_provider, TeslaChargingStationProvider
    ):
        return simulation_result

    station_ids = [stop.station_id for stop in simulation_result.charging_stops]
    charging_provider.enqueue_stations_for_pricing_refresh(station_ids)

    priced_stops: list[ChargingStopSummary] = []
    totals_by_currency: dict[str, float] = {}
    missing_pricing = 0
    for stop in simulation_result.charging_stops:
        cached = charging_provider.get_cached_pricing(stop.station_id)
        rate = select_owner_rate_for_time(cached.tiers, stop.ankunftszeit)
        if rate is None:
            priced_stops.append(stop)
            missing_pricing += 1
            continue
        estimated_cost = round(stop.energie_geladen_kwh * rate.amount, 2)
        priced_stops.append(
            stop.model_copy(
                update={
                    "price_per_kwh": rate.amount,
                    "currency": rate.currency,
                    "estimated_cost": estimated_cost,
                    "pricing_updated_utc": cached.updated_utc,
                }
            )
        )
        totals_by_currency[rate.currency] = totals_by_currency.get(rate.currency, 0.0) + (
            estimated_cost
        )

    return simulation_result.model_copy(
        update={
            "charging_stops": priced_stops,
            "total_charging_cost": [
                ChargingCostByCurrency(currency=currency, amount=round(amount, 2))
                for currency, amount in sorted(totals_by_currency.items())
            ],
            "charging_stops_missing_pricing": missing_pricing,
        }
    )
