"""Erkennung von Faehrverbindungen in einer berechneten `Route`.

Kein statisches Faehr-Register: Faehrabschnitte werden ausschliesslich aus den
`road_environment`/`street_name`-Feldern erkannt, die `GraphHopperRoutingprovider`
je segment aus den GraphHopper Path-Details `road_environment`/`street_name`
extrahiert (siehe `docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md`).
"""

from __future__ import annotations

from tripplanner.routing.models import Coordinate, FerrySegment, Route, RouteSegment

FAEHR_PUFFER_GRAD: float = 0.005
"""Pufferung (in Dezimalgrad, ca. 500 m bei den Breitengraden DE/DK/SE) um die
exakte segmentgeometrie einer erkannten Faehrverbindung, damit die daraus gebaute
GraphHopper Custom-Model-Area die komplette ferry_line sicher abdeckt."""

UNNAMED_FERRY = "Unbenannte Faehre"


def erkenne_faehren(route: Route) -> list[FerrySegment]:
    """Gruppiert zusammenhaengende Faehrsegmente einer Route zu `Ferrysegment`-Eintraegen.

    Laeuft einmal linear über `route.segments` und fasst aufeinanderfolgende
    segmente mit `road_environment == "FERRY"` zu je einem `Ferrysegment`
    zusammen (Name aus dem ersten vorhandenen `street_name` des Laufs, sonst
    "Unbenannte Faehre"; Laenge als Summe der `length_m`; Bounding Box aus allen
    beteiligten `geometrie`-Koordinaten, gepuffert um `FAEHR_PUFFER_GRAD`).

    Args:
        route: Eine bereits berechnete Route (z. B. aus `Routingprovider.berechne_route()`).

    Returns:
        Liste erkannter Faehrverbindungen in heading. Leer, wenn die Route
        keine Faehrsegmente enthaelt oder `road_environment` nicht verfügbar war
        (z. B. `FakeRoutingprovider`-Routen).
    """
    ergebnis: list[FerrySegment] = []
    current_run: list[RouteSegment] = []

    def run_finish() -> None:
        if current_run:
            ergebnis.append(run_to_ferry_segment(current_run))

    for segment in route.segments:
        if segment.road_environment == "FERRY":
            current_run.append(segment)
        else:
            run_finish()
            current_run = []
    run_finish()

    return ergebnis


def run_to_ferry_segment(run: list[RouteSegment]) -> FerrySegment:
    """Baut ein `Ferrysegment` aus einem zusammenhaengenden Lauf von Faehr-`Routesegment`s."""
    name = next((s.street_name for s in run if s.street_name), None) or UNNAMED_FERRY
    length_m = sum(s.length_m for s in run)

    koordinaten: list[Coordinate] = [koord for s in run for koord in s.geometrie]
    lats = [k[0] for k in koordinaten]
    lons = [k[1] for k in koordinaten]

    return FerrySegment(
        name=name,
        length_m=length_m,
        bbox_sw=(min(lats) - FAEHR_PUFFER_GRAD, min(lons) - FAEHR_PUFFER_GRAD),
        bbox_ne=(max(lats) + FAEHR_PUFFER_GRAD, max(lons) + FAEHR_PUFFER_GRAD),
        segment_index_start=run[0].segment_index,
        # Exklusiv (wie bei Python-Slices), damit `route.segments[start:end]`
        # genau den Faehr-Lauf ergibt - vom `optimization`-Modul genutzt, um
        # eine vom Nutzer vorgegebene Faehrüberfahrt in der Zustandsgraph-
        # Suche in einem Sprung zu ueberspringen (siehe optimizer.py).
        segment_index_end=run[-1].segment_index + 1,
    )
