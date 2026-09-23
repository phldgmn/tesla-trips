"""HTTP-Client für GraphHopper API.

Handles Authentifizierung, Request/Response Mapping für GraphHopper /route Endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import suppress

from httpx import AsyncClient, AsyncHTTPTransport, HTTPStatusError, Response, Timeout

from tripplanner.routing.models import GraphHopperResponse

Coordinate = tuple[float, float]

logger = logging.getLogger(__name__)

# Gateway-/Überlast-Status, die GraphHopper u. a. beim Warmlaufen liefert.
# 4xx werden nie wiederholt (fehlerhafte Anfrage bleibt fehlerhaft).
_RETRYABLE_STATUS = frozenset({502, 503, 504})


class GraphHopperClient:
    """HTTP-Client für GraphHopper API. Handles Authentifizierung, Request/Response Mapping."""

    def __init__(
        self,
        base_url: str = "http://localhost:8989",
        api_key: str | None = None,
        *,
        max_attempts: int = 3,
        backoff_base_s: float = 0.5,
    ):
        """Initialisiert den GraphHopper Client.

        Args:
            base_url: Base URL des GraphHopper Servers (inkl. port, z. B. "http://localhost:8989")
            api_key: Optionaler API Key für Authentifizierung
            max_attempts: Versuche bei 502/503/504 (inkl. des ersten)
            backoff_base_s: Basis des exponentiellen Backoffs (mit Jitter)
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        # retries=2 wiederholt nur Verbindungsfehler (connect/reset), keine
        # HTTP-Statuscodes. Lange Routen mit `ch.disable=True` brauchen ein
        # großzügiges Read-Timeout.
        self._client = AsyncClient(
            base_url=base_url,
            transport=AsyncHTTPTransport(retries=2),
            timeout=Timeout(60.0, connect=3.0),
        )

    async def _send_with_retry(self, method: str, url: str, **kwargs: object) -> Response:
        """Sendet eine idempotente Anfrage; wiederholt 502/503/504 mit Backoff."""
        for attempt in range(1, self._max_attempts):
            response = await self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
            if response.status_code not in _RETRYABLE_STATUS:
                return response
            delay = self._backoff_base_s * 2 ** (attempt - 1) * random.uniform(0.5, 1.5)
            logger.warning(
                "GraphHopper %s %s returned %d (attempt %d/%d), retrying in %.2fs",
                method,
                url,
                response.status_code,
                attempt,
                self._max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
        return await self._client.request(method, url, **kwargs)  # type: ignore[arg-type]

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
        # GraphHopper erwartet im JSON-POST-Body den Schlüssel "points" (Plural,
        # GeoJSON-artiges Array), nicht "point" (das ist nur die Wiederholungs-
        # Query-Param-Syntax der GET-Variante: ?point=lat,lon&point=lat,lon).
        payload = {"points": gh_points, "profile": profile, "elevation": elevation}

        if details:
            payload["details"] = details

        if custom_model:
            payload["custom_model"] = custom_model
            # GraphHopper lehnt `custom_model` ab, solange das Profil im CH
            # ("speed mode") läuft - live gegen den Projekt-GraphHopper-Server
            # verifiziert (Fehler: "The 'custom_model' parameter is currently
            # not supported for speed mode, you need to disable speed mode
            # with `ch.disable=true`."). Muss bei JEDEM custom_model-Request
            # gesetzt werden, unabhängig vom Anwendungsfall.
            payload["ch.disable"] = True

        response = await self._send_with_retry("POST", "/route", json=payload)
        try:
            response.raise_for_status()
        except HTTPStatusError as exc:
            # GraphHopper liefert bei 4xx (z. B. "Point out of bounds", wenn
            # die Koordinaten außerhalb des geladenen OSM-Extrakts liegen)
            # eine aussagekräftige `message` im JSON-Body. httpx' generische
            # Fehlermeldung enthält diesen Text nicht - ohne ihn ist der
            # Fehler für Nutzer (siehe API-Fehlermeldung in api.py) nicht
            # diagnostizierbar. Body-Detail anhängen, falls vorhanden.
            detail = None
            with suppress(ValueError):
                detail = response.json().get("message")
            message = str(exc)
            if detail:
                message = f"{message} (GraphHopper: {detail})"
            raise HTTPStatusError(message, request=exc.request, response=exc.response) from exc

        return GraphHopperResponse.model_validate(response.json())

    async def info(self) -> dict[str, object]:
        """Ruft die GraphHopper `/info`-Metadaten ab.

        Enthält u. a. `encoded_values`: die Path-Details/Encoded-Values, die
        der verbundene Server tatsächlich unterstützt (abhängig von dessen
        `graph.encoded_values`-Konfiguration, z. B. `average_slope` setzt
        eine aktivierte Elevation-Quelle voraus). Wird von
        `GraphHopperRoutingProvider` genutzt, um nur unterstützte Path-Details
        anzufragen statt mit HTTP 400 zu scheitern.

        Returns:
            Rohes JSON-Dict der `/info`-Antwort.

        Raises:
            httpx.HTTPStatusError: Bei HTTP-Fehlern (4xx/5xx).
        """
        response = await self._send_with_retry("GET", "/info")
        response.raise_for_status()
        result: dict[str, object] = response.json()
        return result

    async def close(self) -> None:
        """Schließt den HTTP Client."""
        await self._client.aclose()

    async def __aenter__(self) -> GraphHopperClient:
        """Betritt den async Context-Manager und gibt den Client zurück."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Verlässt den async Context-Manager und schließt den HTTP-Client."""
        await self.close()
