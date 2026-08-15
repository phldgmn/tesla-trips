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

_UNBENANNTE_FAEHRE = "Unbenannte Fähre"


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
    aktueller_lauf: list[RouteSegment] = []

    def _lauf_abschliessen() -> None:
        if aktueller_lauf:
            ergebnis.append(_lauf_zu_faehrsegment(aktueller_lauf))

    for segment in route.segments:
        if segment.road_environment == "FERRY":
            aktueller_lauf.append(segment)
        else:
            _lauf_abschliessen()
            aktueller_lauf = []
    _lauf_abschliessen()

    return ergebnis


def _lauf_zu_faehrsegment(lauf: list[RouteSegment]) -> FaehrSegment:
    """Baut ein `FaehrSegment` aus einem zusammenhängenden Lauf von Fähr-`RouteSegment`s."""
    name = next((s.strassenname for s in lauf if s.strassenname), None) or _UNBENANNTE_FAEHRE
    laenge_m = sum(s.laenge_m for s in lauf)

    koordinaten: list[Coordinate] = [koord for s in lauf for koord in s.geometrie]
    lats = [k[0] for k in koordinaten]
    lons = [k[1] for k in koordinaten]

    return FaehrSegment(
        name=name,
        laenge_m=laenge_m,
        bbox_sw=(min(lats) - FAEHR_PUFFER_GRAD, min(lons) - FAEHR_PUFFER_GRAD),
        bbox_no=(max(lats) + FAEHR_PUFFER_GRAD, max(lons) + FAEHR_PUFFER_GRAD),
    )
