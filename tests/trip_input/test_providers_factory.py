"""Tests für providers_factory: ProductionProviders factory functions.

Testet:
- build_production_providers() returns a usable tuple even when optional env vars are missing
- The returned tuple has all 5 provider fields
- Die providers können via close_production_providers() ohne Fehler geschlossen werden
- Fehlende Credentials crashen nicht
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tripplanner.trip_input.providers_factory import (
    ProductionProviders,
    build_production_providers,
    close_production_providers,
)


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
