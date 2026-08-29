"""Tests für das optimization-Modul.

Alle Tests sind deterministisch und verwenden kleine, synthetische Szenarien.
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta

import networkx as nx
import pytest

from tripplanner.battery.models import LadekurveReferenz
from tripplanner.charging_infrastructure.models import (
    ChargingStation,
    ConnectorType,
    StallType,
)
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization import (
    charging_math,
    create_networkx_optimizer,
    create_ortools_optimizer,
    detour_costs,
)
from tripplanner.optimization.discretizer import (
    bucket_to_soc,
    bucket_to_time,
    soc_to_bucket,
    time_to_bucket,
)
from tripplanner.optimization.graph_builder import StateGraphBuilder
from tripplanner.optimization.models import (
    ChargingStop,
    DetourKosten,
    OptimizationConstraints,
)
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile, Waypoint

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

        assert time_to_bucket(base_time, base_time) == 0
        assert time_to_bucket(base_time + timedelta(minutes=15), base_time) == 1
        assert time_to_bucket(base_time + timedelta(minutes=30), base_time) == 2
        assert time_to_bucket(base_time + timedelta(minutes=60), base_time) == 4

    def test_bucket_to_zeit(self) -> None:
        """Test Bucket-zu-Zeit-Konvertierung."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)

        assert bucket_to_time(0, base_time) == base_time
        assert bucket_to_time(1, base_time) == base_time + timedelta(minutes=15)
        assert bucket_to_time(2, base_time) == base_time + timedelta(minutes=30)
        assert bucket_to_time(4, base_time) == base_time + timedelta(minutes=60)

    def test_bucket_to_zeit_roundtrip(self) -> None:
        """Test Rundungstoleranz der Zeit-Bucket-Konvertierung."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        time_steps = [0, 1, 2, 4, 8, 16]

        for bucket in time_steps:
            time = bucket_to_time(bucket, base_time)
            result_bucket = time_to_bucket(time, base_time)
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
        """Test: Grenzfall - Start-SoC knapp unter Verbrauch → muss laden.

        `ziel_soc_pct=45.0`: das einzige Segment kostet 40% SoC (25 kWh von
        62.5 kWh); ein Ankunfts-Ziel von >55% waere selbst mit einer Vollladung
        (100%) am Start physikalisch unerreichbar. 45% (minus 5% Reserve = 40%
        Ziel-Bucket) ist mit einer Ladung auf 80-90% erreichbar und erfordert
        dennoch zwingend einen Ladehalt, da der Start-SoC (20%) allein nicht
        einmal die Segmentfahrt selbst decken wuerde.
        """
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
            ziel_soc_pct=45.0,
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


class TestSocQuantisierungBeiFeingranularenSegmenten:
    """Regressionstest: Bug - SoC-Verbrauch pro Kante wurde vom bereits GERUNDETEN
    Bucket abgezogen statt vom kontinuierlichen SoC des Vorgaengerknotens.

    Bei feingranularen Routen (z. B. ein Segment pro GraphHopper-Polyline-
    Punktpaar, oft nur wenige zehn Meter) liegt der Verbrauch je Segment weit
    unter der halben Bucket-Schrittweite (Default 1%). `round(verbrauch/1.0)`
    ergab dann fuer praktisch jede Kante 0 - der gesamte Streckenverbrauch
    verschwand, der Optimierer hielt selbst physikalisch unmoegliche Strecken
    (mehr Energiebedarf als Batteriekapazitaet) faelschlich fuer ladehaltfrei
    fahrbar.
    """

    def test_viele_kleine_segmente_erfordern_trotzdem_einen_ladehalt(self) -> None:
        """1000 Segmente à 50 m (50 km), 15 kWh Gesamtverbrauch bei 10 kWh Akku
        (0.015 kWh/Segment = 0.15% - weit unter der halben 1%-Bucket-Schrittweite):
        Der Optimierer MUSS trotzdem mindestens einen Ladehalt einplanen, da
        50 km auch mit vollem Akku (100%) physikalisch nicht ohne Laden
        schaffbar sind (15 kWh Bedarf > 10 kWh Kapazitaet).
        """
        anzahl_segmente = 1000
        segment_laenge_m = 50.0
        gesamt_energie_kwh = 15.0
        energie_je_segment_kwh = gesamt_energie_kwh / anzahl_segmente

        segments = []
        energy_results = []
        for i in range(anzahl_segmente):
            lat = BERLIN_COORD[0] + i * 0.0002
            lon = BERLIN_COORD[1] + i * 0.0002
            naechste_lat = BERLIN_COORD[0] + (i + 1) * 0.0002
            naechste_lon = BERLIN_COORD[1] + (i + 1) * 0.0002
            segments.append(
                RouteSegment(
                    segment_index=i,
                    geometrie=[(lat, lon), (naechste_lat, naechste_lon)],
                    laenge_m=segment_laenge_m,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=110,
                    steigung_rohdaten=0.0,
                    bearing_deg=45.0,
                )
            )
            energy_results.append(
                SegmentEnergyResult(
                    segment_index=i,
                    energiebedarf_kwh=energie_je_segment_kwh,
                    rekuperation_kwh=0.0,
                    energiebedarf_brutto_kwh=energie_je_segment_kwh,
                    geschwindigkeit_m_s=30.0,
                    fahrzeit_s=segment_laenge_m / 30.0,
                    streckenlaenge_m=segment_laenge_m,
                )
            )

        route = Route(
            segments=segments,
            gesamtlaenge_m=anzahl_segmente * segment_laenge_m,
            geometrie=[s.geometrie[0] for s in segments] + [segments[-1].geometrie[1]],
        )

        mitte = anzahl_segmente // 2
        station = ChargingStation(
            station_id="mitte-station",
            name="Tesla Supercharger Mitte",
            coordinate=segments[mitte].geometrie[0],
            stalls={StallType.V3: 4},
            max_ladeleistung_kw=250.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )

        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=10.0,
        )

        constraints = OptimizationConstraints(min_soc_pct=5.0, ziel_soc_pct=20.0)
        optimizer = create_networkx_optimizer()

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[
                SegmentGradient(
                    segment_index=i,
                    steigung_prozent=0.0,
                    hoehendifferenz_m=0.0,
                    horizontale_distanz_m=segment_laenge_m,
                )
                for i in range(anzahl_segmente)
            ],
            energy_results=energy_results,
            charging_stations=[station],
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=100.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(plan.ladehalte) >= 1


class TestZeitbudgetBeruecksichtigtLadezeit:
    """Regressionstest: Bug - das Zeitbudget der Suche (`_estimate_max_time_buckets`)
    addierte nur einen fixen 5-Bucket-Sicherheitspuffer zur reinen Fahrzeit, ohne
    die fuer notwendige Ladestopps benoetigte Zeit einzurechnen. Bei Routen, die
    mehrere Ladestopps brauchen, verwarf die Suche dadurch faelschlich jeden Pfad
    ueber das Zeitbudget hinaus als "nicht fahrbar" (ValueError), obwohl die Route
    mit ausreichend Ladezeit sehr wohl fahrbar gewesen waere.
    """

    def test_zeitbudget_waechst_mit_benoetigten_ladestopps(self) -> None:
        """Bei gleicher Fahrstrecke muss das geschaetzte Zeitbudget fuer ein
        Szenario mit hohem Energiebedarf (viele Ladestopps noetig) deutlich
        groesser sein als fuer eines mit niedrigem Energiebedarf (kein Laden
        noetig) - der alte, fixe 5-Bucket-Puffer war fuer beide Szenarien
        identisch.
        """
        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=60.0,
        )
        constraints = OptimizationConstraints(max_ladezeit_s=3600)
        optimizer = create_networkx_optimizer()

        total_time_s = 300_000 / (110.0 * 1000 / 3600)  # reale Fahrzeit, Tempolimit 110 km/h, 300km
        buckets_ohne_laden = optimizer._estimate_max_time_buckets(
            total_time_s=total_time_s,
            total_energy_kwh=30.0,  # deutlich unter Akkukapazitaet - kein Laden noetig
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            waypoints=[],
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        )
        buckets_mit_vielen_ladestopps = optimizer._estimate_max_time_buckets(
            total_time_s=total_time_s,
            total_energy_kwh=300.0,  # 5x Akkukapazitaet - mehrere Ladestopps noetig
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            waypoints=[],
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
        )

        assert buckets_mit_vielen_ladestopps > buckets_ohne_laden

    def test_route_mit_mehreren_ladestopps_bleibt_fahrbar(self) -> None:
        """End-to-end: Eine Route, die 4 volle Ladezyklen braucht, MUSS trotz der
        dafuer noetigen Ladezeit (deutlich mehr als der alte fixe 75-Minuten-
        Puffer) als fahrbar erkannt werden, statt faelschlich mit
        'Kein erreichbarer Zielknoten' abgelehnt zu werden.
        """
        anzahl_segmente = 6
        segment_laenge_m = 200_000.0
        energie_je_segment_kwh = 40.0  # 6 * 40 = 240 kWh bei 60 kWh Akku (4x Kapazitaet)

        segments = []
        energy_results = []
        stations = []
        for i in range(anzahl_segmente):
            lat = BERLIN_COORD[0] + i * 1.0
            lon = BERLIN_COORD[1] + i * 1.0
            naechste_lat = BERLIN_COORD[0] + (i + 1) * 1.0
            naechste_lon = BERLIN_COORD[1] + (i + 1) * 1.0
            segments.append(
                RouteSegment(
                    segment_index=i,
                    geometrie=[
                        (lat, lon),
                        ((lat + naechste_lat) / 2, (lon + naechste_lon) / 2),
                        (naechste_lat, naechste_lon),
                    ],
                    laenge_m=segment_laenge_m,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=110,
                    steigung_rohdaten=0.0,
                    bearing_deg=45.0,
                )
            )
            energy_results.append(
                SegmentEnergyResult(
                    segment_index=i,
                    energiebedarf_kwh=energie_je_segment_kwh,
                    rekuperation_kwh=0.0,
                    energiebedarf_brutto_kwh=energie_je_segment_kwh,
                    geschwindigkeit_m_s=30.0,
                    fahrzeit_s=segment_laenge_m / 30.0,
                    streckenlaenge_m=segment_laenge_m,
                )
            )
            stations.append(
                ChargingStation(
                    station_id=f"station-{i}",
                    name=f"Tesla Supercharger {i}",
                    coordinate=((lat + naechste_lat) / 2, (lon + naechste_lon) / 2),
                    stalls={StallType.V3: 4},
                    max_ladeleistung_kw=250.0,
                    connector_types=[ConnectorType.CCS2],
                    country="DE",
                )
            )

        route = Route(
            segments=segments,
            gesamtlaenge_m=anzahl_segmente * segment_laenge_m,
            geometrie=[s.geometrie[0] for s in segments] + [segments[-1].geometrie[-1]],
        )

        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=60.0,
        )
        constraints = OptimizationConstraints(min_soc_pct=10.0, ziel_soc_pct=20.0)
        optimizer = create_networkx_optimizer()

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[
                SegmentGradient(
                    segment_index=i,
                    steigung_prozent=0.0,
                    hoehendifferenz_m=0.0,
                    horizontale_distanz_m=segment_laenge_m,
                )
                for i in range(anzahl_segmente)
            ],
            energy_results=energy_results,
            charging_stations=stations,
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(plan.ladehalte) >= 3


class TestLadehaltUeberlebtKnotenKollision:
    """Regressionstest: Bug - der Zustandsknoten-Schluessel `(segment_index,
    soc_bucket, time_bucket)` kann durch die Diskretisierung mit einer
    ANDEREN, bereits frueher angelegten Fahrtkante kollidieren (dieselbe
    Kombination aus Segment, gerundetem SoC und gerundeter Zeit, aber ueber
    eine Route ohne Ladehalt erreicht). `_fuege_ladekante_hinzu` initialisiert
    `type`/`station_id` nur beim ERSTEN Anlegen eines Knotens
    (`if next_node not in G.nodes`) - kollidiert eine spaeter gefundene,
    guenstigere Ladekante mit einem bereits bestehenden (kollidierenden)
    Knoten, bleibt dessen `type="drive"` ohne `station_id` bestehen, obwohl
    die tatsaechlich gewaehlte Kante sehr wohl eine Ladekante ist.
    `_extract_charging_stops` las `station_id` bisher vom KNOTEN und
    verschluckte den Ladehalt dadurch komplett aus dem Ergebnis - dessen
    Ladezeit floss aber sehr wohl in `gesamtreisezeit_s` ein (ueber die
    Kantenkosten), was sich als mehrminuetig falscher SoC/Position direkt
    nach dem betroffenen Ladehalt in `simulate_trip` zeigte (Diskrepanz
    zwischen realer Gesamtreisezeit und der Summe der extrahierten
    `ChargingStop`-Ladedauern).
    """

    def test_ladehalt_wird_trotz_kollidierendem_knoten_extrahiert(self) -> None:
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=15)
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        optimizer._base_time = base_time

        station = ChargingStation(
            station_id="kollisions-station",
            name="Kollisions-Station",
            coordinate=(52.0, 13.0),
            stalls={StallType.V3: 4},
            max_ladeleistung_kw=250.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
            letzte_datenAktualisierung=datetime.now(UTC),
        )

        G: nx.DiGraph = nx.DiGraph()

        # Ankunft an der Ladestation (Segment 5) mit niedrigem SoC.
        current = (5, 3, 4)
        current_zeitpunkt = base_time + timedelta(minutes=100)
        G.add_node(
            current,
            type="drive",
            soc_pct=15.0,
            zeitpunkt=current_zeitpunkt,
            segment_index=5,
            total_cost=1000.0,
            parent=None,
        )

        ziel_soc_pct = 80.0
        ladezeit_s = 900.0
        neuer_zeitpunkt = current_zeitpunkt + timedelta(seconds=ladezeit_s)
        new_soc_bucket = soc_to_bucket(ziel_soc_pct, optimizer.soc_step_pct)
        new_time_bucket = time_to_bucket(neuer_zeitpunkt, base_time, optimizer.time_step_min)
        target_key = (5, new_soc_bucket, new_time_bucket)

        # Kollidierender Knoten: von einer FRUEHER angelegten, unrelaten
        # Fahrtkante (keine Ladestation) erzeugt - identischer Schluessel,
        # aber ohne `station_id`/mit `type="drive"`. Sehr hohe `total_cost`
        # stellt sicher, dass die spaeter hinzugefuegte Ladekante guenstiger
        # ist und den Pfad tatsaechlich gewinnt (siehe `_fuege_ladekante_hinzu`).
        G.add_node(
            target_key,
            type="drive",
            soc_pct=61.0,
            zeitpunkt=base_time + timedelta(minutes=90),
            segment_index=5,
            total_cost=1e8,
            parent=None,
        )

        builder = StateGraphBuilder(
            soc_step_pct=optimizer.soc_step_pct,
            time_step_min=optimizer.time_step_min,
            base_time=base_time,
            avg_verbrauch_kwh_pro_m=0.0,
        )
        heap: list[tuple[float, int, tuple[int, int, int]]] = []
        builder.fuege_ladekante_hinzu(
            G=G,
            current=current,
            seg_idx=5,
            station=station,
            ankunfts_soc_pct=15.0,
            ziel_soc_pct=ziel_soc_pct,
            ladezeit_s=ladezeit_s,
            detour_zeit_s_je_richtung=0.0,
            detour_soc_pct_je_richtung=0.0,
            max_time_buckets=10_000,
            heap=heap,
        )

        # Vorbedingung des Bugs bestaetigt: der Knoten wurde NICHT neu
        # angelegt, sein `type` blieb "drive" ohne Node-`station_id`.
        assert G.nodes[target_key]["type"] == "drive"
        assert G.nodes[target_key].get("station_id") is None

        constraints = OptimizationConstraints()
        ladehalte = optimizer._extract_charging_stops(
            G=G,
            path=[current, target_key],
            segments=[],
            charging_stations=[station],
            constraints=constraints,
        )

        assert len(ladehalte) == 1
        ladehalt = ladehalte[0]
        assert ladehalt.station.station_id == "kollisions-station"
        assert ladehalt.ankunfts_soc_pct == 15.0
        assert ladehalt.ziel_soc_pct == ziel_soc_pct
        assert ladehalt.geschaetzte_ladedauer_s == int(ladezeit_s)
        assert ladehalt.ankunftszeit == current_zeitpunkt
        assert ladehalt.abfahrtszeit == neuer_zeitpunkt


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


class TestLadedauerVorgabe:
    """Tests für vom Nutzer vorgegebene feste Ladedauern (`ladedauer_vorgaben`)."""

    def _basis_szenario(
        self,
    ) -> tuple[
        Route, list[SegmentGradient], list[SegmentEnergyResult], VehicleProfile, ChargingStation
    ]:
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
        gradients = [
            SegmentGradient(
                segment_index=0,
                steigung_prozent=0.0,
                hoehendifferenz_m=0.0,
                horizontale_distanz_m=60_000,
            )
        ]
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
        station = ChargingStation(
            station_id="station_001",
            name="Tesla Supercharger Ziel",
            coordinate=(53.0, 12.0),
            stalls={StallType.V3: 4},
            max_ladeleistung_kw=250.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )
        return route, gradients, energy_results, vehicle_profile, station

    def test_feste_ladedauer_wird_exakt_uebernommen(self) -> None:
        """Eine vorgegebene Ladedauer für die gewählte Station wird exakt (nicht nur
        näherungsweise über die SoC-Ziel-Iteration) als `geschaetzte_ladedauer_s`
        übernommen - unabhängig von `constraints.max_ladezeit_s`."""
        route, gradients, energy_results, vehicle_profile, station = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=45.0)
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=15)

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=[station],
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=20.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            ladedauer_vorgaben={"station_001": 1800},
        )

        assert len(plan.ladehalte) == 1
        ladehalt = plan.ladehalte[0]
        assert ladehalt.station.station_id == "station_001"
        assert ladehalt.geschaetzte_ladedauer_s == 1800
        assert (ladehalt.abfahrtszeit - ladehalt.ankunftszeit).total_seconds() == 1800
        # Ziel-SoC muss höher als der Ankunfts-SoC sein (es wurde tatsächlich geladen).
        assert ladehalt.ziel_soc_pct > ladehalt.ankunfts_soc_pct

    def test_ohne_vorgabe_weicht_ladedauer_von_der_vorgabe_ab(self) -> None:
        """Ohne `ladedauer_vorgaben` berechnet der Optimierer die Ladedauer wie bisher
        automatisch - als Kontrast zum exakten Vorgabewert im anderen Test."""
        route, gradients, energy_results, vehicle_profile, station = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=45.0)
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=15)

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=[station],
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=20.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(plan.ladehalte) == 1
        assert plan.ladehalte[0].geschaetzte_ladedauer_s != 1800


class TestWaypointSegmentMatchingIgnoriertFrueheKreuzungen:
    """Regressionstest: Bug - `_waypoint_to_segment` suchte den naechstgelegenen
    Segment-Startpunkt ueber die GESAMTE Route per reiner Luftlinien-Distanz,
    unabhaengig davon, wo entlang der Route bereits vorherige Zwischenstopps
    aufgeloest wurden. Bei einer Route, die sich selbst kreuzt oder in der
    Naehe eines fruehen Streckenabschnitts eine Schleife dreht (z. B. eine
    kleine Ortsdurchfahrt), konnte ein spaeterer Zwischenstopp faelschlich auf
    diesen geometrisch naeheren, aber weit VOR dem tatsaechlichen Zwischenstopp
    liegenden fruehen Streckenabschnitt gemappt werden. Sichtbar auf der Karte
    als SoC-Farbverlaufssprung weit entfernt vom tatsaechlichen Zwischenstopp-
    Marker (der SoC-Sprung landete auf dem falschen, frueheren Segment-Index).
    """

    def _kreuzende_segmente(self) -> list[RouteSegment]:
        """Route mit 10 Segmenten: Segment 2 liegt exakt auf der spaeter
        angefragten Zwischenstopp-Koordinate (eine fruehe, unabhaengige
        Kreuzung der Route), Segment 7 ist der tatsaechliche Via-Punkt (etwas
        weiter entfernt, aber der eigentlich gemeinte Zwischenstopp weiter
        entlang der Route)."""
        segmente = []
        for i in range(10):
            if i == 2:
                start = (60.0, 14.0)  # exakter Treffer, aber falsche Kreuzung
            elif i == 7:
                start = (60.0001, 14.0)  # ~11 m entfernt - der echte Via-Punkt
            else:
                start = (61.0 + i, 20.0 + i)  # weit entfernt, irrelevant
            segmente.append(
                RouteSegment(
                    segment_index=i,
                    geometrie=[start, (start[0] + 0.01, start[1] + 0.01)],
                    laenge_m=1000.0,
                    strassenklasse="MOTORWAY",
                    bearing_deg=0.0,
                )
            )
        return segmente

    def test_unbeschraenkte_suche_findet_die_falsche_fruehe_kreuzung(self) -> None:
        """Demonstriert den Bug: eine Suche ohne Mindest-Segment-Index landet
        auf der geometrisch naeheren, aber falschen fruehen Kreuzung."""
        optimizer = create_networkx_optimizer()
        segmente = self._kreuzende_segmente()
        wp = Waypoint(koordinate=(60.0, 14.0))

        assert optimizer._waypoint_to_segment(wp, segmente) == 2

    def test_suche_ab_vorherigem_zwischenstopp_findet_den_echten_via_punkt(
        self,
    ) -> None:
        """Mit dem Fix: sobald ein vorheriger Zwischenstopp bereits Segment 5
        aufgeloest hat, schliesst die Suche fuer den naechsten Zwischenstopp
        die fruehere Kreuzung (Segment 2) aus und findet den tatsaechlichen
        Via-Punkt (Segment 7)."""
        optimizer = create_networkx_optimizer()
        segmente = self._kreuzende_segmente()
        wp = Waypoint(koordinate=(60.0, 14.0))

        assert optimizer._waypoint_to_segment(wp, segmente, 5) == 7

    def test_map_waypoints_to_segments_prefers_exact_route_via_point_indices(
        self,
    ) -> None:
        """Liefert `route.via_point_indices` einen exakten Segment-Index (von
        `GraphHopperRoutingProvider`/`FakeRoutingProvider`), wird dieser statt
        der mehrdeutigen Naechster-Punkt-Suche verwendet - selbst wenn diese
        (wie in `test_unbeschraenkte_suche_findet_die_falsche_fruehe_kreuzung`)
        eine falsche, geometrisch naehere fruehe Kreuzung faende."""
        optimizer = create_networkx_optimizer()
        segmente = self._kreuzende_segmente()
        wp = Waypoint(koordinate=(60.0, 14.0))
        route = Route(
            segments=segmente,
            gesamtlaenge_m=sum(s.laenge_m for s in segmente),
            geometrie=[s.geometrie[0] for s in segmente] + [segmente[-1].geometrie[1]],
            via_point_indices=[7],
        )

        assert optimizer._map_waypoints_to_segments(
            waypoints=[wp], segments=segmente, route=route
        ) == [7]

    def test_map_waypoints_to_segments_falls_back_without_via_point_indices(
        self,
    ) -> None:
        """Ohne (oder mit einer laengenunpassenden) `route.via_point_indices`
        faellt die Zuordnung auf die monotone Naechster-Punkt-Suche zurueck."""
        optimizer = create_networkx_optimizer()
        segmente = self._kreuzende_segmente()
        wp = Waypoint(koordinate=(60.0, 14.0))
        route = Route(
            segments=segmente,
            gesamtlaenge_m=sum(s.laenge_m for s in segmente),
            geometrie=[s.geometrie[0] for s in segmente] + [segmente[-1].geometrie[1]],
        )

        assert optimizer._map_waypoints_to_segments(
            waypoints=[wp], segments=segmente, route=route
        ) == [2]


class TestZwischenstoppErzwingtWartezeit:
    """Regressionstest: Bug - eine an einem Zwischenstopp gesetzte
    `geplante_abfahrt`/`aufenthaltsdauer` war nur ein optionaler Kostenfaktor,
    den der A*-Optimierer als teurer verworfen hat (die Fahrt "sprang" direkt
    weiter, ohne zu warten) - sichtbar als falsche (zu frühe) Ankunftszeit am
    Fahrtziel trotz gesetzter Abfahrtszeit an einem Zwischenstopp.
    """

    def _basis_szenario(
        self,
    ) -> tuple[Route, list[SegmentEnergyResult], VehicleProfile]:
        route = Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[BERLIN_COORD, (52.5, 12.5)],
                    laenge_m=50_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=130,
                    steigung_rohdaten=0.0,
                    bearing_deg=310.0,
                ),
                RouteSegment(
                    segment_index=1,
                    geometrie=[(52.5, 12.5), (53.0, 11.5)],
                    laenge_m=50_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=120,
                    steigung_rohdaten=0.0,
                    bearing_deg=290.0,
                ),
                RouteSegment(
                    segment_index=2,
                    geometrie=[(53.0, 11.5), HAMBURG_COORD],
                    laenge_m=50_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=110,
                    steigung_rohdaten=0.0,
                    bearing_deg=270.0,
                ),
            ],
            gesamtlaenge_m=150_000,
            geometrie=[BERLIN_COORD, (52.5, 12.5), (53.0, 11.5), HAMBURG_COORD],
        )
        energy_results = [
            SegmentEnergyResult(
                segment_index=0,
                energiebedarf_kwh=8.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=8.0,
                geschwindigkeit_m_s=30.0,
                fahrzeit_s=1667,
                streckenlaenge_m=50_000,
            ),
            SegmentEnergyResult(
                segment_index=1,
                energiebedarf_kwh=8.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=8.0,
                geschwindigkeit_m_s=25.0,
                fahrzeit_s=2000,
                streckenlaenge_m=50_000,
            ),
            SegmentEnergyResult(
                segment_index=2,
                energiebedarf_kwh=8.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=8.0,
                geschwindigkeit_m_s=20.0,
                fahrzeit_s=2500,
                streckenlaenge_m=50_000,
            ),
        ]
        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=62.5,
        )
        return route, energy_results, vehicle_profile

    def test_geplante_abfahrt_verzoegert_ankunft_am_ziel(self) -> None:
        """Eine über Nacht geplante Abfahrt an einem Zwischenstopp MUSS die
        Gesamtreisezeit um die volle Wartezeit verlängern - nicht ignoriert
        werden (siehe Nutzer-Report: Start 6:15, Ankunft am Zwischenstopp
        18:44, geplante Abfahrt dort erst am Folgetag 6:30, aber Ankunft am
        Ziel bereits um 22:55 desselben Tages berechnet)."""
        route, energy_results, vehicle_profile = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=35.0)
        optimizer = create_networkx_optimizer()
        abfahrtszeit = datetime(2026, 8, 30, 6, 15, 0)
        geplante_abfahrt = datetime(2026, 8, 31, 6, 30, 0)
        wp = Waypoint(koordinate=(52.5, 12.5), geplante_abfahrt=geplante_abfahrt)

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[],
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[wp],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
        )

        ankunft_ziel = abfahrtszeit + timedelta(seconds=plan.gesamtreisezeit_s)
        # Die Ankunft am Ziel MUSS nach der geplanten Abfahrt am Zwischenstopp
        # liegen - eine bypassbare Wartezeit wuerde stattdessen weit vor
        # `geplante_abfahrt` ankommen (reine Fahrzeit ohne Wartezeit).
        assert ankunft_ziel > geplante_abfahrt
        assert len(plan.zwischenstopp_aufenthalte) == 1
        aufenthalt = plan.zwischenstopp_aufenthalte[0]
        assert aufenthalt.abfahrtszeit == geplante_abfahrt
        assert aufenthalt.ankunftszeit < geplante_abfahrt

    def test_geplante_abfahrt_vor_ankunft_erzwingt_keine_wartezeit(self) -> None:
        """Liegt `geplante_abfahrt` vor der tatsaechlichen Ankunft, wird KEINE
        Wartezeit erzwungen - der Zwischenstopp bleibt optional passierbar."""
        route, energy_results, vehicle_profile = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=35.0)
        optimizer = create_networkx_optimizer()
        abfahrtszeit = datetime(2026, 8, 30, 6, 15, 0)
        wp = Waypoint(koordinate=(52.5, 12.5), geplante_abfahrt=abfahrtszeit + timedelta(minutes=1))

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[],
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[wp],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
        )

        # Reine Fahrzeit (keine erzwungene Wartezeit): Summe der drei
        # Segment-Fahrzeiten.
        assert plan.gesamtreisezeit_s == 1667 + 2000 + 2500
        assert plan.zwischenstopp_aufenthalte == []

    def test_ladeleistung_kw_laedt_waehrend_erzwungener_wartezeit(self) -> None:
        """Ist an einem Zwischenstopp mit erzwungener Wartezeit eine
        Ladeleistung angegeben, wird der SoC waehrend der Wartezeit erhoeht -
        die Ladeleistung ist optional und ohne sie bleibt der SoC unveraendert
        (siehe `test_geplante_abfahrt_verzoegert_ankunft_am_ziel`)."""
        route, energy_results, vehicle_profile = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=35.0)
        optimizer = create_networkx_optimizer()
        abfahrtszeit = datetime(2026, 8, 30, 6, 15, 0)
        wp = Waypoint(
            koordinate=(52.5, 12.5),
            geplante_abfahrt=abfahrtszeit + timedelta(hours=2),
            ladeleistung_kw=11.0,
        )

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[],
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[wp],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=50.0,
            abfahrtszeit=abfahrtszeit,
        )

        assert len(plan.zwischenstopp_aufenthalte) == 1
        aufenthalt = plan.zwischenstopp_aufenthalte[0]
        assert aufenthalt.ladeleistung_kw == 11.0
        assert aufenthalt.ziel_soc_pct > aufenthalt.ankunfts_soc_pct

    def test_ohne_ladeleistung_kw_bleibt_soc_waehrend_wartezeit_unveraendert(self) -> None:
        """Ohne `ladeleistung_kw` bleibt der SoC waehrend der erzwungenen
        Wartezeit unveraendert (kein automatisches Laden ohne Ladepunkt)."""
        route, energy_results, vehicle_profile = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=35.0)
        optimizer = create_networkx_optimizer()
        abfahrtszeit = datetime(2026, 8, 30, 6, 15, 0)
        wp = Waypoint(koordinate=(52.5, 12.5), geplante_abfahrt=abfahrtszeit + timedelta(hours=2))

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[],
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[wp],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
        )

        aufenthalt = plan.zwischenstopp_aufenthalte[0]
        assert aufenthalt.ladeleistung_kw is None
        assert aufenthalt.ziel_soc_pct == aufenthalt.ankunfts_soc_pct


class TestFaehrZeitfenster:
    """Tests für vom Nutzer vorgegebene Fährfahrpläne (`faehr_zeitfenster`)."""

    def _basis_szenario(
        self,
    ) -> tuple[Route, list[SegmentGradient], list[SegmentEnergyResult], VehicleProfile]:
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
                    laenge_m=20_000,
                    strassenklasse="FERRY",
                    road_environment="FERRY",
                    bearing_deg=280.0,
                ),
                RouteSegment(
                    segment_index=2,
                    geometrie=[(53.3, 11.0), HAMBURG_COORD],
                    laenge_m=30_000,
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
        # Segment 1 (die Fähre) bekommt einen absichtlich UNFAHRBAR hohen
        # Energiebedarf (500 kWh bei einer 62.5-kWh-Batterie ohne
        # Ladestationen) - würde der Optimierer sie trotz Fährfahrplan-Pin
        # als normale Fahrtkante behandeln, wäre die Route physikalisch nicht
        # fahrbar. Ein erfolgreicher Plan beweist also, dass `_add_ferry_edge`
        # tatsächlich anstelle von `_add_drive_edge` verwendet wurde.
        energy_results = [
            SegmentEnergyResult(
                segment_index=0,
                energiebedarf_kwh=5.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=5.0,
                geschwindigkeit_m_s=30.0,
                fahrzeit_s=1667,
                streckenlaenge_m=50_000,
            ),
            SegmentEnergyResult(
                segment_index=1,
                energiebedarf_kwh=500.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=500.0,
                geschwindigkeit_m_s=10.0,
                fahrzeit_s=2000,
                streckenlaenge_m=20_000,
            ),
            SegmentEnergyResult(
                segment_index=2,
                energiebedarf_kwh=3.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=3.0,
                geschwindigkeit_m_s=20.0,
                fahrzeit_s=1000,
                streckenlaenge_m=30_000,
            ),
        ]
        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=62.5,
        )
        return route, gradients, energy_results, vehicle_profile

    def test_faehre_wird_in_einem_sprung_ohne_soc_verbrauch_ueberquert(self) -> None:
        """Eine gepinnte Fähre wird als ein Sprung ohne SoC-Verbrauch modelliert; die
        Gesamtreisezeit ergibt sich aus Wartezeit bis zur Abfahrt plus Überfahrts-
        und Restfahrzeit."""
        route, gradients, energy_results, vehicle_profile = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=50.0)
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=15)
        abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        faehr_abfahrt = abfahrtszeit + timedelta(minutes=45)
        faehr_ankunft = faehr_abfahrt + timedelta(minutes=30)

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
            faehr_zeitfenster={1: (2, faehr_abfahrt, faehr_ankunft)},
        )

        assert plan.ladehalte == []  # kein Ladehalt noetig (Segment 1 kostet kein SoC)

        # Restfahrzeit nach der Faehre = das TATSAECHLICHE `fahrzeit_s` von
        # Segment 2 (siehe `_basis_szenario`/`energy_results[2]`), nicht eine
        # pauschale 110-km/h-Annahme (siehe optimizer.py: `_add_drive_edge`
        # nutzt jetzt `SegmentEnergyResult.fahrzeit_s` je Segment).
        drive_seg2_s = energy_results[2].fahrzeit_s
        erwartete_gesamtzeit_s = (faehr_ankunft - abfahrtszeit).total_seconds() + drive_seg2_s
        assert plan.gesamtreisezeit_s == pytest.approx(erwartete_gesamtzeit_s, abs=1.0)

    def test_verpasste_faehre_macht_route_unfahrbar(self) -> None:
        """Liegt die vorgegebene Abfahrt VOR der tatsächlichen Ankunft am Fähr-
        Terminal, ist die Fähre für diesen Pfad nicht mehr nutzbar - die Route
        gilt als nicht fahrbar (keine andere Kante ersetzt die übersprungene
        Fahrtkante)."""
        route, gradients, energy_results, vehicle_profile = self._basis_szenario()
        constraints = OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=50.0)
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=15)
        abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        # Ankunft am Terminal nach Segment 0 ist ca. 08:27 Uhr - eine Abfahrt
        # um 08:05 Uhr wurde also bereits verpasst.
        faehr_abfahrt = abfahrtszeit + timedelta(minutes=5)
        faehr_ankunft = faehr_abfahrt + timedelta(minutes=30)

        with pytest.raises(ValueError, match="Kein erreichbarer Zielknoten"):
            optimizer.optimize(
                route=route,
                segments=route.segments,
                gradients=gradients,
                energy_results=energy_results,
                charging_stations=[],
                waypoints=[],
                vehicle_profile=vehicle_profile,
                constraints=constraints,
                start_soc_pct=80.0,
                abfahrtszeit=abfahrtszeit,
                faehr_zeitfenster={1: (2, faehr_abfahrt, faehr_ankunft)},
            )


class TestDominanzPruningVerhindertKombinatorischeExplosion:
    """Regressionstest: Bug - jeder Zustandsknoten war ueber das volle Tripel
    `(segment_index, soc_bucket, time_bucket)` eindeutig, obwohl `total_cost`
    in diesem Modell ueberall EXAKT der seit Abfahrt verstrichenen Zeit
    entspricht (jede Kante ist eine Zeitdauer - Fahrzeit/Ladezeit/Wartezeit)
    und kein Folgezustand je von einer SPAETEREN Ankunft bei GLEICHER
    Position+SoC profitiert (weder Energieverbrauch noch Ladekurve noch
    `max_time_buckets` noch Fähr-Abfahrtsfenster haengen vom Kalenderzeit-
    punkt ab - fruehere Ankunft heisst hoechstens laenger warten, nie eine
    Faehre verpassen). Ohne Pruning wurden pro Entscheidungspunkt bis zu
    O(SoC-Buckets x Zeit-Buckets) tatsaechlich erweiterte (dominierte)
    Knoten gehalten statt O(SoC-Buckets) - bei Routen mit vielen
    Ladestationen UND einer teuren Kandidaten-Bewertung pro Knoten (siehe
    `_lade_ziel_kandidaten`/`_kandidaten_mit_mindestladedauer`, beide mit
    Bisektionen ueber die Ladekurve) eskalierte das von einigen Sekunden
    zu einem praktischen Haenger (Nutzer-Report: > 100s fuer die reale
    Optimierung einer Deutschland->Schweden-Route mit vielen Ladestationen).
    """

    def test_grosse_stationsanzahl_optimiert_in_angemessener_zeit(self) -> None:
        """Eine synthetische Route mit 150 Ladestationen-Entscheidungspunkten
        MUSS mit den Produktions-Defaults (inkl. Kandidaten-Anreicherung und
        Mindestladedauer) in wenigen Sekunden optimieren - nicht in Minuten.
        Ohne das Dominanz-Pruning uebersteigt dieselbe Route (verifiziert
        manuell gegen den Stand vor diesem Fix) bereits bei 200 Stationen
        180s, ohne innerhalb dieser Zeit ueberhaupt fertigzuwerden.
        """
        anzahl_stationen = 150
        segment_laenge_m = 20_000.0
        rng = random.Random(1)  # nolint: deterministisch, kein Sicherheits-RNG

        segments = []
        energy_results = []
        lat, lon = BERLIN_COORD
        dlat = 30.0 / anzahl_stationen
        for i in range(anzahl_stationen):
            naechste_lat = lat + dlat
            naechste_lon = lon + dlat * 0.5
            energie_kwh = rng.uniform(10.0, 30.0)
            segments.append(
                RouteSegment(
                    segment_index=i,
                    geometrie=[
                        (lat, lon),
                        ((lat + naechste_lat) / 2, (lon + naechste_lon) / 2),
                        (naechste_lat, naechste_lon),
                    ],
                    laenge_m=segment_laenge_m,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=110,
                    steigung_rohdaten=0.0,
                    bearing_deg=45.0,
                )
            )
            energy_results.append(
                SegmentEnergyResult(
                    segment_index=i,
                    energiebedarf_kwh=energie_kwh,
                    rekuperation_kwh=0.0,
                    energiebedarf_brutto_kwh=energie_kwh,
                    geschwindigkeit_m_s=30.0,
                    fahrzeit_s=segment_laenge_m / 30.0,
                    streckenlaenge_m=segment_laenge_m,
                )
            )
            lat, lon = naechste_lat, naechste_lon

        stations = [
            ChargingStation(
                station_id=f"station-{i}",
                name=f"Supercharger {i}",
                coordinate=segments[i].geometrie[1],
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=250.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
            for i in range(1, anzahl_stationen)
        ]

        route = Route(
            segments=segments,
            gesamtlaenge_m=sum(s.laenge_m for s in segments),
            geometrie=[s.geometrie[0] for s in segments] + [segments[-1].geometrie[-1]],
        )
        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=75.0,
        )
        constraints = OptimizationConstraints(min_soc_pct=10.0, ziel_soc_pct=5.0)
        gradients = [
            SegmentGradient(
                segment_index=i,
                steigung_prozent=0.0,
                hoehendifferenz_m=0.0,
                horizontale_distanz_m=segment_laenge_m,
            )
            for i in range(anzahl_stationen)
        ]
        optimizer = create_networkx_optimizer()  # Produktions-Defaults

        start = time.perf_counter()
        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=stations,
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=100.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )
        dauer_s = time.perf_counter() - start

        assert plan.ladehalte  # Plausibilitaet: Route mit vielen Stationen braucht Ladehalte
        # Grosszuegige Grenze (gemessen nach dem ChargingCurve-Fastpath-Fix,
        # siehe `battery.models._evaluate_fast_path`/`_ChargingCurveFastPath`,
        # und der reduzierten Bisektions-Iterationszahl in
        # `_soc_nach_fester_ladezeit`: ~6s auf dieser Entwicklerhardware,
        # zuvor > 120s) - haelt robust Puffer fuer langsamere CI-Maschinen,
        # waehrend sie sowohl eine Rueckkehr zur alten Kombinatorik als auch
        # eine Rueckkehr zur scipy-Pro-Aufruf-Ladekurvenauswertung faengt.
        assert dauer_s < 15.0, (
            f"Optimierung brauchte {dauer_s:.1f}s fuer {anzahl_stationen} Stationen - "
            "deutet auf eine Regression der Dominanz-Pruning-Optimierung in "
            "_generate_graph ODER der ChargingCurve-Fastpath-Optimierung hin "
            "(siehe Klassen-Docstring bzw. battery.models.ChargingCurve)."
        )


class TestGraphKonstruktionFindetDijkstraOptimum:
    """Regressionstest: Bug - `_generate_graph` erweiterte den Zustandsgraphen
    per FIFO-BFS (`deque`/`popleft`) statt per Dijkstra-Min-Heap. Dabei konnte
    ein Knoten als "final" markiert (und seine ausgehenden Kanten erzeugt)
    werden, WAEHREND `total_cost`/`soc_pct` dieses Knotens durch einen
    spaeter entdeckten, tatsaechlich guenstigeren Pfad noch verbessert
    wurden (siehe Kommentar in `_generate_graph`) - die ausgehenden Kanten
    blieben dabei auf dem VERALTETEN (schlechteren) Zustand basiert. Das
    fuehrte dazu, dass der A*-Suchraum den wahren guenstigsten Pfad gar
    nicht erst enthielt, obwohl er physikalisch fahrbar gewesen waere -
    sichtbar u. a. als unnoetig lang dauernder Ladehalt (Nutzer-Report:
    Ladehalt in Kamen von 70% auf 80%, obwohl der naechste Halt in Holdorf
    ohnehin mit 24% SoC erreicht wurde - die 10 Prozentpunkte Ladung in
    Kamen waren komplett unnoetig und kosteten nur Zeit).
    """

    def _sechs_segmente_szenario(
        self,
    ) -> tuple[
        Route,
        list[SegmentGradient],
        list[SegmentEnergyResult],
        list[ChargingStation],
        VehicleProfile,
    ]:
        # 6 gleich lange Segmente mit unterschiedlichem Energiebedarf -
        # erzeugt an den Ladestationen mehrere SoC-/Zeit-Diskretisierungs-
        # Buckets, die sich je nach gewaehltem Ladeziel an einer FRUEHEREN
        # Station spaeter wieder ueberschneiden koennen (Voraussetzung fuer
        # den oben beschriebenen FIFO-Bug).
        energie_je_segment_kwh = [39.0, 21.7, 20.6, 20.9, 28.2, 33.3]
        segments = []
        energy_results = []
        lat, lon = BERLIN_COORD
        for i, energie_kwh in enumerate(energie_je_segment_kwh):
            naechste_lat = lat + 1.0
            naechste_lon = lon + 1.0
            segments.append(
                RouteSegment(
                    segment_index=i,
                    geometrie=[
                        (lat, lon),
                        ((lat + naechste_lat) / 2, (lon + naechste_lon) / 2),
                        (naechste_lat, naechste_lon),
                    ],
                    laenge_m=100_000.0,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=110,
                    steigung_rohdaten=0.0,
                    bearing_deg=45.0,
                )
            )
            energy_results.append(
                SegmentEnergyResult(
                    segment_index=i,
                    energiebedarf_kwh=energie_kwh,
                    rekuperation_kwh=0.0,
                    energiebedarf_brutto_kwh=energie_kwh,
                    geschwindigkeit_m_s=30.0,
                    fahrzeit_s=100_000.0 / 30.0,
                    streckenlaenge_m=100_000.0,
                )
            )
            lat, lon = naechste_lat, naechste_lon

        # Eine Ladestation nach jedem Segment (ausser dem letzten) - jeweils
        # nahe am Mittelpunkt des NAECHSTEN Segments platziert, damit sie
        # dem Segment NACH der bereits gefahrenen Teilstrecke zugeordnet
        # wird (siehe `_station_to_segment`).
        stations = [
            ChargingStation(
                station_id=f"station-{i}",
                name=f"Supercharger {i}",
                coordinate=segments[i].geometrie[1],
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=250.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
            for i in range(1, len(segments))
        ]

        route = Route(
            segments=segments,
            gesamtlaenge_m=sum(s.laenge_m for s in segments),
            geometrie=[s.geometrie[0] for s in segments] + [segments[-1].geometrie[-1]],
        )
        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=100.0,
        )
        gradients = [
            SegmentGradient(
                segment_index=i,
                steigung_prozent=0.0,
                hoehendifferenz_m=0.0,
                horizontale_distanz_m=100_000.0,
            )
            for i in range(len(segments))
        ]
        return route, gradients, energy_results, stations, vehicle_profile

    def test_optimierer_findet_das_globale_zeitoptimum_ueber_ladehalte_hinweg(self) -> None:
        """End-to-end: die Gesamtreisezeit MUSS dem echten globalen Zeit-
        optimum ueber ALLE Ladehalte hinweg entsprechen (21744s bei dieser
        bewusst groben Diskretisierung), nicht einer schlechteren Naeherung.

        Zwei Mechanismen tragen dazu bei und werden hier gemeinsam verifiziert:

        1. Dijkstra-korrekte Graphkonstruktion (`_generate_graph`, Min-Heap
           statt FIFO-BFS) - ohne sie wuerde bereits die Kombination aus
           frueh gewaehlten Ladehalten suboptimal bleiben.
        2. Reichweiten-/kurvenbasierte Ladeziel-Kandidaten
           (`_lade_ziel_kandidaten`) statt starrer 80/90/100%-Rundwerte -
           mit dem Default `mindest_ankunfts_soc_pct=5.0` darf die Suche an
           der LETZTEN Station bis auf 5% herunterfahren (statt vorzeitig an
           einer FRUEHEREN Station mehr zu laden als noetig) und dort die
           besonders schnelle Ladeleistung im unteren SoC-Bereich der
           Ladekurve ausnutzen. Das eigentliche Fahrtziel (kein weiterer
           Entscheidungspunkt nach station-5) verlangt selbst nur noch
           `ziel_soc_target` (hier 5.0%, siehe `optimize()`) statt des
           allgemeinen `min_soc_pct` - die Fahrt endet dort, ein zusaetzliches
           Offene-Strecke-Sicherheitsminimum ist nicht einschlaegig.

        Die erwartete Gesamtzeit (21744s) und die Ankunfts-SoC-Werte wurden
        fuer die produktiv genutzte `model_3_sr`-Ladekurve (siehe Commit
        "use the actual model_3_sr charging curve") neu ermittelt: ein Lauf
        mit deutlich feinerer Diskretisierung (`soc_step_pct=1.0,
        time_step_min=5`) liefert 21723s - nur 21s weniger, was bestaetigt,
        dass 21744s bei DIESER bewusst groben Test-Diskretisierung bereits
        nahe am echten Optimum liegt, statt einer zufaelligen schlechteren
        Naeherung zu entsprechen.
        """
        route, gradients, energy_results, stations, vehicle_profile = (
            self._sechs_segmente_szenario()
        )
        constraints = OptimizationConstraints(
            min_soc_pct=10.0,
            ziel_soc_pct=10.0,
            # Explizit 0: dieser Test isoliert Dijkstra + Reichweiten-/Kurven-
            # Kandidaten von der SEPARATEN Mindestladedauer-Funktionalitaet
            # (siehe `TestMindestLadedauerVerhindertKurzeLadehalte`), die mit
            # ihrem eigenen Produktions-Default (600s) sonst den kurzen
            # 376s-Halt an station-4 aus diesem Szenario entfernen wuerde.
            mindest_ladezeit_s=0,
        )
        # Bewusst grobe Diskretisierung: begünstigt die Bucket-Kollisionen,
        # die den (mittlerweile behobenen) FIFO-Bug ueberhaupt erst sichtbar
        # gemacht haetten (bei der feinen Produktions-Default-Aufloesung von
        # 1%/15min faellt die Kollision fuer dieses konkrete Szenario nicht
        # ins Gewicht).
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=20)

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=stations,
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=100.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert [s.station.station_id for s in plan.ladehalte] == [
            "station-3",
            "station-4",
            "station-5",
        ]
        # Ausnutzung des niedrigen, per `mindest_ankunfts_soc_pct` (Default
        # 5.0) erlaubten Ankunfts-SoC an der LETZTEN Ladestation - genau der
        # vom Nutzer gewuenschte Effekt (schnelles Laden im unteren SoC-
        # Bereich statt unnoetig frueher Teilladung). An station-4 begrenzt
        # bei DIESER groben Diskretisierung (`soc_step_pct=5.0`) die naechst-
        # groebere Kandidaten-Stufe (9.1% statt exakt 5.0%) den erreichbaren
        # Ankunfts-SoC leicht - mit feinerer Aufloesung (`soc_step_pct=1.0`)
        # sinkt er ebenfalls auf 5.0% (siehe Docstring oben).
        assert [round(s.ankunfts_soc_pct, 1) for s in plan.ladehalte][-2:] == [9.1, 5.0]
        assert plan.gesamtreisezeit_s == 21744


class TestMindestLadedauerVerhindertKurzeLadehalte:
    """Tests für `OptimizationConstraints.mindest_ladezeit_s`: ein Kandidat-
    Ladeziel, dessen Ladezeit darunter läge, wird auf die Mindestdauer
    gestreckt statt verworfen (siehe `_kandidaten_mit_mindestladedauer`) -
    ein tatsächlicher Ladehalt dauert dadurch entweder gar nicht oder
    mindestens `mindest_ladezeit_s` (Nutzer-Report: 1-Minuten-Ladehalt,
    gefolgt von einem weiteren Halt nach nur gut 10 Minuten Fahrt).
    """

    def test_kandidaten_werden_auf_mindestladedauer_gestreckt(self) -> None:
        """Direkter Test der Kandidaten-Transformation: ein Kandidat, dessen
        Ladezeit unter der Mindestdauer läge, wird auf das SoC angehoben,
        das GENAU die Mindestdauer ergibt; ein bereits ausreichend langer
        Kandidat bleibt unverändert; mehrere zu kurze Kandidaten, die auf
        dasselbe gestreckte Ziel abgebildet werden, sind im Ergebnis nur
        einmal enthalten (Deduplizierung)."""
        create_networkx_optimizer()
        ladekurve = LadekurveReferenz.model_3_lr_v3()
        batteriekapazitaet_kwh = 75.0

        # 70% -> 71%/72% laden dauert bei dieser Kurve deutlich unter 600s
        # (siehe Kurvenpunkt 50-80% bei 150kW); 70% -> 95% dauert deutlich
        # laenger als 600s und bleibt daher unveraendert.
        ergebnis = charging_math.kandidaten_mit_mindestladedauer(
            kandidaten=[71.0, 72.0, 95.0],
            ankunft_soc_pct=70.0,
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=batteriekapazitaet_kwh,
            mindest_ladezeit_s=600.0,
        )

        # Die beiden zu kurzen Kandidaten (71%/72%) wurden auf dasselbe,
        # per `_soc_nach_fester_ladezeit` bestimmte SoC gestreckt.
        gestrecktes_soc = charging_math.soc_nach_fester_ladezeit(
            start_soc_pct=70.0,
            ladezeit_s=600.0,
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=batteriekapazitaet_kwh,
        )
        assert ergebnis == [pytest.approx(gestrecktes_soc), 95.0]

        # Das gestreckte Ziel dauert tatsaechlich (rund) die Mindestdauer.
        gestreckte_ladezeit_s = charging_math.calc_ladezeit_s(
            start_soc_pct=70.0,
            end_soc_pct=ergebnis[0],
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=batteriekapazitaet_kwh,
        )
        assert gestreckte_ladezeit_s == pytest.approx(600.0, abs=1.0)

    def test_deaktivierte_mindestladedauer_laesst_kandidaten_unveraendert(self) -> None:
        """`mindest_ladezeit_s=0` (deaktiviert) darf Kandidaten nicht verändern."""
        create_networkx_optimizer()
        ladekurve = LadekurveReferenz.model_3_lr_v3()
        kandidaten = [71.0, 72.0, 95.0]

        ergebnis = charging_math.kandidaten_mit_mindestladedauer(
            kandidaten=kandidaten,
            ankunft_soc_pct=70.0,
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=75.0,
            mindest_ladezeit_s=0.0,
        )

        assert ergebnis == kandidaten

    def test_end_to_end_verhindert_zu_kurzen_ladehalt(self) -> None:
        """End-to-end (gleiches Szenario wie
        `TestGraphKonstruktionFindetDijkstraOptimum`): mit deaktivierter
        Mindestladedauer waehlt die Optimierung einen 376s-Kurzhalt an
        station-4. Mit der Produktions-Default-Mindestladedauer (600s) MUSS
        dieser Kurzhalt verschwinden - JEDER verbleibende Ladehalt dauert
        entweder gar nicht (uebersprungen) oder mindestens 600s."""
        szenario = TestGraphKonstruktionFindetDijkstraOptimum()
        route, gradients, energy_results, stations, vehicle_profile = (
            szenario._sechs_segmente_szenario()
        )
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=20)
        abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)

        def _optimiere(mindest_ladezeit_s: int) -> list[int]:
            constraints = OptimizationConstraints(
                min_soc_pct=10.0, ziel_soc_pct=10.0, mindest_ladezeit_s=mindest_ladezeit_s
            )
            plan = optimizer.optimize(
                route=route,
                segments=route.segments,
                gradients=gradients,
                energy_results=energy_results,
                charging_stations=stations,
                waypoints=[],
                vehicle_profile=vehicle_profile,
                constraints=constraints,
                start_soc_pct=100.0,
                abfahrtszeit=abfahrtszeit,
            )
            return [s.geschaetzte_ladedauer_s for s in plan.ladehalte]

        ohne_mindestdauer = _optimiere(0)
        assert min(ohne_mindestdauer) < 600  # Bestaetigt die Kurzhalt-Praemisse

        mit_mindestdauer = _optimiere(600)
        # `ChargingStop.geschaetzte_ladedauer_s` truncated per `int()` von
        # `_extract_charging_stops` (kein Runden) - bis zu 1s unter der
        # exakten Mindestdauer ist daher normal, kein Bug.
        assert all(dauer_s >= 599 for dauer_s in mit_mindestdauer)


class TestDetourKostenNutztRealeRoutingDatenWennVorhanden:
    """Tests for `_detour_kosten`'s real-vs-heuristic fallback logic (see
    `optimization.detour_routing`)."""

    def test_nutzt_reale_kosten_wenn_station_in_map(self) -> None:
        real_kosten = DetourKosten(distanz_m=2000.0, zeit_s=180.0, energie_kwh=0.4)

        zeit_s, _soc_pct = detour_costs.detour_kosten(
            station_id="real-station",
            offroute_distance_m=999_999.0,  # would give a wildly different heuristic result
            vehicle_profile=VehicleProfile(
                masse_kg=1800.0,
                cw_wert=0.23,
                stirnflaeche_m2=2.2,
                rollwiderstandsbeiwert=0.01,
                batteriekapazitaet_kwh=60.0,
                nebenverbraucher_baseline_kw=0.34,
                reifentyp="standard",
                dachbox=False,
            ),
            detour_kosten={"real-station": real_kosten},
            avg_verbrauch_kwh_pro_m=0.0,
        )

        assert zeit_s == 180.0

    def test_faellt_auf_heuristik_zurueck_wenn_station_fehlt(self) -> None:
        vehicle_profile = VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        )

        zeit_s, _ = detour_costs.detour_kosten(
            station_id="missing-station",
            offroute_distance_m=1000.0,
            vehicle_profile=vehicle_profile,
            detour_kosten={
                "other-station": DetourKosten(distanz_m=1.0, zeit_s=1.0, energie_kwh=0.0)
            },
            avg_verbrauch_kwh_pro_m=0.0002,  # set as optimize() normally would
        )

        # Heuristic: 1000m * 1.6 / (70 km/h) = ~82.3s
        assert zeit_s == pytest.approx(82.3, abs=0.5)

    def test_faellt_auf_heuristik_zurueck_wenn_detour_kosten_none(self) -> None:
        vehicle_profile = VehicleProfile(
            masse_kg=1800.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.2,
            rollwiderstandsbeiwert=0.01,
            batteriekapazitaet_kwh=60.0,
            nebenverbraucher_baseline_kw=0.34,
            reifentyp="standard",
            dachbox=False,
        )

        zeit_s, _ = detour_costs.detour_kosten(
            station_id="any-station",
            offroute_distance_m=1000.0,
            vehicle_profile=vehicle_profile,
            detour_kosten=None,
            avg_verbrauch_kwh_pro_m=0.0002,
        )

        assert zeit_s == pytest.approx(82.3, abs=0.5)


class TestMaxChargeSocCapsRegularStops:
    """Tests for `OptimizationConstraints.max_lade_soc_pct`: the global
    cap on the target SoC of regular charging stops (Supercharger stations).
    Default 100.0 = disabled (behavior unchanged).

    Deliberately excluded:
    - `ladedauer_vorgaben` (explicit user decision, analogous to the
      unbounded `max_ladezeit_s`, see `_add_charging_edges`)
    - charging at waypoints (`Waypoint.ladeleistung_kw`)
    """

    def _optimiere(self, max_lade_soc_pct: float | None) -> list[ChargingStop]:
        """Runs the proven six-segment scenario with the given cap.

        Scenario premise (verified against the uncapped run): the natural
        plan charges at station-3 (arr=18.7, target=30.0), station-4
        (arr=9.1, target=33.2) and station-5 (arr=5.0, target=38.3) - so a
        cap of 30% must remove the uncapped plan while keeping a feasible,
        cheaper alternative (charging earlier at station-2).
        """
        szenario = TestGraphKonstruktionFindetDijkstraOptimum()
        route, gradients, energy_results, stations, vehicle_profile = (
            szenario._sechs_segmente_szenario()
        )
        constraints_kwargs: dict[str, float | int] = {
            "min_soc_pct": 10.0,
            "ziel_soc_pct": 10.0,
            "mindest_ladezeit_s": 0,
        }
        if max_lade_soc_pct is not None:
            constraints_kwargs["max_lade_soc_pct"] = max_lade_soc_pct
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=20)
        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=stations,
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=OptimizationConstraints(**constraints_kwargs),
            start_soc_pct=100.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )
        return list(plan.ladehalte)

    def test_without_cap_behavior_is_unchanged(self) -> None:
        """Default (no cap): the natural plan from the scenario premise."""
        halte = self._optimiere(None)
        assert [(s.station.station_id, round(s.ziel_soc_pct, 1)) for s in halte] == [
            ("station-3", 30.0),
            ("station-4", 33.2),
            ("station-5", 38.3),
        ]

    def test_cap_is_a_hard_constraint(self) -> None:
        """The cap is hard: a cap below the physically required charging
        target (station-5 must reach 38.3% to clear the final leg with the
        5% destination floor) leaves no feasible plan, so the optimizer
        reports the trip as undrivable."""
        with pytest.raises(ValueError, match="Route nicht fahrbar"):
            self._optimiere(35.0)

    def test_cap_at_or_above_physical_minimum_leaves_plan_unchanged(self) -> None:
        """A cap at/above the natural charging target does not alter the
        plan (38.3% < 38.4% cap): the natural plan is already minimal."""
        halte = self._optimiere(38.4)
        assert [(s.station.station_id, round(s.ziel_soc_pct, 1)) for s in halte] == [
            ("station-3", 30.0),
            ("station-4", 33.2),
            ("station-5", 38.3),
        ]

    def test_cap_does_not_affect_charging_at_waypoints(self) -> None:
        """`max_lade_soc_pct` affects ONLY regular charging stops:
        charging at a waypoint (`ladeleistung_kw` during a forced wait) is
        unaffected - it keeps charging to its (uncapped) target SoC."""
        szenario = TestZwischenstoppErzwingtWartezeit()
        route, energy_results, vehicle_profile = szenario._basis_szenario()
        constraints = OptimizationConstraints(
            min_soc_pct=15.0,
            ziel_soc_pct=35.0,
            max_lade_soc_pct=30.0,
            mindest_ladezeit_s=0,
        )
        optimizer = create_networkx_optimizer()
        abfahrtszeit = datetime(2026, 8, 30, 6, 15, 0)
        wp = Waypoint(
            koordinate=(52.5, 12.5),
            geplante_abfahrt=abfahrtszeit + timedelta(hours=2),
            ladeleistung_kw=11.0,
        )

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[],
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[wp],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=50.0,
            abfahrtszeit=abfahrtszeit,
        )

        aufenthalte = plan.zwischenstopp_aufenthalte
        assert len(aufenthalte) == 1
        # 2h of wallbox charging raises the SoC to ~64% - well above the
        # 30% cap that applies to regular stops only.
        assert aufenthalte[0].ladeleistung_kw == 11.0
        assert aufenthalte[0].ziel_soc_pct > 60.0

    def test_fixed_charge_duration_override_is_not_capped(self) -> None:
        """An explicitly fixed charge duration (`ladedauer_vorgaben`) is NOT
        capped by `max_lade_soc_pct` - consistent with the existing
        convention for `max_ladezeit_s` (see `_add_charging_edges`): an
        explicit user decision beats the global limit.

        With a 1h duration fixed on every station, the optimizer picks a
        single stop at station-3 (arr=18.7, charges to 100%) - far above the
        30% cap, which must therefore NOT apply here.
        """
        szenario = TestGraphKonstruktionFindetDijkstraOptimum()
        route, gradients, energy_results, stations, vehicle_profile = (
            szenario._sechs_segmente_szenario()
        )
        constraints = OptimizationConstraints(
            min_soc_pct=10.0,
            ziel_soc_pct=10.0,
            max_lade_soc_pct=30.0,
            mindest_ladezeit_s=0,
        )
        optimizer = create_networkx_optimizer(soc_step_pct=5.0, time_step_min=20)
        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=gradients,
            energy_results=energy_results,
            charging_stations=stations,
            waypoints=[],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=100.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            ladedauer_vorgaben={f"station-{i}": 3600 for i in range(1, 6)},
        )

        assert plan.ladehalte
        assert all(s.ziel_soc_pct > 60.0 for s in plan.ladehalte)


class TestMaxChargeSocStretchClamping:
    """`_kandidaten_mit_mindestladedauer` stretches candidates whose charge
    time would fall below the minimum duration - the result MUST stay
    clamped to `max_lade_soc_pct` (the charge limit is hard, the minimum
    duration only soft)."""

    def test_stretched_target_is_clamped_to_cap(self) -> None:
        ladekurve = LadekurveReferenz.model_3_sr()
        # 70% from 20% (60 kWh) takes well under 2400s, so the candidate is
        # stretched to the SoC after 2400s (~100%) - with a 70% cap the
        # clamped target must be exactly the cap.
        ergebnis = charging_math.kandidaten_mit_mindestladedauer(
            kandidaten=[70.0],
            ankunft_soc_pct=20.0,
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=60.0,
            mindest_ladezeit_s=2400.0,
            max_lade_soc_pct=70.0,
        )
        assert ergebnis == [70.0]

    def test_without_cap_stretch_is_unchanged(self) -> None:
        """Without a cap (default) the stretched target stays at ~100%
        (regression: default behavior unchanged)."""
        ladekurve = LadekurveReferenz.model_3_sr()
        ergebnis = charging_math.kandidaten_mit_mindestladedauer(
            kandidaten=[70.0],
            ankunft_soc_pct=20.0,
            ladekurve=ladekurve,
            batteriekapazitaet_kwh=60.0,
            mindest_ladezeit_s=2400.0,
        )
        assert ergebnis == [100.0]


class TestZielUndZwischenstoppFloorNutztAnkunftsMinimum:
    """Regressionstest: Bug - ein niedriges, vom Nutzer gesetztes Ziel-SoC
    (z. B. 5%) wurde von `min_soc_pct` (Sicherheitsreserve fuer offene
    Strecke, nie vom Frontend/API gesetzt, Default 15%) ueberschrieben, UND
    die Fahrtkante zu einem ladefaehigen Zwischenstopp (`Waypoint.
    ladeleistung_kw` + erzwungene Wartezeit) verlangte faelschlich densel-
    ben Sicherheitsreserve-Floor statt des niedrigeren
    `mindest_ankunfts_soc_pct` (siehe Nutzer-Report: Route Gummersbach ->
    Taberg (ladender Zwischenstopp) -> Hagfors, Ziel-SoC 5% gesetzt, aber
    Ankunft am Zwischenstopp mit 21% statt ~5% und am Ziel mit 27% statt
    ~5% - beides Symptome desselben Floor-Bugs). Beide Konstellationen
    machten die Route zuvor sogar komplett unfahrbar, sobald die
    kontinuierliche Ankunfts-SoC zwischen `mindest_ankunfts_soc_pct` (5%)
    und `min_soc_pct` (15%) lag.
    """

    def _szenario(
        self,
    ) -> tuple[Route, list[SegmentEnergyResult], VehicleProfile]:
        berlin = (52.5, 13.4)
        wegpunkt = (52.5, 12.5)
        hamburg = (53.55, 9.99)
        route = Route(
            segments=[
                RouteSegment(
                    segment_index=0,
                    geometrie=[berlin, wegpunkt],
                    laenge_m=50_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=130,
                    steigung_rohdaten=0.0,
                    bearing_deg=310.0,
                ),
                RouteSegment(
                    segment_index=1,
                    geometrie=[wegpunkt, hamburg],
                    laenge_m=50_000,
                    strassenklasse="MOTORWAY",
                    tempolimit_kmh=130,
                    steigung_rohdaten=0.0,
                    bearing_deg=290.0,
                ),
            ],
            gesamtlaenge_m=100_000,
            geometrie=[berlin, wegpunkt, hamburg],
        )
        # Batteriekapazitaet 60 kWh: Segment 0 verbraucht 53 kWh (88.3% ->
        # Ankunfts-SoC 11.7% am Zwischenstopp, ZWISCHEN `mindest_ankunfts_
        # soc_pct`=5% und `min_soc_pct`=15%), Segment 1 (nach vollem
        # Nachladen am Zwischenstopp) verbraucht 57 kWh (95% -> Ankunft am
        # Ziel exakt bei 5%, dem gesetzten `ziel_soc_pct`).
        energy_results = [
            SegmentEnergyResult(
                segment_index=0,
                energiebedarf_kwh=53.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=53.0,
                geschwindigkeit_m_s=30.0,
                fahrzeit_s=1667,
                streckenlaenge_m=50_000,
            ),
            SegmentEnergyResult(
                segment_index=1,
                energiebedarf_kwh=57.0,
                rekuperation_kwh=0.0,
                energiebedarf_brutto_kwh=57.0,
                geschwindigkeit_m_s=30.0,
                fahrzeit_s=1667,
                streckenlaenge_m=50_000,
            ),
        ]
        vehicle_profile = VehicleProfile(
            masse_kg=1706.0,
            cw_wert=0.23,
            stirnflaeche_m2=2.22,
            rollwiderstandsbeiwert=0.011,
            batteriekapazitaet_kwh=60.0,
        )
        return route, energy_results, vehicle_profile

    def test_niedriges_ankunfts_und_ziel_soc_macht_route_nicht_unfahrbar(self) -> None:
        """`min_soc_pct` bleibt auf Produktions-Default (15%, wird von
        Frontend/API nie gesetzt) - die Route MUSS trotzdem planbar sein,
        weil sowohl der ladefaehige Zwischenstopp als auch das (bewusst
        niedrig gesetzte) Fahrtziel `mindest_ankunfts_soc_pct`/`ziel_soc_
        target` statt des allgemeinen Sicherheitsreserve-Floors nutzen
        muessen."""
        route, energy_results, vehicle_profile = self._szenario()
        constraints = OptimizationConstraints(
            ziel_soc_pct=5.0, sicherheitsreserve_pct=0.0, mindest_ladezeit_s=0
        )
        optimizer = create_networkx_optimizer()
        abfahrtszeit = datetime(2026, 8, 30, 6, 30, 0, tzinfo=UTC)
        geplante_abfahrt = datetime(2026, 8, 31, 6, 30, 0, tzinfo=UTC)
        wp = Waypoint(
            koordinate=(52.5, 12.5), geplante_abfahrt=geplante_abfahrt, ladeleistung_kw=11.0
        )

        plan = optimizer.optimize(
            route=route,
            segments=route.segments,
            gradients=[],
            energy_results=energy_results,
            charging_stations=[],
            waypoints=[wp],
            vehicle_profile=vehicle_profile,
            constraints=constraints,
            start_soc_pct=100.0,
            abfahrtszeit=abfahrtszeit,
        )

        assert len(plan.zwischenstopp_aufenthalte) == 1
        aufenthalt = plan.zwischenstopp_aufenthalte[0]
        # Ankunft am Zwischenstopp bei ~11.7% - NICHT durch das allgemeine
        # `min_soc_pct` (15%) auf einen unerreichbaren Floor angehoben bzw.
        # als unzulaessige Kante verworfen.
        assert aufenthalt.ankunfts_soc_pct == pytest.approx(11.7, abs=0.1)
        # Volles Nachladen am Zwischenstopp bleibt uneingeschraenkt (analog
        # zu `TestMaxChargeSocCapsRegularStops.test_cap_does_not_affect_
        # charging_at_waypoints`).
        assert aufenthalt.ziel_soc_pct == pytest.approx(100.0, abs=0.1)
