"""Unit-Tests für `GraphHopperClient`: HTTP-Transport, Response-Parsing und Lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tripplanner.routing.client import GraphHopperClient
from tripplanner.routing.models import GraphHopperResponse

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "routing"

VALID_RESPONSE_JSON = (FIXTURES_DIR / "graphhopper_response_basic.json").read_text()


def _ok_handler(request: httpx.Request) -> httpx.Response:
    """Mock-Handler: akzeptiert jeden /route-Request mit der gültigen Fixture-Antwort."""
    assert request.url.path == "/route"
    return httpx.Response(200, text=VALID_RESPONSE_JSON)


def _status_handler(request: httpx.Request) -> httpx.Response:
    """Mock-Handler: liefert 400 Bad Request."""
    assert request.url.path == "/route"
    return httpx.Response(400, json={"message": "Bad points"})


def _make_client(handler: object, **kwargs: object) -> GraphHopperClient:
    """Erstelle GraphHopperClient mit gemocktem Transport (kein echter Netzwerkzugriff)."""
    client = GraphHopperClient(base_url="http://localhost:8989", api_key="test-key")
    client._client = httpx.AsyncClient(
        base_url="http://localhost:8989",
        timeout=60.0,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return client


class TestRouteSuccess:
    """Tests für erfolgreiche `route()`-Aufrufe mit Response-Parsing."""

    @pytest.mark.asyncio
    async def test_route_success_parses_response(self) -> None:
        """Erfolgreicher Call liefert GraphHopperResponse mit Pfaden."""
        client = _make_client(_ok_handler)
        try:
            response = await client.route(
                [
                    (52.5200, 13.4050),
                    (53.5511, 9.9937),
                ]
            )
            assert isinstance(response, GraphHopperResponse)
            assert len(response.paths) == 1
            assert response.paths[0].distance == 2345.6
            assert response.info.took == 4
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_route_swaps_lat_lon_order(self) -> None:
        """Koordinaten werden von (lat, lon) nach [lon, lat] umsortiert."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured.append(payload)
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route(
                [
                    (52.5200, 13.4050),
                    (53.5511, 9.9937),
                ]
            )
            assert captured[0]["points"] == [[13.4050, 52.5200], [9.9937, 53.5511]]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_route_default_profile_and_elevation(self) -> None:
        """Default-Profile 'car' und elevation=False werden korrekt gesendet."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured.append(payload)
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route([(52.5200, 13.4050), (53.5511, 9.9937)])
            assert captured[0]["profile"] == "car"
            assert captured[0]["elevation"] is False
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_route_passes_details_and_custom_model(self) -> None:
        """Optional `details` und `custom_model` werden in den Payload integriert."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured.append(payload)
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route(
                [(52.5200, 13.4050), (53.5511, 9.9937)],
                profile="truck",
                elevation=True,
                details=["road_class", "max_speed"],
                custom_model={"speed": {"factor": {"max": 80}}},
            )
            assert captured[0]["profile"] == "truck"
            assert captured[0]["elevation"] is True
            assert captured[0]["details"] == ["road_class", "max_speed"]
            assert captured[0]["custom_model"] == {"speed": {"factor": {"max": 80}}}
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_route_omits_details_and_custom_model_when_none(self) -> None:
        """`details=None` und `custom_model=None` werden nicht im Payload gesendet."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured.append(payload)
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route([(52.5200, 13.4050), (53.5511, 9.9937)])
            assert "details" not in captured[0]
            assert "custom_model" not in captured[0]
        finally:
            await client.close()


class TestRouteErrors:
    """Tests für Fehlerfälle der `route()`-Methode."""

    @pytest.mark.asyncio
    async def test_route_raises_value_error_for_too_few_points(self) -> None:
        """Bei < 2 Punkten wird ValueError geworfen, bevor ein HTTP-Aufruf stattfindet."""
        client = _make_client(_ok_handler)
        try:
            with pytest.raises(ValueError, match="Mindestens 2 Koordinaten"):
                await client.route([(52.5200, 13.4050)])
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_route_raises_http_status_error_on_4xx(self) -> None:
        """Bei 4xx-Response löst raise_for_status eine httpx.HTTPStatusError aus."""
        client = _make_client(_status_handler)
        try:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.route([(52.5200, 13.4050), (53.5511, 9.9937)])
            assert exc_info.value.response.status_code == 400
        finally:
            await client.close()


class TestChDisable:
    """Tests für automatisches `ch.disable` bei custom_model-Requests.

    GraphHopper lehnt `custom_model` ab, solange das Profil im CH ("speed
    mode") läuft - live gegen den Projekt-GraphHopper-Server verifiziert
    (Fehler: "The 'custom_model' parameter is currently not supported for
    speed mode, you need to disable speed mode with `ch.disable=true`.").
    """

    @pytest.mark.asyncio
    async def test_ch_disable_set_when_custom_model_present(self) -> None:
        """`ch.disable=True` wird gesetzt, sobald `custom_model` übergeben wird."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route(
                [(52.5200, 13.4050), (53.5511, 9.9937)],
                custom_model={
                    "priority": [{"if": "road_environment == FERRY", "multiply_by": 0.0}]
                },
            )
            assert captured[0]["ch.disable"] is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ch_disable_absent_when_custom_model_none(self) -> None:
        """Ohne custom_model wird `ch.disable` nicht gesendet (unverändertes Verhalten)."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = _make_client(handler)
        try:
            await client.route([(52.5200, 13.4050), (53.5511, 9.9937)])
            assert "ch.disable" not in captured[0]
        finally:
            await client.close()


class TestLifecycle:
    """Tests für close() und den async Context-Manager."""

    @pytest.mark.asyncio
    async def test_close_closes_underlying_httpx_client(self) -> None:
        """close() schließt den internen httpx AsyncClient."""
        client = _make_client(_ok_handler)
        await client.close()
        assert client._client.is_closed

    @pytest.mark.asyncio
    async def test_aenter_returns_self(self) -> None:
        """__aenter__ gibt den Client selbst zurück."""
        client = _make_client(_ok_handler)
        try:
            entered = await client.__aenter__()
            assert entered is client
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_aexit_closes_client(self) -> None:
        """__aexit__ schließt den HTTP-Client."""
        client = _make_client(_ok_handler)
        await client.__aexit__(None, None, None)
        assert client._client.is_closed

    @pytest.mark.asyncio
    async def test_context_manager_protocol(self) -> None:
        """Der `async with`-Block ruft erfolgreich die Route ab und schließt danach."""
        async with _make_client(_ok_handler) as client:
            response = await client.route(
                [
                    (52.5200, 13.4050),
                    (53.5511, 9.9937),
                ]
            )
            assert isinstance(response, GraphHopperResponse)
        # Nach dem Block ist der Client geschlossen.
        assert client._client.is_closed


class TestInit:
    """Tests für __init__-Verhalten."""

    def test_init_strips_trailing_slash_from_base_url(self) -> None:
        """base_url wird um einen abschließenden Slash bereinigt."""
        client = GraphHopperClient(base_url="http://localhost:8989/", api_key="key")
        assert client.base_url == "http://localhost:8989"
        assert client.api_key == "key"
        assert isinstance(client._client, httpx.AsyncClient)

    def test_init_defaults(self) -> None:
        """Default-Parameter werden korrekt ohne API-Key gesetzt."""
        client = GraphHopperClient()
        assert client.base_url == "http://localhost:8989"
        assert client.api_key is None
        assert client._client.base_url == httpx.URL("http://localhost:8989")
        assert client._client.timeout == httpx.Timeout(60.0, connect=3.0)


class TestRetry:
    """Wiederholung bei 502/503/504 (Report-Item 12)."""

    @staticmethod
    def _client_with(handler: object, max_attempts: int = 3) -> GraphHopperClient:
        client = GraphHopperClient(max_attempts=max_attempts, backoff_base_s=0.0)
        client._client = httpx.AsyncClient(
            base_url="http://localhost:8989",
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        )
        return client

    async def test_retries_503_then_succeeds(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) < 3:
                return httpx.Response(503)
            return httpx.Response(200, text=VALID_RESPONSE_JSON)

        client = self._client_with(handler)
        result = await client.route([(52.5, 13.4), (48.1, 11.6)])
        assert isinstance(result, GraphHopperResponse)
        assert len(calls) == 3

    async def test_gives_up_after_max_attempts(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(504)

        client = self._client_with(handler, max_attempts=3)
        with pytest.raises(httpx.HTTPStatusError):
            await client.route([(52.5, 13.4), (48.1, 11.6)])
        assert len(calls) == 3

    async def test_does_not_retry_4xx(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, json={"message": "Point out of bounds"})

        client = self._client_with(handler)
        with pytest.raises(httpx.HTTPStatusError, match="Point out of bounds"):
            await client.route([(52.5, 13.4), (48.1, 11.6)])
        assert len(calls) == 1

    async def test_info_is_retried_too(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(502)
            return httpx.Response(200, json={"version": "10"})

        client = self._client_with(handler)
        assert await client.info() == {"version": "10"}
        assert len(calls) == 2
