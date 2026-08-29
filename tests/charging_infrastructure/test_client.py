"""Tests für SuperchargeInfoClient."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from curl_cffi import AsyncSession

from tripplanner.charging_infrastructure.client import (
    CurlError,
    NodriverTeslaClient,
    SafariTeslaClient,
    SuperchargeInfoClient,
    TeslaLocationsClient,
    create_tesla_client,
)


@pytest.fixture
def mock_client() -> SuperchargeInfoClient:
    """Create a SuperchargeInfoClient with a mocked httpx.AsyncClient.

    The mock client's get method returns mock responses for all URLs.
    """
    mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)

    # Create fixture data
    fixture_path = (
        Path(__file__).parent.parent
        / "fixtures"
        / "charging_infrastructure"
        / "supercharge_info_response_3sites.json"
    )
    with open(fixture_path, encoding="utf-8") as f:
        sites_data = f.read()  # noqa: F841

    def get_side_effect(url: str, **kwargs: Any) -> Mock:
        mock_response = Mock()
        if "allSites" in url:
            mock_response.json.return_value = [
                {
                    "id": 3506,
                    "locationId": "BarcelonaUrbanessupercharger",
                    "name": "Barcelona, Spain - L'Illa Diagonal",
                    "status": "OPEN",
                    "address": {"country": "Spain", "region": "Europe"},
                    "gps": {"latitude": 41.3895, "longitude": 2.1337},
                    "stallCount": 4,
                    "powerKilowatt": 125,
                    "stalls": {"v2": 4},
                    "plugs": {"ccs2": 4, "type2": 4},
                    "dateOpened": "2021-07-01",
                },
                {
                    "id": 5678,
                    "locationId": "CopenhagenAirportsupercharger",
                    "name": "Copenhagen, Denmark - Kastrup",
                    "status": "OPEN",
                    "address": {"country": "Denmark", "region": "Europe"},
                    "gps": {"latitude": 55.6182, "longitude": 12.6509},
                    "stallCount": 8,
                    "powerKilowatt": 250,
                    "stalls": {"v3": 8},
                    "plugs": {"ccs2": 8},
                    "dateOpened": "2022-06-01",
                },
                {
                    "id": 9012,
                    "locationId": "MalmoUrbanessupercharger",
                    "name": "Malmö, Sweden - Urban",
                    "status": "OPEN",
                    "address": {"country": "Sweden", "region": "Europe"},
                    "gps": {"latitude": 55.5941, "longitude": 13.0039},
                    "stallCount": 8,
                    "powerKilowatt": 250,
                    "stalls": {"v3": 8},
                    "plugs": {"ccs2": 8},
                    "dateOpened": "2022-09-01",
                },
            ]
        elif "databaseInfo" in url:
            mock_response.json.return_value = {
                "lastModified": 1700000000000,
                "lastModifiedString": "2024-01-01",
            }
        elif "allChanges" in url:
            mock_response.json.return_value = [
                {"id": 1, "changeType": "UPDATE"},
            ]
        else:
            mock_response.json.return_value = {}
        return mock_response

    mock_httpx_client.get.side_effect = get_side_effect
    return SuperchargeInfoClient(client=mock_httpx_client)


@pytest.fixture
def sample_site() -> dict[str, Any]:
    """Sample site dict (Barcelona site from fixture)."""
    return {
        "id": 3506,
        "locationId": "BarcelonaUrbanessupercharger",
        "name": "Barcelona, Spain - L'Illa Diagonal",
        "status": "OPEN",
        "address": {"country": "Spain", "region": "Europe"},
        "gps": {"latitude": 41.3895, "longitude": 2.1337},
        "stallCount": 4,
        "powerKilowatt": 125,
        "stalls": {"v2": 4},
        "plugs": {"ccs2": 4, "type2": 4},
        "dateOpened": "2021-07-01",
    }


class TestSuperchargeInfoClient:
    """Tests für den SuperchargeInfoClient."""

    @pytest.mark.asyncio
    async def test_fetch_all_sites_returns_list(self, mock_client: SuperchargeInfoClient) -> None:
        """Prüft, dass allSites eine Liste zurückgibt."""
        sites = await mock_client.fetch_all_sites()
        assert isinstance(sites, list)
        assert len(sites) > 0

    @pytest.mark.asyncio
    async def test_fetch_all_sites_structure(
        self, mock_client: SuperchargeInfoClient, sample_site: dict[str, Any]
    ) -> None:
        """Prüft die Struktur eines Site-Eintrags (Pflichtfelder)."""
        sites = await mock_client.fetch_all_sites()
        site = sites[0]
        assert "id" in site
        assert "gps" in site
        assert "latitude" in site["gps"]
        assert "longitude" in site["gps"]
        assert "address" in site
        assert "region" in site["address"]
        assert "stallCount" in site
        assert "powerKilowatt" in site

    @pytest.mark.asyncio
    async def test_fetch_database_info(self, mock_client: SuperchargeInfoClient) -> None:
        """Prüft databaseInfo-Endpunkt."""
        info = await mock_client.fetch_database_info()
        assert "lastModified" in info
        assert isinstance(info["lastModified"], int)

    @pytest.mark.asyncio
    async def test_fetch_all_changes(self, mock_client: SuperchargeInfoClient) -> None:
        """Prüft allChanges-Endpunkt."""
        changes = await mock_client.fetch_all_changes()
        assert isinstance(changes, list)
        assert len(changes) > 0

    @pytest.mark.asyncio
    async def test_client_creates_own_httpx(self) -> None:
        """When no client passed, _client is an httpx.AsyncClient and _owns_client is True."""
        client = SuperchargeInfoClient()
        try:
            assert isinstance(client._client, httpx.AsyncClient)
            assert client._owns_client is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_close_does_nothing_when_not_owner(
        self, mock_client: SuperchargeInfoClient
    ) -> None:
        """With mock client, close doesn't call aclose on mock."""
        await mock_client.close()
        # The mock's aclose should not have been called since we don't own the client
        mock_client._client.aclose.assert_not_called()


