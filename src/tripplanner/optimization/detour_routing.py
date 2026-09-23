"""Precomputes real, road-network-routed detour costs for candidate charging
stations, once, before the state-graph search runs.

Mirrors `trip_input.api._step_route_charging_detours` (which does the same
two-leg routing for the FINAL chosen stops, for map display) but runs for
EVERY candidate station BEFORE the optimizer decides anything, feeding
`NetworkXOptimizer.optimize(detour_kosten=...)` so the search can weigh real
costs instead of the `DETOUR_ROUTENFAKTOR`/`DETOUR_GESCHWINDIGKEIT_KMH`
straight-line heuristic in `optimizer.py`.

Design notes:
- Weather is intentionally NOT re-fetched per detour: a short (typically
  < 5 km) detour's weather is well approximated by a neutral placeholder
  sample (same one used as the fallback in `trip_input.api._step_7_
  calculate_segment_energy` when no real weather sample is available for a
  segment) - re-querying a weather provider per candidate station would add
  dozens to hundreds of extra external API calls for no material accuracy
  gain, and risks the rate-limit issues already seen with weather providers
  in this project.
- Construction zones are intentionally NOT applied to detour segments for
  the same reason (short local roads, negligible probability/impact, and
  avoids re-running construction-zone matching per station).
- Stations within `ON_ROUTE_THRESHOLD_M` of the main route are treated as
  free (matching the existing heuristic's `offroute_distance_m <= 0.0`
  special case) and are not routed at all - saves a routing call per
  on-route station, and a "there and back" GraphHopper request for a near-
  zero-distance detour is not well-defined output-wise.
"""  # noqa: D205

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.elevation.elevation import ElevationProvider
from tripplanner.energy.energy import calculate_segment_consumption
from tripplanner.energy.models import VehicleEnergyParameters
from tripplanner.optimization.models import DetourKosten
from tripplanner.routing.detour_geometry import find_bracket_points
from tripplanner.routing.models import Route
from tripplanner.routing.providers import RoutingProvider
from tripplanner.trip_input.models import TripRequest, VehicleProfile
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents

logger = logging.getLogger(__name__)

ON_ROUTE_THRESHOLD_M: float = 100.0
"""Below this straight-line distance, a station is treated as effectively
on the route (zero detour cost) instead of being routed - matches
`NetworkXOptimizer._detour_kosten`'s `offroute_distance_m <= 0.0` free
case, widened slightly because GraphHopper snapping makes an exact 0.0
distance rare even for stations genuinely at a highway rest stop."""

_NEUTRAL_WEATHER_SAMPLE_KWARGS: dict[str, float] = {
    "temperatur_c": 20.0,
    "windgeschwindigkeit_ms": 5.0,
    "windrichtung_deg": 180.0,
    "niederschlag_mm": 0.0,
    "schneefall_cm": 0.0,
    "luftdruck_hpa": 1013.25,
    "luftfeuchtigkeit_pct": 60.0,
    "globalstrahlung_wm2": 400.0,
    "bewoelkung_pct": 20.0,
}
"""Same neutral placeholder values used as the missing-weather fallback in
`trip_input.api._step_7_calculate_segment_energy` - kept in sync
deliberately (both represent "no real weather signal available")."""


def _make_energy_params(vehicle_profile: VehicleProfile) -> VehicleEnergyParameters:
    """Builds `VehicleEnergyParameters` from a `VehicleProfile`.

    Duplicates the conversion in `trip_input.api._step_7_calculate_segment_
    energy` (that function is private and route-scoped, not reusable as-is)
    - kept as a single, obvious 8-line mapping rather than adding a shared
    helper for a conversion this small.
    """
    return VehicleEnergyParameters(
        mass_kg=vehicle_profile.mass_kg,
        drag_coefficient=vehicle_profile.drag_coefficient,
        frontal_area_m2=vehicle_profile.frontal_area_m2,
        rolling_resistance_coefficient=vehicle_profile.rolling_resistance_coefficient,
        battery_capacity_kwh=vehicle_profile.battery_capacity_kwh,
        auxiliary_baseline_kw=vehicle_profile.auxiliary_baseline_kw,
        tire_type=vehicle_profile.tire_type,
        roof_box=vehicle_profile.roof_box,
    )


