"""Tests für SuperchargeInfoClient."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from tripplanner.charging_infrastructure.client import (
    SuperchargeInfoClient,
    TeslaLocationsClient,
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
    """Tests fuer den TeslaLocationsClient (subprocess/curl-basiert)."""

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
    def _make_fake_process(
        stdout_data: str, returncode: int = 0, status_code: int = 200
    ) -> AsyncMock:
        """Erzeugt einen Mock-Prozess, der create_subprocess_exec zurueckgibt.

        Haengt den HTTP-Statuscode als letzte Zeile an (wie -w '%{http_code}').
        """
        full_output = f"{stdout_data}\n{status_code}"
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(full_output.encode(), b""))
        proc.returncode = returncode
        return proc

    @pytest.mark.asyncio
    async def test_fetch_locations(self) -> None:
        """Prueft fetch_locations gibt Liste zurueck."""
        proc = self._make_fake_process(self._LOCATIONS_JSON)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            client = TeslaLocationsClient()
            locations = await client.fetch_locations("DE")
        assert len(locations) == 4
        assert locations[0]["uuid"] == "1001"

    @pytest.mark.asyncio
    async def test_fetch_locations_empty(self) -> None:
        """Prueft fetch_locations bei leerem Ergebnis."""
        empty = json.dumps({"data": {"data": []}})
        proc = self._make_fake_process(empty)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            client = TeslaLocationsClient()
            locations = await client.fetch_locations("XX")
        assert locations == []

    @pytest.mark.asyncio
    async def test_fetch_location_details(self) -> None:
        """Prueft fetch_location_details."""
        proc = self._make_fake_process(self._DETAIL_BERLIN_JSON)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            client = TeslaLocationsClient()
            detail = await client.fetch_location_details("berlinsupercharger")
        assert detail["marketing"]["display_name"] == "Berlin Supercharger"
        assert detail["supercharger_function"]["num_charger_stalls"] == "12"

    @pytest.mark.asyncio
    async def test_fetch_all_supercharger_details(self) -> None:
        """Prueft vollstaendigen supercharger-detail-flow."""
        results = iter(
            [
                (f"{self._LOCATIONS_JSON}\n200".encode(), b""),
                (f"{self._DETAIL_BERLIN_JSON}\n200".encode(), b""),
                (f"{self._DETAIL_MUNICH_JSON}\n200".encode(), b""),
            ]
        )

        async def mock_subprocess(*args: Any, **kwargs: Any) -> AsyncMock:
            stdout_data, _ = next(results)
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(stdout_data, b""))
            proc.returncode = 0
            return proc

        with patch(
            "asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=mock_subprocess
        ):
            client = TeslaLocationsClient()
            details = await client.fetch_all_supercharger_details("DE", delay_s=0)
        assert len(details) == 2
        slugs = {d["_slug"] for d in details}
        assert slugs == {"berlinsupercharger", "munichsupercharger"}

    @pytest.mark.asyncio
    async def test_curl_error_raises(self) -> None:
        """Prueft dass curl-Fehler eine Exception ausloesen."""
        proc = self._make_fake_process("", returncode=7)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            client = TeslaLocationsClient()
            with pytest.raises(TeslaLocationsClient.CurlError):
                await client.fetch_locations("DE")