class TestTeslaLocationsClient:
    """Tests fuer den TeslaLocationsClient (curl_cffi-basiert)."""

    _LOCATIONS_JSON: str = json.dumps(
        {
            "data": {
                "data": [
                    {
                        "uuid": "1001",
                        "location_url_slug": "berlinsupercharger",
                        "location_type": ["supercharger", "service"],
                        "latitude": 52.5,
                        "longitude": 13.4,
                        "inCN": False,
                        "inHkMoTw": False,
                    },
                    {
                        "uuid": "1002",
                        "location_url_slug": "hamburgservice",
                        "location_type": ["service"],
                        "latitude": 53.5,
                        "longitude": 10.0,
                        "inCN": False,
                        "inHkMoTw": False,
                    },
                    {
                        "uuid": "1003",
                        "location_url_slug": "munichsupercharger",
                        "location_type": ["supercharger"],
                        "latitude": 48.1,
                        "longitude": 11.5,
                        "inCN": False,
                        "inHkMoTw": False,
                    },
                    {
                        "uuid": "1004",
                        "location_url_slug": "chinasupercharger",
                        "location_type": ["supercharger"],
                        "latitude": 31.2,
                        "longitude": 121.4,
                        "inCN": True,
                        "inHkMoTw": False,
                    },
                ]
            }
        }
    )

    _DETAIL_BERLIN_JSON: str = json.dumps(
        {
            "data": {
                "marketing": {"display_name": "Berlin Supercharger"},
                "supercharger_function": {
                    "actual_latitude": "52.5000",
                    "actual_longitude": "13.4000",
                    "num_charger_stalls": "12",
                    "installed_full_power": "250",
                    "open_to_non_tesla": True,
                },
                "key_data": {
                    "status": {"name": "Open"},
                    "geo_point": {"lat": 52.5, "lon": 13.4},
                },
                "functions": [{"opening_date": "2022-06-01"}],
            }
        }
    )

    _DETAIL_MUNICH_JSON: str = json.dumps(
        {
            "data": {
                "marketing": {"display_name": "Munich Supercharger"},
                "supercharger_function": {
                    "actual_latitude": "48.1000",
                    "actual_longitude": "11.5000",
                    "num_charger_stalls": "8",
                    "installed_full_power": "250",
                    "open_to_non_tesla": False,
                },
                "key_data": {
                    "status": {"name": "Open"},
                    "geo_point": {"lat": 48.1, "lon": 11.5},
                },
                "functions": [],
            }
        }
    )

    @staticmethod
    def _make_fake_response(body: str | bytes, status_code: int = 200) -> Any:
        """Builds a mock curl_cffi.Response for test assertions."""
        resp = AsyncMock()
        resp.status_code = status_code
        resp.text = body if isinstance(body, str) else body.decode()
        resp.content = body if isinstance(body, bytes) else body.encode()
        resp.request = AsyncMock()
        resp.request.method = "GET"
        resp.request.url = "https://example.com/test"
        return resp

    @pytest.mark.asyncio
    async def test_fetch_locations(self) -> None:
        """Prueft fetch_locations gibt Liste zurueck."""
        resp = self._make_fake_response(self._LOCATIONS_JSON)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        locations = await client.fetch_locations("DE")
        await client.close()

        assert len(locations) == 4
        assert locations[0]["uuid"] == "1001"

    @pytest.mark.asyncio
    async def test_fetch_locations_empty(self) -> None:
        """Prueft fetch_locations bei leerem Ergebnis."""
        empty = json.dumps({"data": {"data": []}})
        resp = self._make_fake_response(empty)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        locations = await client.fetch_locations("XX")
        await client.close()

        assert locations == []

    @pytest.mark.asyncio
    async def test_fetch_location_details(self) -> None:
        """Prueft fetch_location_details."""
        resp = self._make_fake_response(self._DETAIL_BERLIN_JSON)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        detail = await client.fetch_location_details("berlinsupercharger")
        await client.close()

        assert detail["marketing"]["display_name"] == "Berlin Supercharger"
        assert detail["supercharger_function"]["num_charger_stalls"] == "12"

    @pytest.mark.asyncio
    async def test_fetch_all_supercharger_details(self) -> None:
        """Prueft vollstaendigen supercharger-detail-flow."""
        responses = [
            self._make_fake_response(self._LOCATIONS_JSON),
            self._make_fake_response(self._DETAIL_BERLIN_JSON),
            self._make_fake_response(self._DETAIL_MUNICH_JSON),
        ]
        call_idx = 0

        async def mock_get(url: str, **kwargs: Any) -> Any:
            nonlocal call_idx
            resp = responses[call_idx]
            resp.request.url = url
            call_idx += 1
            return resp

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = mock_get

        client = TeslaLocationsClient(client=mock_session)
        details = await client.fetch_all_supercharger_details("DE", delay_s=0)
        await client.close()

        assert len(details) == 2
        slugs = {d["_slug"] for d in details}
        assert slugs == {"berlinsupercharger", "munichsupercharger"}

    @pytest.mark.asyncio
    async def test_curl_error_raises(self) -> None:
        """Prueft dass Netzwerkfehler eine CurlError ausloesen."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(side_effect=OSError("Connection refused"))

        client = TeslaLocationsClient(client=mock_session)
        with pytest.raises(TeslaLocationsClient.CurlError):
            await client.fetch_locations("DE")
        await client.close()

    @pytest.mark.asyncio
    async def test_fetch_pricing_html_returns_raw_body(self) -> None:
        """Prueft, dass fetch_pricing_html den Rohtext liefert (kein JSON-Parsing)."""
        html = '<html><script id="__NEXT_DATA__">{"a": 1}</script></html>'
        resp = self._make_fake_response(html)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        body = await client.fetch_pricing_html("rhudensupercharger")
        await client.close()

        assert body == html

    @pytest.mark.asyncio
    async def test_fetch_pricing_html_raises_on_waf_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ein anhaltender 403 (WAF-Block) loest nach allen Retries CurlError aus."""
        import tripplanner.charging_infrastructure.clients.tesla_curl as tesla_curl_module

        monkeypatch.setattr(tesla_curl_module.asyncio, "sleep", AsyncMock())
        resp = self._make_fake_response("<html>Access Denied</html>", status_code=403)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        with pytest.raises(TeslaLocationsClient.CurlError, match="WAF-Block"):
            await client.fetch_pricing_html("rhudensupercharger")
        await client.close()

        assert mock_session.get.await_count == 4

    @pytest.mark.asyncio
    async def test_fetch_raises_on_waf_block_page_with_http_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Akamai liefert die Blockseite manchmal mit HTTP 200 statt 403/429 -
        muss trotzdem als Block erkannt werden statt als kaputtes JSON."""
        import tripplanner.charging_infrastructure.clients.tesla_curl as tesla_curl_module

        monkeypatch.setattr(tesla_curl_module.asyncio, "sleep", AsyncMock())
        resp = self._make_fake_response(
            "<html><title>Access Denied</title>https://errors.edgesuite.net/18.foo</html>",
            status_code=200,
        )
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        with pytest.raises(TeslaLocationsClient.CurlError, match="WAF-Block"):
            await client.fetch_locations("DE")
        await client.close()

        assert mock_session.get.await_count == 4

    @pytest.mark.asyncio
    async def test_fetch_pricing_html_url_encodes_slug(self) -> None:
        """Der Slug wird URL-encoded in die Anfrage-URL eingesetzt."""
        captured_url: str | None = None

        async def capture_get(url: str, **kwargs: Any) -> Any:
            nonlocal captured_url
            captured_url = url
            return self._make_fake_response("body")

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = capture_get

        client = TeslaLocationsClient(client=mock_session)
        await client.fetch_pricing_html("a slug/with special")
        await client.close()

        assert "a%20slug%2Fwith%20special" in captured_url

    @pytest.mark.asyncio
    async def test_close_only_closes_owned_session(self) -> None:
        """Ein extern uebergebener Session wird nicht geschlossen."""
        external_session = AsyncMock(spec=AsyncSession)
        client = TeslaLocationsClient(client=external_session)
        await client.close()
        external_session.close.assert_not_called()


class _FakeFetcher:
    """Minimaler NodriverFetcher-Stub fuer Unit-Tests.

    Liefert eine konfigurierbare Antwort (Status + Body) pro URL und zeichnet
    alle Aufrufe auf, ohne einen echten Browser zu starten. ``set_sequence``
    erlaubt unterschiedliche Antworten ueber aufeinanderfolgende Aufrufe
    derselben URL hinweg (fuer Retry-Tests). ``set_run_result``/
    ``set_run_sequence`` konfigurieren ``run()`` (genutzt vom Human-Flow-
    Client) unabhaengig von ``fetch()``.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._by_url: dict[str, tuple[int, str]] = {}
        self._sequences: dict[str, list[tuple[int, str]]] = {}
        self._default: tuple[int, str] = (200, "")
        self.closed = False
        self.restart_count = 0
        self.run_calls = 0
        self._run_result: Any = None
        self._run_sequence: list[Any] | None = None

    def set_response(self, url: str, status: int, body: str) -> None:
        self._by_url[url] = (status, body)

    def set_sequence(self, url: str, responses: list[tuple[int, str]]) -> None:
        """Liefert bei aufeinanderfolgenden Aufrufen von ``url`` je einen
        Eintrag aus ``responses``; der letzte Eintrag wiederholt sich."""
        self._sequences[url] = list(responses)

    def set_default(self, status: int, body: str) -> None:
        self._default = (status, body)

    def fetch(self, url: str) -> tuple[int, str]:
        self.calls.append(url)
        sequence = self._sequences.get(url)
        if sequence:
            return sequence.pop(0) if len(sequence) > 1 else sequence[0]
        if url in self._by_url:
            return self._by_url[url]
        return self._default

    def set_run_result(self, result: Any) -> None:
        """Jeder ``run()``-Aufruf liefert ``result`` (oder wirft es, falls
        eine ``Exception``)."""
        self._run_result = result

    def set_run_sequence(self, results: list[Any]) -> None:
        """Liefert bei aufeinanderfolgenden ``run()``-Aufrufen je einen
        Eintrag aus ``results`` (Wert oder zu werfende ``Exception``); der
        letzte Eintrag wiederholt sich."""
        self._run_sequence = list(results)

    def run(self, coro_factory: Any, timeout_s: float) -> Any:
        """Simuliert ``NodriverBrowserFetcher.run`` ohne echten Browser.

        Ruft ``coro_factory`` absichtlich NICHT auf (kein echtes ``Browser``-
        Objekt vorhanden) - Human-Flow-Unit-Tests pruefen ausschliesslich die
        Orchestrierung (Retry/Fallback/Parsing) des Clients, nicht die
        CDP-Interaktion selbst (siehe ``test_human_flow_integration.py`` fuer
        Letzteres)."""
        self.run_calls += 1
        if self._run_sequence:
            result = (
                self._run_sequence.pop(0) if len(self._run_sequence) > 1 else self._run_sequence[0]
            )
        else:
            result = self._run_result
        if isinstance(result, Exception):
            raise result
        return result

    def restart(self) -> None:
        self.restart_count += 1

    def close(self) -> None:
        self.closed = True


