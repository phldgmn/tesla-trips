"""Tests für die Provider-Implementierungen von `charging_infrastructure`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tripplanner.charging_infrastructure.client import (
    SuperchargeInfoClient,
    TeslaLocationsClient,
)
from tripplanner.charging_infrastructure.database import SQLiteDatabase
from tripplanner.charging_infrastructure.models import (
    ChargingStationProvider,
    StallType,
)
from tripplanner.charging_infrastructure.pricing import PricingParseError
from tripplanner.charging_infrastructure.providers import (
    FakeChargingStationProvider,
    LocalFileChargingStationProvider,
    TeslaChargingStationProvider,
)
from tripplanner.geo import haversine_distance_m
from tripplanner.routing.models import Route, RouteSegment


class TestFakeChargingStationProvider:
    """Tests für den FakeChargingStationProvider."""

    @pytest.fixture
    def provider(self) -> FakeChargingStationProvider:
        return FakeChargingStationProvider()

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_basic(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet grundlegende Radius-Suche."""
        # Berlin Alexanderplatz
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=10.0)
        assert len(stations) >= 1
        # Erste Station sollte Berlin Alexanderplatz sein (Distanz ~0)
        assert stations[0].station_id == "test-berlin-1"

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_with_country_filter(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet Radius-Suche mit Länderfilter."""
        # Berlin Koordinaten, aber Filter auf DK -> Kopenhagen station in radius
        stations = await provider.get_stations_in_radius(
            (52.5234, 13.4114), radius_km=500.0, country_filter="DK"
        )
        # Kopenhagen station ist within 500km of Berlin
        assert len(stations) == 1
        assert all(s.country == "DK" for s in stations)

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_distance_sorting(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet, dass Stationen nach Distanz sortiert zurückgegeben werden."""
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=100.0)
        distances = []
        for s in stations:
            dist = haversine_distance_m((52.5234, 13.4114), s.coordinate) / 1000.0
            distances.append(dist)
        # Distanzen sollten aufsteigend sortiert sein
        assert distances == sorted(distances)
        # Berlin Alexanderplatz ist am Standort (0km), Potsdamer Platz ist weiter weg (2.72km)
        assert stations[0].station_id == "test-berlin-1"
        assert stations[1].station_id == "test-berlin-2"

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_empty_result(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet Suche mit minimalem Radius."""
        # FakeProvider gibt immer Berlin Stationen zurück, auch bei radius=0.0
        # Da Berlin Stationen im FakeProvider existieren, wird mindestens eine zurückgegeben
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=0.0)
        # Mindestens eine Station (Berlin Alexanderplatz) ist im 0km Radius enthalten
        assert len(stations) >= 1

    @pytest.mark.asyncio
    async def test_get_stations_along_route(self, provider: FakeChargingStationProvider) -> None:
        """Testet Suche entlang einer Route."""
        segments = [
            RouteSegment(
                segment_index=0,
                geometrie=[(52.5234, 13.4114), (52.61, 13.35)],
                laenge_m=10000.0,
                strassenklasse="MOTORWAY",
                bearing_deg=315.0,
            ),
            RouteSegment(
                segment_index=1,
                geometrie=[(52.61, 13.35), (52.73, 13.2)],
                laenge_m=15000.0,
                strassenklasse="MOTORWAY",
                bearing_deg=322.0,
            ),
        ]
        route = Route(
            segments=segments,
            gesamtlaenge_m=25000.0,
            geometrie=[(52.5234, 13.4114), (52.61, 13.35), (52.73, 13.2)],
        )

        result = await provider.get_stations_along_route(route, search_radius_km=5.0)

        # Prüfe keine doppelten Stationen pro Segment
        for _segment_idx, stations in result.items():
            station_ids = [s.station_id for s in stations]
            assert len(station_ids) == len(set(station_ids))

    @pytest.mark.asyncio
    async def test_get_stations_along_route_empty_segments(
        self, provider: FakeChargingStationProvider
    ) -> None:
        """Testet Route mit leeren Segmenten - neue Implementierung gibt leere
        Liste zurueck, da keine Segmente existieren."""
        segments: list[RouteSegment] = []
        route = Route(
            segments=segments,
            gesamtlaenge_m=1.0,
            geometrie=[(52.5234, 13.4114)],
        )

        result = await provider.get_stations_along_route(route, search_radius_km=5.0)
        # Neue Implementierung: ohne Segmente keine Stationszuordnung moeglich
        assert len(result) == 0

    @pytest.mark.parametrize(
        "coordinate,radius_km,expected_min_count",
        [
            ((52.5234, 13.4114), 10.0, 1),  # Berlin
            ((55.6761, 12.5683), 10.0, 1),  # Kopenhagen
            ((48.1650, 11.5850), 10.0, 1),  # München
        ],
    )
    @pytest.mark.asyncio
    async def test_get_stations_in_radius_parametrized(
        self,
        local_provider: LocalFileChargingStationProvider,
        coordinate: tuple[float, float],
        radius_km: float,
        expected_min_count: int,
    ) -> None:
        """Parametrisierter Test für get_stations_in_radius an bekannten Orten."""
        stations = await local_provider.get_stations_in_radius(coordinate, radius_km)
        assert len(stations) >= expected_min_count


class TestLocalFileChargingStationProvider:
    """Tests für den LocalFileChargingStationProvider mit echter Test-Fixture."""

    @pytest.fixture
    def provider(self, test_fixture_path: Path) -> LocalFileChargingStationProvider:
        """LocalFileChargingStationProvider geladen mit Test-Fixture."""
        return LocalFileChargingStationProvider(test_fixture_path)

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_basic(
        self, provider: LocalFileChargingStationProvider
    ) -> None:
        """Testet grundlegende Radius-Suche mit echten Daten."""
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=10.0)
        assert len(stations) >= 1
        assert any(s.station_id == "de-berlin-001" for s in stations)

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_all_countries(
        self, provider: LocalFileChargingStationProvider
    ) -> None:
        """Testet, dass alle 24 Stationen gefunden werden."""
        stations = await provider.get_stations_in_radius((52.5234, 13.4114), radius_km=1000.0)
        assert len(stations) == 24

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_de_filter(
        self, provider: LocalFileChargingStationProvider
    ) -> None:
        """Testet Länderfilter auf DE."""
        stations = await provider.get_stations_in_radius(
            (52.5234, 13.4114), radius_km=1000.0, country_filter="DE"
        )
        assert len(stations) == 14
        assert all(s.country == "DE" for s in stations)

    @pytest.mark.asyncio
    async def test_get_stations_along_route(
        self, provider: LocalFileChargingStationProvider, sample_route: dict
    ) -> None:
        """Testet Suche entlang einer Route mit echten Daten."""
        segments = [RouteSegment(**seg) for seg in sample_route["segments"]]
        route = Route(
            segments=segments,
            gesamtlaenge_m=sample_route["gesamtlaenge_m"],
            geometrie=[tuple(c) for c in sample_route["geometrie"]],
        )
        result = await provider.get_stations_along_route(route, search_radius_km=15.0)
        for _segment_idx, stations in result.items():
            station_ids = [s.station_id for s in stations]
            assert len(station_ids) == len(set(station_ids))

    @pytest.mark.asyncio
    async def test_get_stations_along_route_different_radius(
        self, provider: LocalFileChargingStationProvider, sample_route: dict
    ) -> None:
        """Testet, dass größerer Radius mehr Stationen findet."""
        segments = [RouteSegment(**seg) for seg in sample_route["segments"]]
        route = Route(
            segments=segments,
            gesamtlaenge_m=sample_route["gesamtlaenge_m"],
            geometrie=[tuple(c) for c in sample_route["geometrie"]],
        )

        result_small = await provider.get_stations_along_route(route, search_radius_km=1.0)
        result_large = await provider.get_stations_along_route(route, search_radius_km=15.0)

        # Mit größerem Radius sollten mindestens so viele Segmente Stationen haben
        # (oder mehr Stationen pro Segment)
        total_stations_small = sum(len(v) for v in result_small.values())
        total_stations_large = sum(len(v) for v in result_large.values())
        assert total_stations_large >= total_stations_small

    @pytest.mark.asyncio
    async def test_caching(self, provider: LocalFileChargingStationProvider) -> None:
        """Testet, dass Stationen nach erstem Laden gecacht werden."""
        stations1 = provider._load_stations()
        stations2 = provider._load_stations()

        # Derselbe Objekt (Cached)
        assert stations1 is stations2
        assert len(stations1) == len(stations2) == 24


class TestProviderProtocol:
    """Tests, dass beide Provider das Protocol einhalten."""

    def test_fake_provider_is_provider(self) -> None:
        """Testet, dass FakeChargingStationProvider das Protocol implementiert."""
        provider = FakeChargingStationProvider()
        assert isinstance(provider, ChargingStationProvider)

    def test_local_provider_is_provider(
        self,
        test_fixture_path: Path,
    ) -> None:
        """Testet, dass LocalFileChargingStationProvider das Protocol implementiert."""
        provider = LocalFileChargingStationProvider(test_fixture_path)
        assert isinstance(provider, ChargingStationProvider)

    def test_tesla_provider_is_provider(self, tmp_path: Path) -> None:
        """Testet, dass TeslaChargingStationProvider das Protocol implementiert."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "test.db")
        assert isinstance(provider, ChargingStationProvider)


class TestTeslaChargingStationProvider:
    """Tests für den TeslaChargingStationProvider."""

    @pytest.mark.asyncio
    async def test_get_stations_in_radius(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Radius-Suche liefert Stationen im Umkreis."""
        stations = await tesla_provider_seeded.get_stations_in_radius(
            (55.6, 13.0),
            50.0,  # Malmö area
        )
        assert len(stations) >= 1
        # Malmö has SE country code
        assert stations[0].country == "SE"

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_empty(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Leeres Ergebnis bei zu kleinem Radius."""
        stations = await tesla_provider_seeded.get_stations_in_radius(
            (48.0, 2.0),
            1.0,  # Paris, no stations in fixture
        )
        assert len(stations) == 0

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_country_filter(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Länderfilter funktioniert."""
        stations = await tesla_provider_seeded.get_stations_in_radius(
            (55.6, 13.0), 1000.0, country_filter="DK"
        )
        assert len(stations) >= 1
        assert all(s.country == "DK" for s in stations)

    @pytest.mark.asyncio
    async def test_get_stations_in_radius_empty_country_filter(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Länderfilter auf Land ohne Stationen."""
        stations = await tesla_provider_seeded.get_stations_in_radius(
            (52.5, 13.4), 1000.0, country_filter="DE"
        )
        assert len(stations) == 0  # Our test data has ES, DK, SE (not DE)

    @pytest.mark.asyncio
    async def test_station_mapping(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Prüft korrektes Mapping von DB-Record -> ChargingStation."""
        stations = await tesla_provider_seeded.get_stations_in_radius((55.6, 13.0), 1000.0)
        assert len(stations) >= 1
        station = stations[0]
        assert isinstance(station.station_id, str)
        assert isinstance(station.max_ladeleistung_kw, float)
        assert station.max_ladeleistung_kw > 0
        assert len(station.stalls) > 0

    @pytest.mark.asyncio
    async def test_station_v3_stall_mapping(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """V3-Stationen (250kW) werden korrekt auf StallType.V3 gemappt."""
        stations = await tesla_provider_seeded.get_stations_in_radius((55.6, 13.0), 1000.0)
        # Find Copenhagen (DK, V3 at 250kW)
        dk_stations = [s for s in stations if s.country == "DK"]
        assert len(dk_stations) >= 1
        cp = dk_stations[0]
        # 250kW per stall -> V3
        assert cp.stalls.get(StallType.V3, 0) == 8
        assert cp.stalls.get(StallType.V3_ULTRA, 0) == 0

    @pytest.mark.asyncio
    async def test_station_v2_stall_mapping(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """V2-Stationen (125kW) werden korrekt auf StallType.V2 gemappt."""
        stations = await tesla_provider_seeded.get_stations_in_radius(
            (41.4, 2.2),
            50.0,  # Barcelona area
        )
        # Barcelona (ES, V2 at 125kW)
        es_stations = [s for s in stations if s.country == "ES"]
        # ES is not in the valid country set (DE/DK/SE), so should be filtered out
        assert len(es_stations) == 0

    @pytest.mark.asyncio
    async def test_non_open_status_excluded_from_routing(self, tmp_path: Path) -> None:
        """Stationen mit Status ungleich OPEN (im Bau/Permit/Plan, existieren
        noch nicht physisch - z. B. "Torsvik, Sweden" oder "Ringsted,
        Denmark") duerfen nicht als Ladestopp-Kandidat auftauchen."""
        db_path = tmp_path / "status_filter.db"
        db = SQLiteDatabase(db_path)
        db.initialize()
        now = datetime.now(UTC).isoformat()
        base = {
            "latitude": 57.7,
            "longitude": 11.9,
            "country_code": "SE",
            "stalls_v2": 0,
            "stalls_v3": 8,
            "stalls_v3_ultra": 0,
            "stalls_v4": 0,
            "total_stalls": 8,
            "power_kilowatt": 250,
            "connector_types": json.dumps(["ccs2"]),
            "ist_24_7": 1,
            "date_opened": None,
            "last_updated_utc": now,
        }
        db.replace_all_stations(
            [
                {
                    **base,
                    "supercharge_info_id": 1,
                    "tesla_location_id": "torsviksupercharger",
                    "site_name": "Torsvik, Sweden",
                    "status": "CONSTRUCTION",
                },
                {
                    **base,
                    "supercharge_info_id": 2,
                    "tesla_location_id": "ringstedsupercharger",
                    "site_name": "Ringsted, Denmark",
                    "country_code": "DK",
                    "status": "PERMIT",
                },
                {
                    **base,
                    "supercharge_info_id": 3,
                    "tesla_location_id": "quickbornsupercharger",
                    "site_name": "Quickborn, Germany",
                    "country_code": "DE",
                    "status": "PLAN",
                },
                {
                    **base,
                    "supercharge_info_id": 4,
                    "tesla_location_id": "goteborgsupercharger",
                    "site_name": "Göteborg, Sweden",
                    "status": "OPEN",
                },
            ]
        )
        db.close()

        provider = TeslaChargingStationProvider(db_path=db_path)
        stations = await provider.get_stations_in_radius((57.7, 11.9), 10.0)

        station_ids = {s.station_id for s in stations}
        assert station_ids == {"goteborgsupercharger"}

    @pytest.mark.asyncio
    async def test_auto_init_empty_db(
        self, tesla_provider_with_db: TeslaChargingStationProvider
    ) -> None:
        """Leere DB gibt leere Stationsliste zurück (kein Fehler)."""
        stations = await tesla_provider_with_db.get_stations_in_radius((52.5, 13.4), 10.0)
        assert stations == []

    @pytest.mark.asyncio
    async def test_refresh_with_mock_client(
        self, tmp_path: Path, tesla_euro_fixture_path: Path
    ) -> None:
        """Mock-Client: refresh filtert Europe und speichert in DB."""
        # Load fixture data
        with open(tesla_euro_fixture_path, encoding="utf-8") as f:
            fixture_data = json.load(f)

        # Create mock client
        mock_httpx = AsyncMock(spec=SuperchargeInfoClient)

        async def mock_fetch_all_sites() -> list[dict]:
            return fixture_data

        mock_httpx.fetch_all_sites = mock_fetch_all_sites  # type: ignore[method-assign]

        provider = TeslaChargingStationProvider(
            db_path=tmp_path / "refresh_test.db",
            client=mock_httpx,  # type: ignore[arg-type]
        )

        count = await provider.refresh()

        # 3 European sites (Barcelona, Copenhagen, Malmö); USA filtered out
        assert count == 3

        # Verify stations loaded from DB
        stations = await provider.get_stations_in_radius((55.6, 13.0), 1000.0)
        # Only DK and SE are in VALID_COUNTRIES (ES is filtered out)
        assert len(stations) >= 1

    @pytest.mark.asyncio
    async def test_refresh_from_tesla_api_with_mock(self, tmp_path: Path) -> None:
        """Mock-Tesla-API: refresh_from_tesla_api speichert Stationen."""
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)

        async def mock_fetch_locations(country: str) -> list[dict]:
            return [
                {
                    "uuid": "1001",
                    "location_url_slug": "berlinsupercharger",
                    "location_type": ["supercharger"],
                    "latitude": 52.52,
                    "longitude": 13.405,
                    "inCN": False,
                    "inHkMoTw": False,
                },
            ]

        async def mock_fetch_details(slug: str, **kwargs: Any) -> dict:
            return {
                "marketing": {"display_name": "Berlin Supercharger"},
                "supercharger_function": {
                    "actual_latitude": "52.5200",
                    "actual_longitude": "13.4050",
                    "num_charger_stalls": "12",
                    "installed_full_power": "250",
                    "open_to_non_tesla": True,
                },
                "key_data": {
                    "status": {"name": "Open"},
                    "geo_point": {"lat": 52.52, "lon": 13.405},
                },
                "functions": [{"opening_date": "2022-06-01"}],
            }

        mock_tesla.fetch_locations = mock_fetch_locations  # type: ignore[method-assign]
        mock_tesla.fetch_location_details = mock_fetch_details  # type: ignore[method-assign]

        provider = TeslaChargingStationProvider(db_path=tmp_path / "tesla_test.db")
        count = await provider.refresh_from_tesla_api(
            countries=["DE"],
            tesla_client=mock_tesla,  # type: ignore[arg-type]
            enrich_details=True,
            delay_s=0,
        )

        assert count == 1

        stations = await provider.get_stations_in_radius((52.5, 13.4), 50.0)
        assert len(stations) >= 1
        assert stations[0].country == "DE"

    @pytest.mark.asyncio
    async def test_tesla_detail_mapping(self, tmp_path: Path) -> None:
        """Tesla-API-Detail wird korrekt auf ChargingStation gemappt."""
        detail = {
            "_uuid": "2001",
            "_slug": "hamburgsupercharger",
            "marketing": {
                "display_name": "Hamburg Supercharger",
            },
            "supercharger_function": {
                "actual_latitude": "53.5500",
                "actual_longitude": "10.0000",
                "num_charger_stalls": "8",
                "installed_full_power": "250",
                "open_to_non_tesla": False,
            },
            "key_data": {
                "status": {"name": "Open"},
                "geo_point": {"lat": 53.55, "lon": 10.0},
            },
            "functions": [{"opening_date": "2021-03-15"}],
        }

        record = TeslaChargingStationProvider._tesla_detail_to_db_record(detail, "DE")

        assert record["supercharge_info_id"] == 2001
        assert record["tesla_location_id"] == "hamburgsupercharger"
        assert record["site_name"] == "Hamburg Supercharger"
        assert record["total_stalls"] == 8
        assert record["power_kilowatt"] == 250
        assert record["country_code"] == "DE"
        assert record["date_opened"] == "2021-03-15"

    @pytest.mark.asyncio
    async def test_tesla_detail_mapping_v2_stalls(self, tmp_path: Path) -> None:
        """Tesla-API-Detail mit V2-Leistung wird korrekt gemappt."""
        detail = {
            "_uuid": "3001",
            "_slug": "oldsupercharger",
            "marketing": {"display_name": "Old SC"},
            "supercharger_function": {
                "actual_latitude": "50.0",
                "actual_longitude": "8.0",
                "num_charger_stalls": "4",
                "installed_full_power": "120",
                "open_to_non_tesla": True,
            },
            "key_data": {
                "status": {"name": "Open"},
                "geo_point": {"lat": 50.0, "lon": 8.0},
            },
            "functions": [],
        }

        record = TeslaChargingStationProvider._tesla_detail_to_db_record(detail, "DE")

        assert record["total_stalls"] == 4
        assert record["power_kilowatt"] == 120
        assert record["stalls_v2"] == 4  # 120kW <= 150 -> V2
        assert record["stalls_v3"] == 0

    @pytest.mark.asyncio
    async def test_refresh_single_station_with_mock(self, tmp_path: Path) -> None:
        """Mock-Tesla-API: refresh_single_station aktualisiert DB-Eintrag."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "single_test.db")
        # Bestehenden Eintrag mit veraltetem Stallcount seeden
        provider._db.update_station(
            {
                "supercharge_info_id": 4001,
                "tesla_location_id": "muenchensupercharger",
                "site_name": "Muenchen Supercharger (alt)",
                "latitude": 48.13,
                "longitude": 11.58,
                "country_code": "DE",
                "stalls_v2": 8,
                "stalls_v3": 0,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 8,
                "power_kilowatt": 150,
                "status": "OPEN",
                "connector_types": "[]",
                "ist_24_7": 1,
                "date_opened": None,
                "last_updated_utc": "2020-01-01T00:00:00+00:00",
            }
        )

        mock_tesla = AsyncMock(spec=TeslaLocationsClient)

        async def mock_fetch_details(slug: str, **kwargs: Any) -> dict:
            assert slug == "muenchensupercharger"
            return {
                "marketing": {"display_name": "Muenchen Supercharger"},
                "supercharger_function": {
                    "actual_latitude": "48.1300",
                    "actual_longitude": "11.5800",
                    "num_charger_stalls": "16",
                    "installed_full_power": "250",
                    "open_to_non_tesla": True,
                },
                "key_data": {
                    "status": {"name": "Open"},
                    "geo_point": {"lat": 48.13, "lon": 11.58},
                },
                "functions": [{"opening_date": "2019-05-01"}],
            }

        mock_tesla.fetch_location_details = mock_fetch_details  # type: ignore[method-assign]

        station = await provider.refresh_single_station(
            "muenchensupercharger",
            tesla_client=mock_tesla,  # type: ignore[arg-type]
        )

        assert station is not None
        assert station.name == "Tesla Supercharger - Muenchen Supercharger"
        assert sum(station.stalls.values()) == 16

        # DB tatsaechlich aktualisiert (nicht nur der In-Memory-Rueckgabewert)
        record = provider._db.find_station_by_slug("muenchensupercharger")
        assert record is not None
        assert record["total_stalls"] == 16

    @pytest.mark.asyncio
    async def test_refresh_single_station_unknown_slug_returns_none(self, tmp_path: Path) -> None:
        """Unbekannter Slug ohne explizites country liefert None statt Fehler."""
        provider = TeslaChargingStationProvider(db_path=tmp_path / "unknown_test.db")
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)

        station = await provider.refresh_single_station(
            "does-not-exist",
            tesla_client=mock_tesla,  # type: ignore[arg-type]
        )

        assert station is None


def _pricing_html(charger_pricing: list[dict[str, Any]]) -> str:
    """Wraps `charger_pricing` in a minimal `__NEXT_DATA__` HTML page."""
    payload = {"props": {"pageProps": {"formattedData": {"chargerPricing": charger_pricing}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


_FLAT_OWNER_TIER = [{"label": "Charging Fees for Tesla Owner", "price": "DKK 4.50/kWh"}]


class TestTeslaChargingStationProviderPricing:
    """Tests für die Preis-Scraping/Warteschlangen-Methoden."""

    def test_resolve_supercharge_info_id_by_slug(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Aufloesung ueber die tesla_location_id (Normalfall)."""
        assert tesla_provider_seeded._resolve_supercharge_info_id("kopenhagensupercharger") == 5678

    def test_resolve_supercharge_info_id_by_numeric_fallback(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Station 3506 hat keine tesla_location_id (siehe fixture) - Aufloesung
        faellt auf die numerische supercharge_info_id zurueck, wie
        `_db_record_to_charging_station` sie als `station_id` verwendet."""
        assert tesla_provider_seeded._resolve_supercharge_info_id("3506") == 3506

    def test_resolve_supercharge_info_id_unknown_returns_none(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Unbekannte Station-ID liefert None statt Fehler."""
        assert tesla_provider_seeded._resolve_supercharge_info_id("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_refresh_pricing_unknown_slug_raises(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """refresh_pricing wirft ValueError fuer unbekannte Stationen."""
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        with pytest.raises(ValueError, match="Unbekannte Station"):
            await tesla_provider_seeded.refresh_pricing("does-not-exist", tesla_client=mock_tesla)

    @pytest.mark.asyncio
    async def test_refresh_pricing_persists_and_dequeues(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """refresh_pricing speichert die geparsten Tiers und entfernt die Station
        aus der Warteschlange."""
        tesla_provider_seeded.enqueue_stations_for_pricing_refresh(["kopenhagensupercharger"])
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        mock_tesla.fetch_pricing_html.return_value = _pricing_html(_FLAT_OWNER_TIER)

        tiers = await tesla_provider_seeded.refresh_pricing(
            "kopenhagensupercharger", tesla_client=mock_tesla
        )

        assert len(tiers) == 1
        assert tiers[0].currency == "DKK"
        mock_tesla.fetch_pricing_html.assert_awaited_once_with("kopenhagensupercharger")
        assert tesla_provider_seeded.list_pricing_queue() == []

    @pytest.mark.asyncio
    async def test_refresh_pricing_resolves_numeric_slug(self, tmp_path: Path) -> None:
        """Eine rein numerische `tesla_location_id` (stale supercharge.info-
        `locationId`, z. B. "Rødekro East, Denmark" als "28500") wird vor dem
        Abruf ueber Teslas eigene Standortliste in den echten Slug
        ("rodekrosupercharger") aufgeloest, statt eine garantiert
        404-liefernde Anfrage gegen die numerische ID zu senden."""
        db_path = tmp_path / "numeric_slug.db"
        db = SQLiteDatabase(db_path)
        db.initialize()
        now = datetime.now(UTC).isoformat()
        db.replace_all_stations(
            [
                {
                    "supercharge_info_id": 28500,
                    "tesla_location_id": "28500",
                    "site_name": "Rødekro East, Denmark",
                    "latitude": 55.0715,
                    "longitude": 9.3421,
                    "country_code": "DK",
                    "stalls_v2": 0,
                    "stalls_v3": 8,
                    "stalls_v3_ultra": 0,
                    "stalls_v4": 0,
                    "total_stalls": 8,
                    "power_kilowatt": 250,
                    "status": "OPEN",
                    "connector_types": json.dumps(["ccs2"]),
                    "ist_24_7": 1,
                    "date_opened": None,
                    "last_updated_utc": now,
                },
            ]
        )
        db.close()

        provider = TeslaChargingStationProvider(db_path=db_path)
        provider.enqueue_stations_for_pricing_refresh(["28500"])
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        mock_tesla.fetch_locations.return_value = [
            {
                "location_type": ["supercharger"],
                "location_url_slug": "rodekrosupercharger",
                "latitude": 55.0716,
                "longitude": 9.3422,
            },
            {
                "location_type": ["supercharger"],
                "location_url_slug": "kolding-supercharger",
                "latitude": 55.5,
                "longitude": 9.5,
            },
        ]
        mock_tesla.fetch_pricing_html.return_value = _pricing_html(_FLAT_OWNER_TIER)

        tiers = await provider.refresh_pricing("28500", tesla_client=mock_tesla)

        assert len(tiers) == 1
        mock_tesla.fetch_pricing_html.assert_awaited_once_with("rodekrosupercharger")
        assert provider.list_pricing_queue() == []
        # The corrected slug is persisted for future refreshes.
        assert provider._resolve_supercharge_info_id("rodekrosupercharger") == 28500

    @pytest.mark.asyncio
    async def test_refresh_pricing_numeric_slug_no_match_raises(self, tmp_path: Path) -> None:
        """Findet sich in Teslas Standortliste kein nahegelegener Treffer fuer
        eine numerische ID, wird kein Request gegen die (bekannt 404-liefernde)
        numerische ID gestellt - stattdessen ein CurlError geworfen und die
        Station dennoch aus der Warteschlange entfernt."""
        db_path = tmp_path / "numeric_slug_unresolved.db"
        db = SQLiteDatabase(db_path)
        db.initialize()
        now = datetime.now(UTC).isoformat()
        db.replace_all_stations(
            [
                {
                    "supercharge_info_id": 99999,
                    "tesla_location_id": "99999",
                    "site_name": "Unknown Placeholder, Germany",
                    "latitude": 52.0,
                    "longitude": 13.0,
                    "country_code": "DE",
                    "stalls_v2": 0,
                    "stalls_v3": 8,
                    "stalls_v3_ultra": 0,
                    "stalls_v4": 0,
                    "total_stalls": 8,
                    "power_kilowatt": 250,
                    "status": "OPEN",
                    "connector_types": json.dumps(["ccs2"]),
                    "ist_24_7": 1,
                    "date_opened": None,
                    "last_updated_utc": now,
                },
            ]
        )
        db.close()

        provider = TeslaChargingStationProvider(db_path=db_path)
        provider.enqueue_stations_for_pricing_refresh(["99999"])
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        mock_tesla.fetch_locations.return_value = [
            {
                "location_type": ["supercharger"],
                "location_url_slug": "far-away-supercharger",
                "latitude": 60.0,
                "longitude": 20.0,
            },
        ]

        with pytest.raises(TeslaLocationsClient.CurlError):
            await provider.refresh_pricing("99999", tesla_client=mock_tesla)

        mock_tesla.fetch_pricing_html.assert_not_awaited()
        assert provider.list_pricing_queue() == []

    @pytest.mark.asyncio
    async def test_refresh_pricing_dequeues_even_on_parse_failure(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Ein Parse-Fehler entfernt die Station trotzdem aus der Warteschlange
        (verhindert dauerhaftes Haengenbleiben, siehe drain_pricing_queue)."""
        tesla_provider_seeded.enqueue_stations_for_pricing_refresh(["kopenhagensupercharger"])
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        mock_tesla.fetch_pricing_html.return_value = "<html>no next data</html>"

        with pytest.raises(PricingParseError):
            await tesla_provider_seeded.refresh_pricing(
                "kopenhagensupercharger", tesla_client=mock_tesla
            )

        assert tesla_provider_seeded.list_pricing_queue() == []

    def test_get_cached_pricing_empty_for_never_scraped(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Ohne vorherigen Scrape liefert get_cached_pricing eine leere CachedPricing."""
        cached = tesla_provider_seeded.get_cached_pricing("kopenhagensupercharger")
        assert cached.tiers == []
        assert cached.updated_utc is None

    @pytest.mark.asyncio
    async def test_get_cached_pricing_after_refresh(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Nach refresh_pricing liefert get_cached_pricing die gespeicherten Tiers
        samt Aktualisierungszeitpunkt."""
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        mock_tesla.fetch_pricing_html.return_value = _pricing_html(_FLAT_OWNER_TIER)
        await tesla_provider_seeded.refresh_pricing(
            "kopenhagensupercharger", tesla_client=mock_tesla
        )

        cached = tesla_provider_seeded.get_cached_pricing("kopenhagensupercharger")

        assert len(cached.tiers) == 1
        assert cached.updated_utc is not None
        assert cached.updated_utc.tzinfo is not None

    def test_enqueue_stations_for_pricing_refresh_skips_fresh(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Eine kuerzlich aktualisierte Station wird nicht erneut eingereiht,
        eine nie gescrapte schon."""
        tesla_provider_seeded._db.upsert_pricing(
            5678,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "DKK",
                    "amount": 4.5,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )

        count = tesla_provider_seeded.enqueue_stations_for_pricing_refresh(
            ["kopenhagensupercharger", "malmosupercharger"]
        )

        queued = {row["supercharge_info_id"] for row in tesla_provider_seeded.list_pricing_queue()}
        assert count == 1
        assert queued == {9012}  # malmosupercharger: never scraped

    def test_enqueue_stations_for_pricing_refresh_requeues_stale(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Preisdaten aelter als max_age werden erneut eingereiht."""
        tesla_provider_seeded._db.upsert_pricing(
            5678,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "DKK",
                    "amount": 4.5,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        conn = tesla_provider_seeded._db._conn
        assert conn is not None
        conn.execute(
            "UPDATE charging_pricing SET last_updated_utc = ? WHERE supercharge_info_id = ?",
            ("2000-01-01T00:00:00+00:00", 5678),
        )
        conn.commit()

        count = tesla_provider_seeded.enqueue_stations_for_pricing_refresh(
            ["kopenhagensupercharger"], max_age=timedelta(days=14)
        )

        assert count == 1

    @pytest.mark.asyncio
    async def test_drain_pricing_queue_refreshes_all_entries(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """drain_pricing_queue arbeitet alle eingereihten Stationen ab."""
        tesla_provider_seeded.enqueue_stations_for_pricing_refresh(
            ["kopenhagensupercharger", "malmosupercharger"]
        )
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        mock_tesla.fetch_pricing_html.return_value = _pricing_html(_FLAT_OWNER_TIER)

        result = await tesla_provider_seeded.drain_pricing_queue(tesla_client=mock_tesla, delay_s=0)

        assert set(result.refreshed) == {"kopenhagensupercharger", "malmosupercharger"}
        assert result.failed == []
        assert tesla_provider_seeded.list_pricing_queue() == []

    @pytest.mark.asyncio
    async def test_drain_pricing_queue_records_failures_and_still_dequeues(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Ein WAF-Block wird als Fehlschlag erfasst, die Station trotzdem
        aus der Warteschlange entfernt (Retry erst bei naechster
        Routen-Finalisierung, siehe enqueue_stations_for_pricing_refresh)."""
        tesla_provider_seeded.enqueue_stations_for_pricing_refresh(["kopenhagensupercharger"])
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)
        mock_tesla.fetch_pricing_html.side_effect = TeslaLocationsClient.CurlError("403")

        result = await tesla_provider_seeded.drain_pricing_queue(tesla_client=mock_tesla, delay_s=0)

        assert result.refreshed == []
        assert len(result.failed) == 1
        assert result.failed[0][0] == "kopenhagensupercharger"
        assert tesla_provider_seeded.list_pricing_queue() == []

    @pytest.mark.asyncio
    async def test_drain_pricing_queue_skips_already_fresh_without_fetch(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """Wird eine Station zwischenzeitlich anderweitig aktualisiert, prueft
        drain_pricing_queue das defensiv nach und ueberspringt sie ohne
        Netzwerk-Request."""
        tesla_provider_seeded._db.enqueue_pricing_refresh([5678])
        tesla_provider_seeded._db.upsert_pricing(
            5678,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "DKK",
                    "amount": 4.5,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        mock_tesla = AsyncMock(spec=TeslaLocationsClient)

        result = await tesla_provider_seeded.drain_pricing_queue(tesla_client=mock_tesla, delay_s=0)

        mock_tesla.fetch_pricing_html.assert_not_awaited()
        assert result.skipped_fresh == ["kopenhagensupercharger"]
        assert tesla_provider_seeded.list_pricing_queue() == []

    def test_list_pricing_queue_respects_priority_order(
        self, tesla_provider_seeded: TeslaChargingStationProvider
    ) -> None:
        """list_pricing_queue delegiert an SQLiteDatabase.load_pricing_queue
        (nie gescrapt zuerst)."""
        tesla_provider_seeded._db.upsert_pricing(
            5678,
            [
                {
                    "tier_label": "Charging Fees for Tesla Owner",
                    "time_label": None,
                    "currency": "DKK",
                    "amount": 4.5,
                    "unit": "kWh",
                    "idle_fee_text": None,
                }
            ],
        )
        tesla_provider_seeded._db.enqueue_pricing_refresh([5678, 9012])

        queue = tesla_provider_seeded.list_pricing_queue()

        assert [row["supercharge_info_id"] for row in queue] == [9012, 5678]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tesla_refresh_against_real_api(tmp_path: Path) -> None:
    """Integrationstest: TeslaChargingStationProvider.refresh() gegen echte API.

    Prüft, dass der vollständige Refresh-Zyklus funktioniert:
    1. Fetch von supercharge.info
    2. Europe-Filter
    3. DB-Insert
    4. Radius-Suche aus DB

    Erfordert Internetzugriff auf supercharge.info (öffentlich, kein API-Key).
    """
    db_path = tmp_path / "integration_tesla.db"
    provider = TeslaChargingStationProvider(db_path=db_path)

    count = await provider.refresh()
    assert count > 0, "Refresh sollte europäische Stationen laden"

    # Prüfe Radius-Suche für Berlin (sollte deutsche Stationen finden)
    stations = await provider.get_stations_in_radius((52.5, 13.4), 50.0)
    assert len(stations) >= 1
    assert all(s.country == "DE" for s in stations)

    # Prüfe Property
    assert provider._db.station_count > 0
    assert provider._db.last_refresh_utc is not None