async def _route_leg_kosten(
    leg_route: Route,
    elevation_provider: ElevationProvider,
    energy_params: VehicleEnergyParameters,
    departure_time: datetime,
) -> tuple[float, float, float]:
    """Computes (distanz_m, zeit_s, energie_kwh) for one already-routed leg.

    Reuses the exact same elevation -> gradient -> energy pipeline used for
    the main route (`_step_2_extract_elevation_profile` /
    `ElevationProvider.calculate_segment_gradients` /
    `calculate_segment_consumption`), applied to the leg's own segments.
    """
    if not leg_route.segments:
        return 0.0, 0.0, 0.0

    elevation_points = await elevation_provider.get_elevation_profile(leg_route)
    gradients = elevation_provider.calculate_segment_gradients(elevation_points, leg_route)

    distanz_m = 0.0
    zeit_s = 0.0
    energie_kwh = 0.0
    for idx, segment in enumerate(leg_route.segments):
        gradient = gradients[idx] if idx < len(gradients) else None
        wetter = WeatherSample(
            coordinate=segment.geometrie[0],
            zeitpunkt=departure_time,
            **_NEUTRAL_WEATHER_SAMPLE_KWARGS,
        )
        wind = WindComponents(
            segment_index=segment.segment_index, gegenwind_ms=0.0, seitenwind_ms=0.0
        )
        ergebnis = calculate_segment_consumption(
            segment=segment,
            gradient=gradient,  # type: ignore[arg-type]
            wetter=wetter,
            wind=wind,
            fahrzeug_params=energy_params,
            baustellen=None,
        )
        distanz_m += segment.laenge_m
        zeit_s += ergebnis.fahrzeit_s
        energie_kwh += ergebnis.energiebedarf_kwh

    return distanz_m, zeit_s, energie_kwh


async def precompute_detour_costs(  # noqa: PLR0913, PLR0917
    routing_provider: RoutingProvider,
    elevation_provider: ElevationProvider,
    vehicle_profile: VehicleProfile,
    route: Route,
    station_segments: dict[int, list[tuple[ChargingStation, float]]],
    departure_time: datetime,
    max_concurrent_requests: int = 20,
) -> dict[str, DetourKosten]:
    """Computes real, road-network-routed detour costs for every candidate station.

    Routes a "there and back" detour (main route -> station -> main route)
    for each station whose straight-line distance from the route exceeds
    `ON_ROUTE_THRESHOLD_M`, through the same routing/elevation/energy
    pipeline used for the main route, all concurrently (bounded by
    `max_concurrent_requests`). A station whose routing fails (timeout, no
    route found, HTTP error) is simply OMITTED from the result - callers
    (`NetworkXOptimizer._detour_kosten`) fall back to the straight-line
    heuristic for that one station rather than failing the whole trip.

    Args:
        routing_provider: Same provider used for the main route.
        elevation_provider: Same provider used for the main route.
        vehicle_profile: The trip's vehicle profile.
        route: The already-computed main route (for bracket-point anchoring).
        station_segments: Output of `station_mapping.map_stations_to_segments`
            - maps each candidate station to its nearest main-route segment
            index (needed to place the detour's bracket points).
        departure_time: Trip departure time (used for the placeholder weather
            sample's timestamp field only - see module docstring on why
            detours don't fetch real weather).
        max_concurrent_requests: Upper bound on simultaneous routing calls
            in flight, to avoid overwhelming the routing server.

    Returns:
        Mapping from `station_id` to `DetourKosten` for every station that
        was successfully routed. Stations within `ON_ROUTE_THRESHOLD_M` and
        stations whose routing failed are absent from the result.
    """
    energy_params = _make_energy_params(vehicle_profile)
    semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def _kosten_fuer_station(
        station: ChargingStation, seg_idx: int
    ) -> tuple[str, DetourKosten] | None:
        vor_index, nach_index = find_bracket_points(route, seg_idx)
        hinweg_anfrage = TripRequest(
            start=route.geometrie[vor_index],
            destination=station.coordinate,
            departure_time=departure_time,
            vehicle_profile=vehicle_profile,
        )
        rueckweg_anfrage = TripRequest(
            start=station.coordinate,
            destination=route.geometrie[nach_index],
            departure_time=departure_time,
            vehicle_profile=vehicle_profile,
        )
        try:
            async with semaphore:
                hinweg_route, rueckweg_route = await asyncio.gather(
                    routing_provider.berechne_route(hinweg_anfrage),
                    routing_provider.berechne_route(rueckweg_anfrage),
                )
            hinweg_distanz, hinweg_zeit, hinweg_energie = await _route_leg_kosten(
                hinweg_route, elevation_provider, energy_params, departure_time
            )
            rueckweg_distanz, rueckweg_zeit, rueckweg_energie = await _route_leg_kosten(
                rueckweg_route, elevation_provider, energy_params, departure_time
            )
        except Exception:
            logger.warning(
                "Detour routing failed for station %s (%s) - falling back to the "
                "straight-line heuristic for this station.",
                station.station_id,
                station.name,
                exc_info=True,
            )
            return None

        return station.station_id, DetourKosten(
            hinweg_distanz_m=hinweg_distanz,
            hinweg_zeit_s=hinweg_zeit,
            hinweg_energie_kwh=hinweg_energie,
            rueckweg_distanz_m=rueckweg_distanz,
            rueckweg_zeit_s=rueckweg_zeit,
            rueckweg_energie_kwh=rueckweg_energie,
        )

    tasks = [
        _kosten_fuer_station(station, seg_idx)
        for seg_idx, stations in station_segments.items()
        for station, offroute_distance_m in stations
        if offroute_distance_m > ON_ROUTE_THRESHOLD_M
    ]
    if not tasks:
        return {}

    results = await asyncio.gather(*tasks)
    return {station_id: kosten for r in results if r is not None for station_id, kosten in [r]}
