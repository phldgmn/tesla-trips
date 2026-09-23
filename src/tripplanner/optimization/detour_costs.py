"""Reine Kostenfunktionen für Abstecher zu Ladestationen (ohne Optimizer-Zustand).

Extrahiert aus `optimizer.py` (Task 2.1) als freie, testbare Funktionen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tripplanner.optimization.charging_math import calc_soc_verbrauch_pct
from tripplanner.optimization.models import DetourKosten

if TYPE_CHECKING:
    from tripplanner.trip_input.models import VehicleProfile


DETOUR_ROUTENFAKTOR: float = 1.6
"""Multiplikator, um aus der Luftlinien-Entfernung Station<->Route eine
realistische Straßendistanz zu schätzen (echte Straßen sind selten
geradlinig - kalibriert an den 1.2x-2x, die `_step_route_charging_detours`
live gegen GraphHopper für Abstecher zu Ladestationen beobachtet, siehe
`find_bracket_points`-Docstring in `tripplanner.routing.detour_geometry`)."""

DETOUR_GESCHWINDIGKEIT_KMH: float = 70.0
"""Angenommene Durchschnittsgeschwindigkeit auf dem Abstecher zur Ladestation
(oft Landstraße/Zubringer, nicht die Haupttrasse - konservativ niedriger als
ein Autobahn-Tempolimit)."""


def detour_kosten(
    station_id: str,
    offroute_distance_m: float,
    vehicle_profile: VehicleProfile,
    detour_kosten: dict[str, DetourKosten] | None,
    avg_verbrauch_kwh_pro_m: float,
) -> tuple[float, float, float, float]:
    """Estimates outward/return detour time (s) and SoC cost (%) for a station.

    Uses the REAL, GraphHopper-routed, per-direction cost from
    `detour_kosten` (see `optimization.detour_routing.precompute_detour_costs`)
    whenever the station is present there. Falls back to the straight-line
    heuristic (`DETOUR_ROUTENFAKTOR`/`DETOUR_GESCHWINDIGKEIT_KMH`) only when
    `detour_kosten` is `None` (caller didn't precompute - e.g. some tests)
    or the station is missing from it (real routing failed for this
    specific station, see `precompute_detour_costs`'s docstring). The
    heuristic has no directional information (a single straight-line
    distance), so it necessarily reports the SAME cost for both directions
    - only the real, routed case can be asymmetric.

    `offroute_distance_m` is the straight-line distance (see
    `station_mapping.map_station_to_segment`); it is only used by the
    fallback heuristic.

    Returns:
        `(hinweg_zeit_s, hinweg_soc_pct, rueckweg_zeit_s, rueckweg_soc_pct)` -
        outward (route -> station) and return (station -> route) leg costs,
        tracked separately (see `DetourKosten` docstring for why averaging
        them is unsafe).
    """
    if detour_kosten is not None and station_id in detour_kosten:
        kosten = detour_kosten[station_id]
        hinweg_soc_pct = calc_soc_verbrauch_pct(
            kosten.hinweg_energie_kwh, vehicle_profile.battery_capacity_kwh
        )
        rueckweg_soc_pct = calc_soc_verbrauch_pct(
            kosten.rueckweg_energie_kwh, vehicle_profile.battery_capacity_kwh
        )
        return kosten.hinweg_zeit_s, hinweg_soc_pct, kosten.rueckweg_zeit_s, rueckweg_soc_pct

    if offroute_distance_m <= 0.0:
        return 0.0, 0.0, 0.0, 0.0

    strecke_m = offroute_distance_m * DETOUR_ROUTENFAKTOR
    detour_geschwindigkeit_m_s = DETOUR_GESCHWINDIGKEIT_KMH * 1000.0 / 3600.0
    zeit_s = strecke_m / detour_geschwindigkeit_m_s
    energie_kwh = strecke_m * avg_verbrauch_kwh_pro_m
    soc_pct = calc_soc_verbrauch_pct(energie_kwh, vehicle_profile.battery_capacity_kwh)
    return zeit_s, soc_pct, zeit_s, soc_pct
