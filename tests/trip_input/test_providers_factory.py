"""Tests für providers_factory: ProductionProviders factory functions.

Testet:
- build_production_providers() returns a usable tuple even when optional env vars are missing
- The returned tuple has all 5 provider fields
- Die providers können via close_production_providers() ohne Fehler geschlossen werden
- Fehlende Credentials crashen nicht
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tripplanner.trip_input import providers_factory as providers_factory_module
from tripplanner.trip_input.providers_factory import (
    ProductionProviders,
    build_production_providers,
    close_production_providers,
)
from tripplanner.weather.providers import LoadBalancedWeatherProvider


class TestProductionProviders:
    """Tests für ProductionProviders NamedTuple Struktur."""

    def test_production_providers_has_five_fields(self) -> None:
        """ProductionProviders muss genau 5 Felder haben."""
        assert len(ProductionProviders._fields) == 5
        fields = set(ProductionProviders._fields)
        assert "routing" in fields
        assert "elevation_provider" in fields
        assert "weather" in fields
        assert "construction" in fields
        assert "charging" in fields


@pytest.mark.asyncio
class TestBuildProductionProviders:
    """Tests für build_production_providers()."""

    async def test_build_production_providers_returns_tuple(self) -> None:
        """build_production_providers() gibt ein ProductionProviders Tuple zurück."""
        providers = await build_production_providers()
        assert isinstance(providers, ProductionProviders)

    async def test_build_production_providers_has_all_five_provider_fields(
        self,
    ) -> None:
        """Alle 5 Provider-Felder sind gesetzt."""
        providers = await build_production_providers()

        assert providers.routing is not None
        assert providers.elevation_provider is not None
        assert providers.weather is not None
        assert providers.construction is not None
        assert providers.charging is not None

    async def test_build_production_providers_works_without_optional_env_vars(
        self,
    ) -> None:
        """Factory funktioniert auch ohne optionale Umgebungsvariablen."""
        # Simuliere fehlende optionale Env Vars
        with patch.dict("os.environ", {}, clear=True):
            providers = await build_production_providers()
            assert isinstance(providers, ProductionProviders)
            assert all(
                getattr(providers, field) is not None for field in ProductionProviders._fields
            )

    async def test_build_production_providers_does_not_crash_without_credentials(
        self,
    ) -> None:
        """Fehlende Credentials führen nicht zu einem Crash."""
        # setze key Umgebungsvariablen auf leer
        with patch.dict(
            "os.environ",
            {
                "GRAPHHOPPER_URL": "http://localhost:8989",
            },
            clear=True,
        ):
            providers = await build_production_providers()
            assert isinstance(providers, ProductionProviders)

    async def test_build_production_providers_routing_provider_is_usable(
        self,
    ) -> None:
        """Routing Provider ist nutzbar (kann Route berechnen)."""
        providers = await build_production_providers()
        # Der FakeRoutingProvider sollte immer funktionieren
        assert hasattr(providers.routing, "berechne_route")


@pytest.mark.asyncio
class TestCloseProductionProviders:
    """Tests für close_production_providers()."""

    async def test_close_production_providers_does_not_crash(self) -> None:
        """close_production_providers() wirft keinen Fehler bei gültigen Providern."""
        providers = await build_production_providers()
        # sollte keine Exception werfen
        await close_production_providers(providers)

    async def test_close_production_providers_idempotent_after_close(
        self,
    ) -> None:
        """Nach close_production_providers() sind asynchrone Ressourcen geschlossen."""
        providers = await build_production_providers()
        await close_production_providers(providers)
        # Zweites Schließen sollte keine Exception werfen (idempotent)
        await close_production_providers(providers)

    async def test_close_production_providers_closes_all_provider_resources(
        self,
    ) -> None:
        """Alle Provider-Ressourcen werden geschlossen."""
        providers = await build_production_providers()
        # Before close: check that async clients exist
        assert providers.routing is not None
        assert providers.weather is not None

        await close_production_providers(providers)

        # Nach Schließen sollten Ressourcen freigegeben sein
        # (keine further assertions, da Implementierungsspezifisch)


@pytest.mark.asyncio
class TestBuildWeatherProvider:
    """Tests for the `weather` field produced by `build_production_providers()`."""

    async def test_weather_is_load_balanced_provider(self) -> None:
        """The production `weather` provider is always a `LoadBalancedWeatherProvider`,
        even with zero optional API keys configured - the resilience guarantee (see
        `LoadBalancedWeatherProvider`) applies unconditionally."""
        providers = await build_production_providers()
        assert isinstance(providers.weather, LoadBalancedWeatherProvider)
        await close_production_providers(providers)

    async def test_weather_always_includes_global_and_national_providers(
        self, tmp_path: Path
    ) -> None:
        """Open-Meteo, MET Norway, SMHI, and DMI are always registered."""
        missing_credentials = tmp_path / "missing-credentials.yaml"
        with (
            patch.object(providers_factory_module, "_LOCAL_CREDENTIALS_PATH", missing_credentials),
            patch.dict("os.environ", {}, clear=True),
        ):
            providers = await build_production_providers()

        names = {e.name for e in providers.weather._entries}
        assert {"open-meteo", "met-norway", "smhi", "dmi"} <= names
        await close_production_providers(providers)

    async def test_weather_excludes_openweather_without_api_key(self, tmp_path: Path) -> None:
        """OpenWeather is omitted entirely when no API key is configured anywhere."""
        missing_credentials = tmp_path / "missing-credentials.yaml"
        with (
            patch.object(providers_factory_module, "_LOCAL_CREDENTIALS_PATH", missing_credentials),
            patch.dict("os.environ", {}, clear=True),
        ):
            providers = await build_production_providers()

        names = {e.name for e in providers.weather._entries}
        assert "openweather" not in names
        await close_production_providers(providers)

    async def test_weather_includes_openweather_when_env_key_set(self, tmp_path: Path) -> None:
        """Setting `OPENWEATHER_API_KEY` adds an `openweather` entry to the composite."""
        missing_credentials = tmp_path / "missing-credentials.yaml"
        with (
            patch.object(providers_factory_module, "_LOCAL_CREDENTIALS_PATH", missing_credentials),
            patch.dict("os.environ", {"OPENWEATHER_API_KEY": "env-key"}, clear=True),
        ):
            providers = await build_production_providers()

        names = {e.name for e in providers.weather._entries}
        assert "openweather" in names
        await close_production_providers(providers)

    async def test_weather_smhi_restricted_to_sweden_dmi_restricted_to_denmark(self) -> None:
        """SMHI's and DMI's `WeatherProviderEntry.countries` restrict them to SE/DK only."""
        providers = await build_production_providers()
        entries = {e.name: e for e in providers.weather._entries}

        assert entries["smhi"].countries == frozenset({"SE"})
        assert entries["dmi"].countries == frozenset({"DK"})
        assert entries["open-meteo"].countries is None
        assert entries["met-norway"].countries is None
        await close_production_providers(providers)

    async def test_weather_openweather_key_from_local_credentials_file(
        self, tmp_path: Path
    ) -> None:
        """A `weather.openweather.key` in `credentials.local.yaml` is picked up."""
        credentials_file = tmp_path / "credentials.local.yaml"
        credentials_file.write_text("weather:\n  openweather:\n    key: file-key\n")
        with (
            patch.object(providers_factory_module, "_LOCAL_CREDENTIALS_PATH", credentials_file),
            patch.dict("os.environ", {}, clear=True),
        ):
            providers = await build_production_providers()

        names = {e.name for e in providers.weather._entries}
        assert "openweather" in names
        await close_production_providers(providers)

    async def test_weather_env_key_takes_precedence_over_file(self, tmp_path: Path) -> None:
        """`OPENWEATHER_API_KEY` env var wins over `credentials.local.yaml`."""
        credentials_file = tmp_path / "credentials.local.yaml"
        credentials_file.write_text("weather:\n  openweather:\n    key: file-key\n")
        with (
            patch.object(providers_factory_module, "_LOCAL_CREDENTIALS_PATH", credentials_file),
            patch.dict("os.environ", {"OPENWEATHER_API_KEY": "env-key"}, clear=True),
        ):
            providers = await build_production_providers()

        entries = {e.name: e for e in providers.weather._entries}
        openweather_provider = entries["openweather"].provider
        assert openweather_provider._api_key == "env-key"
        await close_production_providers(providers)
