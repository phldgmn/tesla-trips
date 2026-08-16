"""Integrationstests: Fährvermeidung gegen einen echten GraphHopper-Server.

Reproduziert die live gegen den Projekt-GraphHopper (siehe docker-compose.yml,
DE+DK+SE-Extrakt) validierten Szenarien aus
`docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md`.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.faehren import erkenne_faehren
from tripplanner.routing.providers import GraphHopperRoutingProvider
from tripplanner.trip_input.models import FerryExclusion, TripRequest, VehicleProfile

# Rødby (DK) <-> Puttgarden (D): direkte Fährüberquerung des Fehmarnbelt,
# ca. 22 km / 69 min per Fähre (live verifiziert).
_PUTTGARDEN = (54.5033, 11.2270)
_RODBY = (54.6558, 11.3453)
_LUEBECK = (53.8655, 10.6866)
_COPENHAGEN = (55.6761, 12.5683)

# Gummersbach (D) -> Hagfors (S): reales Nutzerszenario, ca. 1070 km
# Luftlinie start-zu-ziel - überschreitet GraphHoppers
# `routing.non_ch.max_waypoint_distance`-Default (1000 km), der im
# Nicht-CH/Flex-Modus greift, sobald ein `custom_model` (hier:
# Fährvermeidung) verwendet wird. Reproduziert den ursprünglich gemeldeten
# 400 "Point 1 is too far from Point 0" Fehler (siehe
# docker/graphhopper/gh-config.yml für den Server-seitigen Fix).
_GUMMERSBACH = (51.033, 7.567)
_HAGFORS = (60.033, 13.650)


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    """Beispiel-Fahrzeugprofil für Integrationstests."""
    return VehicleProfile(
        masse_kg=1706.0,
        cw_wert=0.23,
        stirnflaeche_m2=2.22,
        rollwiderstandsbeiwert=0.011,
        batteriekapazitaet_kwh=62.5,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_baseline_route_uses_the_ferry(vehicle_profile: VehicleProfile) -> None:
    """Ohne Vermeidung nutzt die direkte Route die Rødby-Puttgarden-Fähre."""
    client = GraphHopperClient(base_url="http://localhost:8989")
    provider = GraphHopperRoutingProvider(client)
    anfrage = TripRequest(
        start=_PUTTGARDEN,
        ziel=_RODBY,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
    )
    try:
        route = await provider.berechne_route(anfrage)
        faehren = erkenne_faehren(route)
        assert len(faehren) == 1
        assert route.gesamtlaenge_m < 30_000
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_avoid_all_ferries_forces_long_detour(vehicle_profile: VehicleProfile) -> None:
    """alle_faehren_vermeiden=True erzwingt eine deutlich längere Landroute ohne Fähre."""
    client = GraphHopperClient(base_url="http://localhost:8989")
    provider = GraphHopperRoutingProvider(client)
    anfrage = TripRequest(
        start=_PUTTGARDEN,
        ziel=_RODBY,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
        alle_faehren_vermeiden=True,
    )
    try:
        route = await provider.berechne_route(anfrage)
        assert erkenne_faehren(route) == []
        assert route.gesamtlaenge_m > 400_000
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_specific_ferry_exclusion_reroutes_around_detected_segment(
    vehicle_profile: VehicleProfile,
) -> None:
    """Wird die zuvor erkannte Fähre gezielt ausgeschlossen, wird sie nicht erneut genutzt
    (die Route darf jedoch eine ANDERE Fähre nutzen - Selbstkorrektur, siehe Design-Spec)."""
    client = GraphHopperClient(base_url="http://localhost:8989")
    provider = GraphHopperRoutingProvider(client)
    baseline_anfrage = TripRequest(
        start=_LUEBECK,
        ziel=_COPENHAGEN,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
    )
    try:
        baseline_route = await provider.berechne_route(baseline_anfrage)
        erkannt = erkenne_faehren(baseline_route)
        assert len(erkannt) == 1
        assert "Rødby" in erkannt[0].name or "Puttgarden" in erkannt[0].name

        ausschluss_anfrage = baseline_anfrage.model_copy(
            update={
                "vermiedene_faehren": [
                    FerryExclusion(
                        name=erkannt[0].name,
                        bbox_sw=erkannt[0].bbox_sw,
                        bbox_no=erkannt[0].bbox_no,
                    )
                ]
            }
        )
        rerouted = await provider.berechne_route(ausschluss_anfrage)

        rerouted_faehren = erkenne_faehren(rerouted)
        excluded_names = {f.name for f in rerouted_faehren}
        assert erkannt[0].name not in excluded_names
        assert rerouted.gesamtlaenge_m > baseline_route.gesamtlaenge_m
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_long_distance_ferry_exclusion_does_not_hit_waypoint_distance_limit(
    vehicle_profile: VehicleProfile,
) -> None:
    """Gummersbach -> Hagfors (~1070 km) mit gezielter Fährvermeidung darf nicht mit
    GraphHoppers `non_ch.max_waypoint_distance`-400-Fehler fehlschlagen (Regressionstest
    für den ursprünglich gemeldeten Bug)."""
    client = GraphHopperClient(base_url="http://localhost:8989")
    provider = GraphHopperRoutingProvider(client)
    baseline_anfrage = TripRequest(
        start=_GUMMERSBACH,
        ziel=_HAGFORS,
        abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        fahrzeugprofil=vehicle_profile,
    )
    try:
        baseline_route = await provider.berechne_route(baseline_anfrage)
        erkannt = erkenne_faehren(baseline_route)
        assert len(erkannt) >= 1, "Testannahme: Basisroute nutzt mind. eine Fähre"

        ausschluss_anfrage = baseline_anfrage.model_copy(
            update={
                "vermiedene_faehren": [
                    FerryExclusion(
                        name=erkannt[0].name,
                        bbox_sw=erkannt[0].bbox_sw,
                        bbox_no=erkannt[0].bbox_no,
                    )
                ]
            }
        )
        rerouted = await provider.berechne_route(ausschluss_anfrage)

        excluded_names = {f.name for f in erkenne_faehren(rerouted)}
        assert erkannt[0].name not in excluded_names
    finally:
        await client.close()