class TestCreateTeslaClient:
    """Tests fuer die Factory-Funktion ``create_tesla_client``."""

    def test_returns_curl_cffi_when_requested(self) -> None:
        client = create_tesla_client(transport="curl_cffi")
        assert isinstance(client, TeslaLocationsClient)

    def test_returns_safari_by_default(self) -> None:
        client = create_tesla_client()
        assert isinstance(client, SafariTeslaClient)
        assert client._delay == 0.5

    def test_unknown_transport_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unbekannter Tesla-Client-Transport"):
            create_tesla_client(transport="magic")

    def test_factory_respects_custom_args(self) -> None:
        client = create_tesla_client(transport="curl_cffi", rate_limit_delay_s=1.25)
        assert client._delay == 1.25

    def test_nodriver_falls_back_to_curl_cffi_on_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wenn nodriver nicht installiert ist, wird transparent curl_cffi genutzt."""
        import tripplanner.charging_infrastructure.client as client_module

        def fake_import_module(name: str) -> object:
            if name == "nodriver":
                raise ImportError("simulated missing")
            return importlib.import_module(name)

        monkeypatch.setattr(client_module.importlib, "import_module", fake_import_module)
        client = create_tesla_client(transport="nodriver")
        assert isinstance(client, TeslaLocationsClient)


class TestNodriverTeslaClient:
    """Tests fuer ``NodriverTeslaClient`` mit injiziertem Fake-Fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_locations_parses_json(self) -> None:
        """Prueft fetch_locations gibt Liste zurueck."""

        fetcher = _FakeFetcher()
        locations_json = json.dumps(
            {
                "data": {
                    "data": [
                        {
                            "uuid": "1001",
                            "location_url_slug": "berlinsupercharger",
                            "location_type": ["supercharger"],
                            "latitude": 52.5,
                            "longitude": 13.4,
                        }
                    ]
                }
            }
        )
        fetcher.set_response(
            "https://www.tesla.com/api/findus/get-locations?country=DE&view=map",
            200,
            locations_json,
        )

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            locations = await client.fetch_locations("DE")
        finally:
            await client.close()

        assert len(locations) == 1
        assert locations[0]["location_url_slug"] == "berlinsupercharger"
        assert len(fetcher.calls) == 1

    @pytest.mark.asyncio
    async def test_fetch_location_details_parses_json(self) -> None:
        """Prueft fetch_location_details."""

        detail_json = json.dumps(
            {
                "data": {
                    "marketing": {"display_name": "Berlin Supercharger"},
                }
            }
        )
        url = (
            "https://www.tesla.com/api/findus/get-location-details"
            "?locationSlug=berlinsupercharger&functionTypes=party"
            "&locale=de_DE&isInHkMoTw=false"
        )
        fetcher = _FakeFetcher()
        fetcher.set_response(url, 200, detail_json)

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            detail = await client.fetch_location_details("berlinsupercharger")
        finally:
            await client.close()

        assert detail["marketing"]["display_name"] == "Berlin Supercharger"

    @pytest.mark.asyncio
    async def test_fetch_pricing_html_returns_raw_body(self) -> None:
        """Prueft, dass fetch_pricing_html den Rohtext liefert (kein JSON-Parsing)."""

        html = '<html><script id="__NEXT_DATA__">{"a": 1}</script></html>'
        url = "https://www.tesla.com/de_de/findus/location/supercharger/rhudensupercharger"
        fetcher = _FakeFetcher()
        fetcher.set_response(url, 200, html)

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            body = await client.fetch_pricing_html("rhudensupercharger")
        finally:
            await client.close()

        assert body == html

    @pytest.mark.asyncio
    async def test_fetch_pricing_html_raises_on_waf_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ein anhaltender 403 (WAF-Block) loest nach allen Retries CurlError aus."""
        import tripplanner.charging_infrastructure.clients.nodriver as nodriver_module

        monkeypatch.setattr(nodriver_module.asyncio, "sleep", AsyncMock())
        url = "https://www.tesla.com/de_de/findus/location/supercharger/rhudensupercharger"
        fetcher = _FakeFetcher()
        fetcher.set_response(url, 403, "<html>Access Denied</html>")

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            with pytest.raises(CurlError, match="WAF-Block"):
                await client.fetch_pricing_html("rhudensupercharger")
        finally:
            await client.close()

        # Ein frischer Browser (neuer Fingerprint) wird vor jedem Retry
        # gestartet - 4 Versuche = 3 Restarts.
        assert len(fetcher.calls) == 4
        assert fetcher.restart_count == 3

    @pytest.mark.asyncio
    async def test_fetch_raises_on_waf_block_page_with_http_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tesla liefert die Akamai-Blockseite manchmal mit HTTP 200 statt 403 -
        muss trotzdem als Block erkannt und nicht als leeres/kaputtes JSON
        durchgereicht werden."""
        import tripplanner.charging_infrastructure.clients.nodriver as nodriver_module

        monkeypatch.setattr(nodriver_module.asyncio, "sleep", AsyncMock())
        fetcher = _FakeFetcher()
        fetcher.set_default(
            200,
            "<html><head><title>Access Denied</title></head><body>"
            "You don't have permission... https://errors.edgesuite.net/18.foo"
            "</body></html>",
        )

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            with pytest.raises(CurlError, match="WAF-Block"):
                await client.fetch_locations("DE")
        finally:
            await client.close()

        assert len(fetcher.calls) == 4
        assert fetcher.restart_count == 3

    @pytest.mark.asyncio
    async def test_fetch_locations_recovers_after_transient_waf_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ein einmaliger WAF-Block gefolgt von Erfolg liefert die Daten,
        ohne dass der Aufrufer den Fehler sieht."""
        import tripplanner.charging_infrastructure.clients.nodriver as nodriver_module

        monkeypatch.setattr(nodriver_module.asyncio, "sleep", AsyncMock())
        url = "https://www.tesla.com/api/findus/get-locations?country=DE&view=map"
        locations_json = json.dumps({"data": {"data": [{"uuid": "1"}]}})
        fetcher = _FakeFetcher()
        fetcher.set_sequence(
            url,
            [
                (200, "<html><title>Access Denied</title></html>"),
                (200, locations_json),
            ],
        )

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            locations = await client.fetch_locations("DE")
        finally:
            await client.close()

        assert locations == [{"uuid": "1"}]
        assert len(fetcher.calls) == 2
        assert fetcher.restart_count == 1

    @pytest.mark.asyncio
    async def test_fetch_raises_on_too_many_requests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ein anhaltendes 429 loest nach allen Retries CurlError aus."""
        import tripplanner.charging_infrastructure.clients.nodriver as nodriver_module

        monkeypatch.setattr(nodriver_module.asyncio, "sleep", AsyncMock())
        fetcher = _FakeFetcher()
        fetcher.set_default(429, "rate limited")

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            with pytest.raises(CurlError, match="429"):
                await client.fetch_locations("DE")
        finally:
            await client.close()

        assert len(fetcher.calls) == 4

    @pytest.mark.asyncio
    async def test_fetch_raises_on_server_error(self) -> None:
        """Ein 500 loest sofort (ohne Retry) CurlError aus - nicht WAF-bedingt."""

        fetcher = _FakeFetcher()
        fetcher.set_default(500, "boom")

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            with pytest.raises(CurlError, match="HTTP 500"):
                await client.fetch_locations("DE")
        finally:
            await client.close()

        assert len(fetcher.calls) == 1

    @pytest.mark.asyncio
    async def test_fetch_raises_on_empty_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Eine anhaltend leere Antwort loest nach allen Retries CurlError aus."""
        import tripplanner.charging_infrastructure.clients.nodriver as nodriver_module

        monkeypatch.setattr(nodriver_module.asyncio, "sleep", AsyncMock())
        fetcher = _FakeFetcher()
        fetcher.set_default(200, "   ")

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            with pytest.raises(CurlError, match="empty response"):
                await client.fetch_locations("DE")
        finally:
            await client.close()

        assert len(fetcher.calls) == 4

    @pytest.mark.asyncio
    async def test_close_only_closes_owned_fetcher(self) -> None:
        """Ein extern uebergebener Fetcher wird nicht geschlossen."""

        external = _FakeFetcher()
        client = NodriverTeslaClient(fetcher=external)
        await client.close()
        assert external.closed is False

    @pytest.mark.asyncio
    async def test_close_closes_owned_fetcher(self) -> None:
        """Ein selbst erzeugter Fetcher wird via close() beendet."""

        client = NodriverTeslaClient()
        # Ersetze den echten Browser-Fetcher durch einen Fake, der nur den
        # ``closed``-Marker setzt - ohne den echten Browser zu starten.
        fake = _FakeFetcher()
        client._fetcher = fake  # type: ignore[assignment]
        client._owns_fetcher = True
        await client.close()
        assert fake.closed is True

    @pytest.mark.asyncio
    async def test_fetch_all_supercharger_details_filters_non_supercharger(
        self,
    ) -> None:
        """Prueft Vollstaendigen supercharger-detail-flow (Filter, Reihenfolge)."""

        locations_url = "https://www.tesla.com/api/findus/get-locations?country=DE&view=map"
        locations_json = json.dumps(
            {
                "data": {
                    "data": [
                        {
                            "uuid": "1001",
                            "location_url_slug": "berlinsupercharger",
                            "location_type": ["supercharger"],
                            "latitude": 52.5,
                            "longitude": 13.4,
                            "inCN": False,
                            "inHkMoTw": False,
                        },
                        {
                            "uuid": "1002",
                            "location_url_slug": "hamburgservice",
                            "location_type": ["service"],
                            "latitude": 53.5,
                            "longitude": 10.0,
                            "inCN": False,
                            "inHkMoTw": False,
                        },
                        {
                            "uuid": "1003",
                            "location_url_slug": "chinasupercharger",
                            "location_type": ["supercharger"],
                            "latitude": 31.2,
                            "longitude": 121.4,
                            "inCN": True,
                            "inHkMoTw": False,
                        },
                    ]
                }
            }
        )
        detail_berlin = json.dumps({"data": {"marketing": {"display_name": "Berlin"}}})
        detail_url_berlin = (
            "https://www.tesla.com/api/findus/get-location-details"
            "?locationSlug=berlinsupercharger&functionTypes=party"
            "&locale=de_DE&isInHkMoTw=false"
        )

        fetcher = _FakeFetcher()
        fetcher.set_response(locations_url, 200, locations_json)
        fetcher.set_response(detail_url_berlin, 200, detail_berlin)

        client = NodriverTeslaClient(fetcher=fetcher)
        try:
            details = await client.fetch_all_supercharger_details("DE", delay_s=0)
        finally:
            await client.close()

        # nur Berlin, nicht Hamburg (service) oder China (inCN)
        assert len(details) == 1
        assert details[0]["_slug"] == "berlinsupercharger"
        assert details[0]["_uuid"] == "1001"


class TestNodriverHumanFlowTeslaClient:
    """Tests fuer ``NodriverHumanFlowTeslaClient`` (Karten-Suche-Detail-Flow).

    Der injizierte ``_FakeFetcher.run()`` ruft die eigentliche
    Human-Flow-Koroutine (CDP-Navigation/Tippen) absichtlich nicht auf -
    getestet wird ausschliesslich die Orchestrierung des Clients (Retry,
    WAF-Block-Erkennung, Fallback auf die Basisklasse, Merge der
    Ladepunkt-Daten). Die echte CDP-Interaktion deckt
    ``test_human_flow_integration.py`` gegen einen lokalen Browser ab.
    """

    @pytest.mark.asyncio
    async def test_search_locations_parses_results(self) -> None:
        """Ein erfolgreicher Human-Flow-Such-Body wird zu einer Liste geparst."""
        from tripplanner.charging_infrastructure.client import NodriverHumanFlowTeslaClient

        fetcher = _FakeFetcher()
        fetcher.set_run_result(json.dumps({"data": [{"location_url_slug": "testsupercharger"}]}))

        client = NodriverHumanFlowTeslaClient(fetcher=fetcher)
        try:
            results = await client.search_locations("Test City")
        finally:
            await client.close()

        assert results == [{"location_url_slug": "testsupercharger"}]
        assert fetcher.run_calls == 1

    @pytest.mark.asyncio
    async def test_search_locations_retries_after_transient_waf_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ein einmaliger WAF-Block gefolgt von Erfolg liefert die Ergebnisse."""
        import tripplanner.charging_infrastructure.clients.human_flow as human_flow_module
        from tripplanner.charging_infrastructure.client import NodriverHumanFlowTeslaClient

        monkeypatch.setattr(human_flow_module.asyncio, "sleep", AsyncMock())
        fetcher = _FakeFetcher()
        fetcher.set_run_sequence(
            [
                "<html><title>Access Denied</title></html>",
                json.dumps({"data": [{"location_url_slug": "testsupercharger"}]}),
            ]
        )

        client = NodriverHumanFlowTeslaClient(fetcher=fetcher)
        try:
            results = await client.search_locations("Test City")
        finally:
            await client.close()

        assert results == [{"location_url_slug": "testsupercharger"}]
        assert fetcher.run_calls == 2
        assert fetcher.restart_count == 1

    @pytest.mark.asyncio
    async def test_search_locations_raises_after_exhausting_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ein anhaltender WAF-Block loest nach allen Human-Flow-Versuchen CurlError aus."""
        import tripplanner.charging_infrastructure.clients.human_flow as human_flow_module
        from tripplanner.charging_infrastructure.client import NodriverHumanFlowTeslaClient

        monkeypatch.setattr(human_flow_module.asyncio, "sleep", AsyncMock())
        fetcher = _FakeFetcher()
        fetcher.set_run_result("<html><title>Access Denied</title></html>")

        client = NodriverHumanFlowTeslaClient(fetcher=fetcher)
        try:
            with pytest.raises(CurlError, match="WAF-Block"):
                await client.search_locations("Test City")
        finally:
            await client.close()

        assert fetcher.run_calls == 2
        assert fetcher.restart_count == 1

    @pytest.mark.asyncio
    async def test_fetch_location_details_merges_charger_data(self) -> None:
        """Erfolgreicher Human-Flow liefert Standortdaten angereichert um ``_charger_details``."""
        from tripplanner.charging_infrastructure.client import NodriverHumanFlowTeslaClient

        fetcher = _FakeFetcher()
        fetcher.set_run_result(
            {
                "location-details": json.dumps(
                    {"data": {"marketing": {"display_name": "Test SC"}}}
                ),
                "charger-details": json.dumps({"data": {"stall_count": 8}}),
            }
        )

        client = NodriverHumanFlowTeslaClient(fetcher=fetcher)
        try:
            detail = await client.fetch_location_details("testsupercharger")
        finally:
            await client.close()

        assert detail["marketing"]["display_name"] == "Test SC"
        assert detail["_charger_details"] == {"stall_count": 8}
        assert fetcher.run_calls == 1

    @pytest.mark.asyncio
    async def test_fetch_location_details_falls_back_to_direct_api_on_persistent_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nach erschoepften Human-Flow-Versuchen faellt der Client auf den
        direkten JSON-API-GET der Basisklasse zurueck."""
        import tripplanner.charging_infrastructure.clients.human_flow as human_flow_module
        from tripplanner.charging_infrastructure.client import NodriverHumanFlowTeslaClient

        monkeypatch.setattr(human_flow_module.asyncio, "sleep", AsyncMock())
        fetcher = _FakeFetcher()
        fetcher.set_run_result(
            {
                "location-details": "<html><title>Access Denied</title></html>",
                "charger-details": "<html><title>Access Denied</title></html>",
            }
        )
        direct_api_url = (
            "https://www.tesla.com/api/findus/get-location-details"
            "?locationSlug=testsupercharger&functionTypes=party"
            "&locale=de_DE&isInHkMoTw=false"
        )
        fetcher.set_response(
            direct_api_url,
            200,
            json.dumps({"data": {"marketing": {"display_name": "Fallback SC"}}}),
        )

        client = NodriverHumanFlowTeslaClient(fetcher=fetcher)
        try:
            detail = await client.fetch_location_details("testsupercharger")
        finally:
            await client.close()

        assert detail["marketing"]["display_name"] == "Fallback SC"
        assert "_charger_details" not in detail
        assert fetcher.run_calls == 2
        assert fetcher.calls == [direct_api_url]

    @pytest.mark.asyncio
    async def test_fetch_location_details_falls_back_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ein Human-Flow-Fehler (z.B. Such-Input nicht gefunden) faellt ebenfalls
        auf den direkten JSON-API-GET zurueck."""
        import tripplanner.charging_infrastructure.clients.human_flow as human_flow_module
        from tripplanner.charging_infrastructure.client import NodriverHumanFlowTeslaClient

        monkeypatch.setattr(human_flow_module.asyncio, "sleep", AsyncMock())
        fetcher = _FakeFetcher()
        fetcher.set_run_result(CurlError("Human-Flow: Such-Input nicht gefunden"))
        direct_api_url = (
            "https://www.tesla.com/api/findus/get-location-details"
            "?locationSlug=testsupercharger&functionTypes=party"
            "&locale=de_DE&isInHkMoTw=false"
        )
        fetcher.set_response(
            direct_api_url,
            200,
            json.dumps({"data": {"marketing": {"display_name": "Fallback SC"}}}),
        )

        client = NodriverHumanFlowTeslaClient(fetcher=fetcher)
        try:
            detail = await client.fetch_location_details("testsupercharger")
        finally:
            await client.close()

        assert detail["marketing"]["display_name"] == "Fallback SC"
        assert fetcher.run_calls == 2

    @pytest.mark.asyncio
    async def test_search_locations_uses_custom_base_url(self) -> None:
        """``base_url`` wird an die Human-Flow-Koroutine durchgereicht."""
        from tripplanner.charging_infrastructure.client import NodriverHumanFlowTeslaClient

        fetcher = _FakeFetcher()
        fetcher.set_run_result(json.dumps({"data": []}))

        client = NodriverHumanFlowTeslaClient(fetcher=fetcher, base_url="http://127.0.0.1:9")
        try:
            await client.search_locations("Test City")
        finally:
            await client.close()

        assert client._base_url == "http://127.0.0.1:9"
