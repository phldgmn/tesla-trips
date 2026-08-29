"""Fake construction provider for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tripplanner.construction.models import ConstructionProvider, ConstructionZone, Land

if TYPE_CHECKING:
    import tripplanner.routing.models as routing_models


class FakeConstructionProvider(ConstructionProvider):
    """Fake-Provider für Tests ohne externe API-Aufrufe."""

    def __init__(self, test_zones: list[ConstructionZone] | None = None) -> None:
        """Initialisiert den Fake-Provider mit optionalen Test-Zonen."""
        self.test_zones = test_zones or []
        self.fetch_construction_zones_calls: list[tuple[routing_models.Route, list[Land]]] = []

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Liefert die konfigurierten Test-Zonen, optional gefiltert nach Land."""
        self.fetch_construction_zones_calls.append((route, laender))
        if laender:
            return [zone for zone in self.test_zones if zone.land in laender]
        return self.test_zones.copy()