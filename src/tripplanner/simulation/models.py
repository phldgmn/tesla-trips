"""data models für das simulation-Modul.

Pydantic-modele zur Darstellung von simulationsframes und Ergebnissen.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from tripplanner.construction.models import ConstructionZone
from tripplanner.routing.models import Coordinate

# Konstanten für speedsschwellen
_MAX_LADE_GESCHWINDIGKIT_KMH = 0.5
_MAX_PAUSE_GESCHWINDIGKIT_KMH = 5.0


class TripState(StrEnum):
    """Zustand des Fahrzeugs zu einem timestamp in der simulation."""

    FAHREN = "FAHREN"
    LADEN = "LADEN"
    PAUSE = "PAUSE"


class SimulationFrame(BaseModel):
    """Ein einzelner timestamp in der Reisesimulation."""

    timestamp: datetime
    position: tuple[float, float] = Field(..., description="Position als (lat, lon) Tuple in WGS84")
    distance_m: float = Field(
        ..., ge=0.0, description="Cumulative distance from trip start along the route in meters"
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ladestand in Prozent")
    zustand: TripState
    speed_kmh: float = Field(..., ge=0.0, description="speed in km/h")
    temperature_c: float | None = Field(
        default=None,
        description=(
            "Fuer diesen Streckenpunkt angenommene temperature in Grad Celsius "
            "(None, wenn Wetter bei der Berechnung nicht beruecksichtigt wurde, "
            "siehe `WeatherDetailLevel` 'off'). Fuer die Routen-Hover-Anzeige im "
            "Frontend (siehe `buildRouteHoverText` in `popups.ts`)."
        ),
    )
    wind_speed_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Fuer diesen Streckenpunkt angenommene wind_speed_ms in m/s "
        "(None wie temperature_c).",
    )
    wind_direction_deg: float | None = Field(
        default=None,
        ge=0.0,
        le=360.0,
        description="Fuer diesen Streckenpunkt angenommene wind_direction_deg in Grad "
        "(0° = N, 90° = O; None wie temperature_c).",
    )
    precipitation_mm: float | None = Field(
        default=None,
        ge=0.0,
        description="Fuer diesen Streckenpunkt angenommener precipitation in mm/h "
        "(None wie temperature_c).",
    )

    @model_validator(mode="after")
    def validate_speed_state_consistency(self) -> SimulationFrame:
        """Validiere Konsistenz zwischen Zustand und speed."""
        if self.zustand == TripState.LADEN and self.speed_kmh > _MAX_LADE_GESCHWINDIGKIT_KMH:
            raise ValueError("Beim Laden muss speed ≈ 0 km/h sein")
        if self.zustand == TripState.PAUSE and self.speed_kmh > _MAX_PAUSE_GESCHWINDIGKIT_KMH:
            raise ValueError("Bei Pause sollte speed sehr gering sein")
        return self


class ChargingStopSummary(BaseModel):
    """Zusammenfassung eines Ladehalts fuer die Visualisierung.

    Ein Eintrag pro tatsaechlichem Ladehalt (nicht pro simulationsframe) -
    im Gegensatz zu den `simulationFrame`-Eintraegen mit `zustand == LADEN`,
    von denen es waehrend eines einzelnen Ladehalts mehrere geben kann.
    """

    name: str = Field(..., min_length=1, description="Name der charging station")
    station_id: str = Field(
        ...,
        min_length=1,
        description="Eindeutige ID der Ladestation, zur Identifikation "
        "bei einer vom Nutzer vorgegebenen charge_duration (siehe "
        "`tripplanner.trip_input.models.ChargingDurationSpecification`)",
    )
    position: tuple[float, float] = Field(
        ..., description="Position der charging station als (lat, lon)"
    )
    distance_m: float = Field(
        ..., ge=0.0, description="Cumulative distance along the route at which the turn is made"
    )
    detour_geometrie: list[tuple[float, float]] = Field(
        default_factory=list,
        description=(
            "Echte, ueber GraphHopper geroutete Geometrie von der Route zur Ladestation und "
            "zurueck (leer, falls die Detour-Route nicht ermittelt werden konnte)"
        ),
    )
    route_index_vor: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `Route.geometrie`/`route_geometrie`, ab dem `detour_geometrie` die "
            "Hauptroute ersetzt (None, falls `detour_geometrie` leer ist)"
        ),
    )
    route_index_nach: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `Route.geometrie`/`route_geometrie`, bis zu dem (inklusive) "
            "`detour_geometrie` die Hauptroute ersetzt (None, falls `detour_geometrie` leer ist)"
        ),
    )
    detour_station_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Index in `detour_geometrie`, an dem die Ladestation tatsaechlich erreicht wird - "
            "dort erfolgt der SoC-Sprung Ankunfts- zu Ziel-Wert in der Kartendarstellung, statt "
            "ueber die gesamte Rueckfahrt der Detour-Schleife verschmiert zu werden (None, "
            "falls `detour_geometrie` leer ist)."
        ),
    )
    arrival_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC upon arrival in %")
    target_soc_pct: float = Field(
        ..., ge=0.0, le=100.0, description="Angestrebter SoC nach dem Laden in %"
    )
    charging_duration_s: int = Field(..., ge=0, description="charge duration in seconds")
    energie_geladen_kwh: float = Field(
        ..., ge=0.0, description="Waehrend des Ladehalts energy charged in kWh"
    )
    arrival_time: datetime = Field(..., description="timestamp der Ankunft an der Station")
    departure_time: datetime = Field(..., description="timestamp der Abfahrt von der Station")
    price_per_kwh: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Applicable Tesla-owner rate per kWh at arrival time, selected from "
            "cached pricing data (see `tripplanner.charging_infrastructure.pricing."
            "select_owner_rate_for_time`). None if no pricing data is cached yet "
            "for this station."
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
    pricing_updated_utc: datetime | None = Field(
        default=None,
        description=(
            "Timestamp of the cached pricing data used for `price_per_kwh`. "
            "None if no pricing data has ever been scraped for this station."
        ),
    )


class WaypointStopSummary(BaseModel):
    """Zusammenfassung eines Zwischenstopp-Aufenthalts fuer die Visualisierung.

    Analog zu `ChargingStopSummary`, aber fuer eine erzwungene Wartezeit an
    einem Zwischenstopp (`tripplanner.optimization.models.
    WaypointDwell`), optional mit Ladung ueber eine vor Ort
    verfuegbare charging_power - kein `station_id`/Preis-/Detour-Handling, da
    kein `ChargingStation`-Objekt existiert (der Zwischenstopp ist keine
    charging_infrastructure).
    """

    position: tuple[float, float] = Field(
        ..., description="Position des Zwischenstopps als (lat, lon)"
    )
    distance_m: float = Field(
        ..., ge=0.0, description="Cumulative distance along the route at this waypoint stop"
    )
    arrival_time: datetime = Field(..., description="timestamp der Ankunft am Zwischenstopp")
    departure_time: datetime = Field(..., description="timestamp der (erzwungenen) Abfahrt")
    charging_power_kw: float | None = Field(
        default=None, ge=0.0, description="Charging power used in kW, None if not charging"
    )
    arrival_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC upon arrival in %")
    target_soc_pct: float = Field(..., ge=0.0, le=100.0, description="SoC upon departure in %")
    energie_geladen_kwh: float = Field(
        ..., ge=0.0, description="Waehrend des Aufenthalts energy charged in kWh"
    )


class ChargingCostByCurrency(BaseModel):
    """Aggregated estimated charging cost in a single currency.

    A trip spanning several countries (e.g. Germany -> Denmark -> Sweden) can
    have charging stops priced in different currencies (EUR/DKK/SEK); summing
    raw amounts across currencies without a conversion would be meaningless,
    so `TripsimulationResult.total_charging_cost` reports one entry per
    currency actually observed among priced stops instead of a single total.
    """

    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code")
    amount: float = Field(..., ge=0.0, description="Summed `estimated_cost` in `currency`")


class TripSimulationResult(BaseModel):
    """Vollstaendige Zeitreihe einer Reise."""

    frames: list[SimulationFrame]
    gesamt_distanz_km: float = Field(..., ge=0, description="total_distance in km")
    gesamt_fahrzeit_min: float = Field(..., ge=0, description="Total driving time in minutes")
    gesamt_ladezeit_min: float = Field(..., ge=0, description="total charge time in minutes")
    gesamt_wartezeit_min: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Aufenthaltszeit an Zwischenstopps (Waypoints), in Minuten - auch "
            "wenn dort geladen wird. Nicht in `gesamt_fahrzeit_min`/"
            "`gesamt_ladezeit_min` enthalten; nur tatsaechliche Ladestopps "
            "zahlen in `gesamt_ladezeit_min`."
        ),
    )
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start SoC in %")
    target_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Target SoC in %")
    charging_stops: list[ChargingStopSummary] = Field(
        default_factory=list,
        description="Ein Eintrag pro Ladehalt (chronologisch), fuer die map display",
    )
    waypoint_stops: list[WaypointStopSummary] = Field(
        default_factory=list,
        description=(
            "Ein Eintrag pro Zwischenstopp-Aufenthalt (chronologisch), fuer die map display"
        ),
    )
    total_charging_cost: list[ChargingCostByCurrency] = Field(
        default_factory=list,
        description=(
            "Sum of `ChargingStopSummary.estimated_cost` across all charging "
            "stops, grouped by currency. Empty if no stop has cached pricing data."
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
    construction_zones: list[ConstructionZone] = Field(
        default_factory=list,
        description="construction zones entlang der Route fuer die map display",
    )


class LadehaltDetour(BaseModel):
    """Ergebnis des Detour-Routings zu einem Ladehalt.

    Input fuer `simulate_trip()`, produziert von
    `tripplanner.trip_input.api._step_lade_detours_routen`.

    Die beiden Klammerpunkte (`route_index_vor`/`route_index_nach`, indices in
    `Route.geometrie`) liegen bewusst deutlich VOR/NACH dem eigentlichen
    Abzweigpunkt auf der Route - ein Detour-Request mit `start == destination`
    (derselbe Punkt) ist fuer GraphHopper richtungsmehrdeutig und fuehrt zu
    unnoetigen Umwegen (an der falschen Ausfahrt vorbei, an der naechsten
    wenden). Mit zwei UNTERSCHIEDLICHEN, bereits auf der Hauptroute in
    korrekter heading liegenden Punkten ist die heading dagegen
    von vornherein eindeutig.
    """

    geometrie: list[Coordinate] = Field(
        ...,
        min_length=2,
        description="Geroutete Geometrie von `route_index_vor` ueber die Station zum Ziel",
    )
    station_index: int = Field(
        ...,
        ge=0,
        description=(
            "Index in `geometrie`, an dem die Ladestation tatsaechlich erreicht wird (Ende des "
            "Hinwegs / Anfang des Rueckwegs, siehe `_step_lade_detours_routen` - zwei separat "
            "geroutete Beine statt eines Via-Punkt-Requests, damit dieser Index exakt statt per "
            "Naechster-Punkt-Heuristik ermittelt wird)"
        ),
    )
    route_index_vor: int = Field(
        ...,
        ge=0,
        description="Index in `Route.geometrie`, from which this geometry replaces the main route",
    )
    route_index_nach: int = Field(
        ...,
        ge=0,
        description=(
            "Index in `Route.geometrie`, bis zu dem (inklusive) diese Geometrie die Hauptroute "
            "ersetzt"
        ),
    )
