"""Tests für das simulation-Modul: Modelle und Validierung.

Testfälle gemäß Plan Abschnitt 6.1:
- Testfall 1: SimulationFrame Validierung (SoC-Bereich, Zustand-Geschwindigkeit-Konsistenz)
- Testfall 2: TripSimulationResult Validierung
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tripplanner.simulation import SimulationFrame, TripState
from tripplanner.simulation.models import ChargingCostByCurrency, ChargingStopSummary


class TestSimulationFrame:
    """Tests für das SimulationFrame-Modell."""

    def test_valid_fahren_frame(self) -> None:
        """Testfall 1: Validierung eines FAHREN-Frames mit korrekten Werten."""
        frame = SimulationFrame(
            zeitpunkt=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            position=(52.5200, 13.4050),
            distanz_m=0.0,
            soc_pct=80.0,
            zustand=TripState.FAHREN,
            geschwindigkeit_kmh=100.0,
        )
        assert frame.zustand == TripState.FAHREN
        assert frame.geschwindigkeit_kmh == 100.0
        assert frame.soc_pct == 80.0

    def test_valid_laden_frame(self) -> None:
        """Testfall 2: Validierung eines LADEN-Frames mit korrekten Werten."""
        frame = SimulationFrame(
            zeitpunkt=datetime(2026, 8, 15, 8, 30, 0, tzinfo=UTC),
            position=(52.5200, 13.4050),
            distanz_m=1000.0,
            soc_pct=40.0,
            zustand=TripState.LADEN,
            geschwindigkeit_kmh=0.0,
        )
        assert frame.zustand == TripState.LADEN
        assert frame.geschwindigkeit_kmh == 0.0

    def test_valid_pause_frame(self) -> None:
        """Testfall 3: Validierung eines PAUSE-Frames mit korrekten Werten."""
        frame = SimulationFrame(
            zeitpunkt=datetime(2026, 8, 15, 8, 15, 0, tzinfo=UTC),
            position=(52.5200, 13.4050),
            distanz_m=500.0,
            soc_pct=60.0,
            zustand=TripState.PAUSE,
            geschwindigkeit_kmh=2.0,
        )
        assert frame.zustand == TripState.PAUSE
        assert frame.geschwindigkeit_kmh == 2.0

    def test_charging_frame_high_speed_raise(self) -> None:
        """Testfall 4: LADEN mit hoher Geschwindigkeit muss ValueError werfen."""
        with pytest.raises(ValueError, match="Beim Laden muss Geschwindigkeit"):
            SimulationFrame(
                zeitpunkt=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
                position=(52.5200, 13.4050),
                distanz_m=0.0,
                soc_pct=50.0,
                zustand=TripState.LADEN,
                geschwindigkeit_kmh=10.0,
            )

    def test_pause_frame_high_speed_raise(self) -> None:
        """Testfall 5: PAUSE mit hoher Geschwindigkeit muss ValueError werfen."""
        with pytest.raises(ValueError, match="Bei Pause sollte Geschwindigkeit"):
            SimulationFrame(
                zeitpunkt=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
                position=(52.5200, 13.4050),
                distanz_m=0.0,
                soc_pct=50.0,
                zustand=TripState.PAUSE,
                geschwindigkeit_kmh=20.0,
            )

    def test_soc_over_100_raise(self) -> None:
        """Testfall 6: SoC über 100% muss ValueError werfen."""
        with pytest.raises(ValueError, match="soc_pct"):
            SimulationFrame(
                zeitpunkt=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
                position=(52.5200, 13.4050),
                distanz_m=0.0,
                soc_pct=101.0,
                zustand=TripState.FAHREN,
                geschwindigkeit_kmh=100.0,
            )

    def test_soc_under_0_raise(self) -> None:
        """Testfall 7: SoC unter 0% muss ValueError werfen."""
        with pytest.raises(ValueError, match="soc_pct"):
            SimulationFrame(
                zeitpunkt=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
                position=(52.5200, 13.4050),
                distanz_m=0.0,
                soc_pct=-1.0,
                zustand=TripState.FAHREN,
                geschwindigkeit_kmh=100.0,
            )

    def test_position_format(self) -> None:
        """Testfall 8: Position muss (lat, lon) Tuple sein."""
        frame = SimulationFrame(
            zeitpunkt=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            position=(52.5200, 13.4050),
            distanz_m=0.0,
            soc_pct=80.0,
            zustand=TripState.FAHREN,
            geschwindigkeit_kmh=100.0,
        )
        assert isinstance(frame.position, tuple)
        assert len(frame.position) == 2
        assert frame.position[0] == 52.5200  # lat
        assert frame.position[1] == 13.4050  # lon


class TestChargingStopSummaryPricing:
    """Tests für die Pricing-Felder von `ChargingStopSummary`."""

    def _base_kwargs(self) -> dict[str, object]:
        return {
            "name": "Tesla Supercharger - Rhueden",
            "station_id": "rhudensupercharger",
            "position": (51.947, 10.140),
            "distanz_m": 12000.0,
            "ankunfts_soc_pct": 30.0,
            "ziel_soc_pct": 80.0,
            "ladedauer_s": 1500,
            "energie_geladen_kwh": 25.0,
            "ankunftszeit": datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            "abfahrtszeit": datetime(2026, 1, 1, 10, 25, tzinfo=UTC),
        }

    def test_pricing_fields_default_to_none(self) -> None:
        """Ohne explizite Preisdaten sind alle Pricing-Felder None (kein Preis
        gecacht - Standardfall vor dem ersten Scrape)."""
        stop = ChargingStopSummary(**self._base_kwargs())
        assert stop.price_per_kwh is None
        assert stop.currency is None
        assert stop.estimated_cost is None
        assert stop.pricing_updated_utc is None

    def test_pricing_fields_accept_populated_values(self) -> None:
        """Mit Preisdaten sind alle Pricing-Felder korrekt gesetzt."""
        stop = ChargingStopSummary(
            **self._base_kwargs(),
            price_per_kwh=0.45,
            currency="EUR",
            estimated_cost=11.25,
            pricing_updated_utc=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        )
        assert stop.price_per_kwh == 0.45
        assert stop.currency == "EUR"
        assert stop.estimated_cost == 11.25
        assert stop.pricing_updated_utc == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


class TestChargingCostByCurrency:
    """Tests für das `ChargingCostByCurrency`-Modell."""

    def test_valid_entry(self) -> None:
        """Ein gültiger Eintrag speichert Waehrung und Betrag."""
        entry = ChargingCostByCurrency(currency="SEK", amount=45.5)
        assert entry.currency == "SEK"
        assert entry.amount == 45.5

    def test_rejects_invalid_currency_length(self) -> None:
        """Eine Waehrung, die nicht aus genau 3 Zeichen besteht, ist ungueltig."""
        with pytest.raises(ValueError, match="3"):
            ChargingCostByCurrency(currency="EURO", amount=1.0)

    def test_rejects_negative_amount(self) -> None:
        """Ein negativer Betrag ist ungueltig."""
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            ChargingCostByCurrency(currency="EUR", amount=-1.0)
