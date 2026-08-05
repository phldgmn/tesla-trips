"""HTTP-Client für GraphHopper API.

Handles Authentifizierung, Request/Response Mapping für GraphHopper /route Endpoint.
"""

from __future__ import annotations

from httpx import AsyncClient

from tripplanner.routing.models import GraphHopperResponse

Coordinate = tuple[float, float]


class GraphHopperClient:
    """HTTP-Client für GraphHopper API. Handles Authentifizierung, Request/Response Mapping."""

    def __init__(self, base_url: str = "http://localhost:8989", api_key: str | None = None):
        """Initialisiert den GraphHopper Client.

        Args:
            base_url: Base URL des GraphHopper Servers (inkl. port, z. B. "http://localhost:8989")
            api_key: Optionaler API Key für Authentifizierung
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = AsyncClient(base_url=base_url, timeout=60.0)

    async def route(
        self,
        points: list[Coordinate],
        profile: str = "car",
        elevation: bool = False,
        details: list[str] | None = None,
        custom_model: dict[str, object] | None = None,
    ) -> GraphHopperResponse:
        """Ruft eine Route von GraphHopper ab.

        Args:
            points: Liste von (lat, lon) Koordinaten (mindestens 2)
            profile: GraphHopper Profilname (z. B. "car", "bike", "foot", oder benutzerdefiniert)
            elevation: Falls True, Elevation in Polyline einbeziehen
            details: Liste von gewünschten Path Details
                (z. B. ["road_class", "max_speed", "average_slope", "surface"])
            custom_model: Optionaler custom_model JSON für individuelles Fahrzeugprofil

        Returns:
            GraphHopperResponse mit decoded Polyline und Details

        Raises:
            ValueError: Wenn weniger als 2 Punkte übergeben werden
            httpx.HTTPStatusError: Bei HTTP-Fehlern (4xx/5xx)
        """
        min_points = 2
        if len(points) < min_points:
            raise ValueError("Mindestens 2 Koordinaten (Start und Ziel) sind erforderlich")

        # Umwandlung points: (lat, lon) → [lon, lat]
        gh_points = [[lon, lat] for lat, lon in points]
        payload = {"point": gh_points, "profile": profile, "elevation": elevation}

        if details:
            payload["details"] = details

        if custom_model:
            payload["custom_model"] = custom_model

        response = await self._client.post("/route", json=payload)
        response.raise_for_status()

        return GraphHopperResponse.model_validate(response.json())

    async def close(self) -> None:
        """Schließt den HTTP Client."""
        await self._client.aclose()

    async def __aenter__(self) -> GraphHopperClient:
        """Betritt den async Context-Manager und gibt den Client zurück."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Verlässt den async Context-Manager und schließt den HTTP-Client."""
        await self.close()
