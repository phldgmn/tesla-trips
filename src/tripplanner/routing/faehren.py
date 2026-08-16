"""Erkennung von Fährverbindungen in einer berechneten `Route`.

Kein statisches Fähr-Register: Fährabschnitte werden ausschließlich aus den
`road_environment`/`strassenname`-Feldern erkannt, die `GraphHopperRoutingProvider`
je Segment aus den GraphHopper Path-Details `road_environment`/`street_name`
extrahiert (siehe `docs/superpowers/specs/2026-08-15-ferry-avoidance-design.md`).
"""

from __future__ import annotations

from tripplanner.routing.models import Coordinate, FaehrSegment, Route, RouteSegment

FAEHR_PUFFER_GRAD: float = 0.005
"""Pufferung (in Dezimalgrad, ca. 500 m bei den Breitengraden DE/DK/SE) um die
exakte Segmentgeometrie einer erkannten Fährverbindung, damit die daraus gebaute
GraphHopper Custom-Model-Area die komplette Fährlinie sicher abdeckt."""

UNNAMED_FERRY = "Unbenannte Fähre"


def erkenne_faehren(route: Route) -> list[FaehrSegment]:
    """Gruppiert zusammenhängende Fährsegmente einer Route zu `FaehrSegment`-Einträgen.

    Läuft einmal linear über `route.segments` und fasst aufeinanderfolgende
    Segmente mit `road_environment == "FERRY"` zu je einem `FaehrSegment`
    zusammen (Name aus dem ersten vorhandenen `strassenname` des Laufs, sonst
    "Unbenannte Fähre"; Länge als Summe der `laenge_m`; Bounding Box aus allen
    beteiligten `geometrie`-Koordinaten, gepuffert um `FAEHR_PUFFER_GRAD`).

    Args:
        route: Eine bereits berechnete Route (z. B. aus `RoutingProvider.berechne_route()`).

    Returns:
        Liste erkannter Fährverbindungen in Fahrtrichtung. Leer, wenn die Route
        keine Fährsegmente enthält oder `road_environment` nicht verfügbar war
        (z. B. `FakeRoutingProvider`-Routen).
    """
    ergebnis: list[FaehrSegment] = []
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


def run_to_ferry_segment(run: list[RouteSegment]) -> FaehrSegment:
    """Baut ein `FaehrSegment` aus einem zusammenhängenden Lauf von Fähr-`RouteSegment`s."""
    name = next((s.strassenname for s in run if s.strassenname), None) or UNNAMED_FERRY
    laenge_m = sum(s.laenge_m for s in run)

    koordinaten: list[Coordinate] = [koord for s in run for koord in s.geometrie]
    lats = [k[0] for k in koordinaten]
    lons = [k[1] for k in koordinaten]

    return FaehrSegment(
        name=name,
        laenge_m=laenge_m,
        bbox_sw=(min(lats) - FAEHR_PUFFER_GRAD, min(lons) - FAEHR_PUFFER_GRAD),
        bbox_no=(max(lats) + FAEHR_PUFFER_GRAD, max(lons) + FAEHR_PUFFER_GRAD),
        segment_index_start=run[0].segment_index,
        # Exklusiv (wie bei Python-Slices), damit `route.segments[start:end]`
        # genau den Fähr-Lauf ergibt - vom `optimization`-Modul genutzt, um
        # eine vom Nutzer vorgegebene Fährüberfahrt in der Zustandsgraph-
        # Suche in einem Sprung zu ueberspringen (siehe optimizer.py).
        segment_index_end=run[-1].segment_index + 1,
    )
