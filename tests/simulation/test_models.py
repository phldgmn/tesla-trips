"""Tests für das simulation-Modul: Modelle und Validierung.

Testfälle gemäß Plan Abschnitt 6.1:
- Testfall 1: SimulationFrame Validierung (SoC-Bereich, Zustand-Geschwindigkeit-Konsistenz)
- Testfall 2: TripSimulationResult Validierung
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tripplanner.simulation import SimulationFrame, TripState


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
