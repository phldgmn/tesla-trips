"""Tests für das optimization-Modul.

Alle Tests sind deterministisch und verwenden kleine, synthetische Szenarien.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tripplanner.charging_infrastructure.models import (
    ChargingStation,
    ConnectorType,
    StallType,
)
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization import (
    create_networkx_optimizer,
    create_ortools_optimizer,
)
from tripplanner.optimization.discretizer import (
    bucket_to_soc,
    bucket_to_zeit,
    soc_to_bucket,
    zeit_to_bucket,
)
from tripplanner.optimization.models import (
    OptimizationConstraints,
)
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile

# Fixe Koordinaten für Tests
BERLIN_COORD: tuple[float, float] = (52.5200, 13.4050)
HAMBURG_COORD: tuple[float, float] = (53.5511, 9.9937)
LEIPZIG_COORD: tuple[float, float] = (51.3397, 12.3731)


class TestDiscretizerFunctions:
    """Tests für die Diskretisierungsfunktionen."""

    def test_soc_to_bucket_with_default_step(self) -> None:
        """Test SoC-Bucket-Konvertierung mit 1%-Schritten."""
        assert soc_to_bucket(0.0) == 0
        assert soc_to_bucket(50.0) == 50
        assert soc_to_bucket(100.0) == 100
        assert soc_to_bucket(50.5) == 50
        assert soc_to_bucket(50.6) == 51

    def test_soc_to_bucket_with_custom_step(self) -> None:
        """Test SoC-Bucket-Konvertierung mit 5%-Schritten."""
        assert soc_to_bucket(0.0, soc_step_pct=5.0) == 0
        assert soc_to_bucket(50.0, soc_step_pct=5.0) == 10
        assert soc_to_bucket(100.0, soc_step_pct=5.0) == 20
        assert soc_to_bucket(75.5, soc_step_pct=5.0) == 15

    def test_bucket_to_soc_with_default_step(self) -> None:
        """Test Bucket-zu-SoC-Konvertierung mit 1%-Schritten."""
        assert bucket_to_soc(0) == 0.0
        assert bucket_to_soc(50) == 50.0
        assert bucket_to_soc(100) == 100.0

    def test_bucket_to_soc_with_custom_step(self) -> None:
        """Test Bucket-zu-SoC-Konvertierung mit 5%-Schritten."""
        assert bucket_to_soc(0, soc_step_pct=5.0) == 0.0
        assert bucket_to_soc(10, soc_step_pct=5.0) == 50.0
        assert bucket_to_soc(20, soc_step_pct=5.0) == 100.0

    def test_soc_bucket_roundtrip(self) -> None:
        """Test Rundungstoleranz der Bucket-Konvertierung."""
        for soc in [0.0, 25.0, 50.0, 75.0, 100.0]:
            bucket = soc_to_bucket(soc)
            result = bucket_to_soc(bucket)
            assert result == soc

    def test_zeit_to_bucket(self) -> None:
        """Test Zeit-Bucket-Konvertierung."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)

        assert zeit_to_bucket(base_time, base_time) == 0
        assert zeit_to_bucket(base_time + timedelta(minutes=15), base_time) == 1
        assert zeit_to_bucket(base_time + timedelta(minutes=30), base_time) == 2
        assert zeit_to_bucket(base_time + timedelta(minutes=60), base_time) == 4

    def test_bucket_to_zeit(self) -> None:
        """Test Bucket-zu-Zeit-Konvertierung."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)

        assert bucket_to_zeit(0, base_time) == base_time
        assert bucket_to_zeit(1, base_time) == base_time + timedelta(minutes=15)
        assert bucket_to_zeit(2, base_time) == base_time + timedelta(minutes=30)
        assert bucket_to_zeit(4, base_time) == base_time + timedelta(minutes=60)

    def test_bucket_to_zeit_roundtrip(self) -> None:
        """Test Rundungstoleranz der Zeit-Bucket-Konvertierung."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        time_steps = [0, 1, 2, 4, 8, 16]

        for bucket in time_steps:
            time = bucket_to_zeit(bucket, base_time)
            result_bucket = zeit_to_bucket(time, base_time)
            assert result_bucket == bucket


