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

    Runs once linearly over ``route.segments`` and groups consecutive
    segments with ``road_environment == "FERRY"`` into one ``FerrySegment`` each
    together (name from the first available ``street_name`` of the run, otherwise
    "Unnamed ferry"; length as the sum of ``length_m``; bounding box from all
    beteiligten `geometrie`-Koordinaten, gepuffert um `FAEHR_PUFFER_GRAD`).

    Args:
        route: Eine bereits berechnete Route (z. B. aus `Routingprovider.berechne_route()`).

    Returns:
        List of detected ferry connections in chronological order. Empty if the route
        has no ferry segments or ``road_environment`` was unavailable
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
        # exactly matches the ferry run — used by the ``optimization`` module to
        # skip a user-specified ferry crossing in one jump in the state graph
        # search (see optimizer.py).
        segment_index_end=run[-1].segment_index + 1,
    )