class TestNetworkXOptimizer:
    """Tests für den NetworkXOptimizer (Prototyp)."""

    def test_kein_ladehalt_noetig(self) -> None:
        """Test: Einfache Route, keine Ladehalt nötig (100% Start, geringer Verbrauch)."""
        route = Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[BERLIN_COORD, (53.0, 12.0)],
                    laenge_m=50_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=130,
                    steigung_rohdaten=0.0,
                    bearing_deg=315.0,
                ),
                RouteSegment(
                    segment_index=1,
                    geometrie=[(53.0, 12.0), (53.3, 11.0)],
                    laenge_m=30_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=120,
                    steigung_rohdaten=0.0,
                    bearing_deg=280.0,
                ),
                RouteSegment(
                    segment_index=2,
                    geometrie=[(53.3, 11.0), HAMBURG_COORD],
                    laenge_m=20_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=110,
                    steigung_rohdaten=0.0,
                    bearing_deg=260.0,
                ),
            ],
            gesamtlaenge_m=100_000,
            geometrie=[BERLIN_COORD, (53.0, 12.0), (53.3, 11.0), HAMBURG_COORD],
        )

        gradients = [
            SegmentGradient(
                segment_index=i,
                steigung_prozent=0.0,
                hoehendifferenz_m=0.0,
                horizontale_distanz_m=seg.laenge_m,
            )
            for i, seg in enumerate(route.segments)
        ]

        energy_results = [
            SegmentEnergyResult(
                segment_index=0,
                energiebedarf_kwh=3.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=3.0,
                geschwindigkeit_m_s=30.0,
                fahrzeit_s=1667,
                streckenlaenge_m=50_000,
            ),
            SegmentEnergyResult(
                segment_index=1,
                energiebedarf_kwh=2.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=2.0,
                geschwindigkeit_m_s=25.0,
                fahrzeit_s=1200,
                streckenlaenge_m=30_000,
            ),
            SegmentEnergyResult(
                segment_index=2,
                energiebedarf_kwh=2.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=2.0,
                geschwindigkeit_m_s=20.0,
                fahrzeit_s=1000,
                streckenlaenge_m=20_000,
            ),
        ]

        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=62.5,
        )

        constraints = OptimizationConstraints(
            min_soc_pct=15.0,
            ziel_soc_pct=60.0,
            sicherheitsreserve_pct=5.0,
        )

        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=30)

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=100.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(plan.ladehalte) == 0
        assert plan.gesamtreisezeit_s >= 0
        assert plan.min_zwischenstopp_ankunftszeit == {}

    def test_grenzfall_minimaler_soc(self) -> None:
        """Test: Grenzfall - Start-SoC knapp unter Verbrauch → muss laden."""
        route = Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[BERLIN_COORD, (53.0, 12.0)],
                    laenge_m=60_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=130,
                    steigung_rohdaten=0.0,
                    bearing_deg=315.0,
                ),
            ],
            gesamtlaenge_m=60_000,
            geometrie=[BERLIN_COORD, (53.0, 12.0)],
        )

        energy_results = [
            SegmentEnergyResult(
                segment_index=0,
                energiebedarf_kwh=25.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=25.0,
                geschwindigkeit_m_s=30.0,
                fahrzeit_s=2000,
                streckenlaenge_m=60_000,
            ),
        ]

        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=62.5,
        )

        constraints = OptimizationConstraints(
            min_soc_pct=15.0,
            ziel_soc_pct=80.0,
        )

        station = ChargingStation(
            station_id="station_001",
            name="Tesla Supercharger Ziel",
            coordinate=(53.0, 12.0),
            stalls={StallType.V3: 4},
            max_ladeleistung_kw=250.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )

        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=15)

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[
                SegmentGradient(
                    segment_index=0,
                    steigung_prozent=0.0,
                    hoehendifferenz_m=0.0,
                    horizontale_distanz_m=60_000,
                )
            ],
            energy_results=energy_results,
            charging_stations=[station],
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=20.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(plan.ladehalte) >= 1
        assert plan.gesamtreisezeit_s > 0

    def test_route_mit_zu_wenig_reichweite_schlaegt_fehl(self) -> None:
        """Test: Route mit zu wenig Reichweite schlägt fehl (keine Ladestation)."""
        route = Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[BERLIN_COORD, (52.0, 10.0)],
                    laenge_m=100_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=130,
                    steigung_rohdaten=0.0,
                    bearing_deg=300.0,
                ),
                RouteSegment(
                    segment_index=1,
                    geometrie=[(52.0, 10.0), HAMBURG_COORD],
                    laenge_m=100_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=120,
                    steigung_rohdaten=0.0,
                    bearing_deg=270.0,
                ),
            ],
            gesamtlaenge_m=200_000,
            geometrie=[BERLIN_COORD, (52.0, 10.0), HAMBURG_COORD],
        )

        energy_results = [
            SegmentEnergyResult(
                segment_index=0,
                energiebedarf_kwh=30.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=30.0,
                geschwindigkeit_m_s=30.0,
                fahrzeit_s=3333,
                streckenlaenge_m=100_000,
            ),
            SegmentEnergyResult(
                segment_index=1,
                energiebedarf_kwh=30.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=30.0,
                geschwindigkeit_m_s=25.0,
                fahrzeit_s=4000,
                streckenlaenge_m=100_000,
            ),
        ]

        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=62.5,
        )

        constraints = OptimizationConstraints(
            min_soc_pct=20.0,
            ziel_soc_pct=70.0,
        )

        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=30)

        with pytest.raises(ValueError, match="Kein erreichbarer Zielknoten"):
            optimizer.optimize(
                route=route,
                segments=route.segments,
                gradients=[
                    SegmentGradient(
                        segment_index=0,
                        steigung_prozent=0.0,
                        hoehendifferenz_m=0.0,
                        horizontale_distanz_m=100_000,
                    ),
                    SegmentGradient(
                        segment_index=1,
                        steigung_prozent=0.0,
                        hoehendifferenz_m=0.0,
                        horizontale_distanz_m=100_000,
                    ),
                ],
                energy_results=energy_results,
                charging_stations=[],
                waypoints=[],
                vehicle_profile=vehicle_profile,
                constraints=constraints,
                start_soc_pct=100.0,
                abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            )


class TestORToolsOptimizer:
    """Tests für den OR-Tools-Optimizer (Platzhalter)."""

    def test_ortools_not_implemented(self) -> None:
        """Test: OR-Tools-Optimizer raise NotImplementedError."""
        optimizer = create_ortools_optimizer()

        route = Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[BERLIN_COORD, HAMBURG_COORD],
                    laenge_m=100_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=130,
                    steigung_rohdaten=0.0,
                    bearing_deg=315.0,
                ),
            ],
            gesamtlaenge_m=100_000,
            geometrie=[BERLIN_COORD, HAMBURG_COORD],
        )

        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=62.5,
        )

        constraints = OptimizationConstraints(
            min_soc_pct=15.0,
            ziel_soc_pct=80.0,
        )

        with pytest.raises(NotImplementedError) as exc_info:
            optimizer.optimize(
                route=route,
                segments=route.segments,
                gradients=[],
                energy_results=[],
                charging_stations=[],
                waypoints=[],
                vehicle_profile=vehicle_profile,
                constraints=constraints,
                start_soc_pct=100.0,
                abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            )

        assert "OR-Tools-Backend ist eine spätere Ausbaustufe" in str(exc_info.value)
