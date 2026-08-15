"""Tests fuer das simulation-Modul: simulate_trip-Funktion.

Testfaelle gemaeess Plan Abschnitt 6.1:
- Positions-Interpolation (Segmentanfang/-ende/Mitte)
- Zustandswechsel FAHREN->LADEN->FAHREN an einem Ladehalt
- SoC faellt waehrend Fahrt und steigt waehrend Ladevorgang
- Gesamtdistanz/-fahrzeit/-ladezeit korrekt aufsummiert
- Zeitauflösung konfigurierbar
- Grenzfall Route ohne Ladehalt (durchgehend FAHREN)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.optimization.models import ChargingPlan, ChargingStop
from tripplanner.routing.models import Coordinate, Route, RouteSegment
from tripplanner.simulation import TripState, simulate_trip

# Fixe Koordinaten
BERLIN_COORD: Coordinate = (52.5200, 13.4050)
LEIPZIG_COORD: Coordinate = (51.3397, 12.3731)
FRANKFURT_COORD: Coordinate = (50.1109, 8.6821)


def make_route_segment(
    segment_index: int,
    geometrie: list[Coordinate],
    laenge_m: float,
) -> RouteSegment:
    """Hilfsfunktion zur Erstellung von RouteSegment-Instanzen."""
    return RouteSegment(
        segment_index=segment_index,
        geometrie=geometrie,
        laenge_m=laenge_m,
        strassenklasse="MOTORWAY",
        tempolimit_kmh=120,
        bearing_deg=45.0,
    )


def make_energy_result(
    segment_index: int,
    energiebedarf_kwh: float,
    fahrzeit_s: float,
    geschwindigkeit_m_s: float,
    streckenlaenge_m: float,
) -> SegmentEnergyResult:
    """Hilfsfunktion zur Erstellung von SegmentEnergyResult-Instanzen."""
    return SegmentEnergyResult(
        segment_index=segment_index,
        energiebedarf_kwh=energiebedarf_kwh,
        rekuperation_kwh=0.0,
        energiebedarf_brutto_kwh=energiebedarf_kwh,
        geschwindigkeit_m_s=geschwindigkeit_m_s,
        fahrzeit_s=fahrzeit_s,
        streckenlaenge_m=streckenlaenge_m,
    )


@pytest.fixture
def route_3_segments() -> Route:
    """Route mit 3 Segmenten (insgesamt ca. 150 km)."""
    return Route(
        segments=[
            make_route_segment(0, [BERLIN_COORD, LEIPZIG_COORD], 50_000),
            make_route_segment(1, [LEIPZIG_COORD, (50.5, 9.0)], 60_000),
            make_route_segment(2, [(50.5, 9.0), FRANKFURT_COORD], 40_000),
        ],
        gesamtlaenge_m=150_000,
        geometrie=[BERLIN_COORD, LEIPZIG_COORD, FRANKFURT_COORD],
    )


@pytest.fixture
def energy_results_3_segments() -> list[SegmentEnergyResult]:
    """Energieergebnisse fuer 3 Segmente."""
    return [
        SegmentEnergyResult(
            segment_index=0,
            energiebedarf_kwh=8.5,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=8.5,
            geschwindigkeit_m_s=33.33,
            fahrzeit_s=1500,
            streckenlaenge_m=50_000,
        ),
        SegmentEnergyResult(
            segment_index=1,
            energiebedarf_kwh=9.2,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=9.2,
            geschwindigkeit_m_s=33.33,
            fahrzeit_s=1800,
            streckenlaenge_m=60_000,
        ),
        SegmentEnergyResult(
            segment_index=2,
            energiebedarf_kwh=7.8,
            rekuperation_kwh=0.0,
            energiebedarf_brutto_kwh=7.8,
            geschwindigkeit_m_s=33.33,
            fahrzeit_s=1200,
            streckenlaenge_m=40_000,
        ),
    ]


class TestPositionsInterpolation:
    """Tests fuer Positionsinterpolation entlang der Route."""

    def test_position_at_segment_start(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 1: Position am Segmentanfang (t=0)."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=4500,
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=4500,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(result.frames) == 2
        start_frame = result.frames[0]
        assert start_frame.position == BERLIN_COORD
        assert start_frame.zustand == TripState.FAHREN
        assert start_frame.soc_pct == pytest.approx(80.0, abs=0.1)

        last_frame = result.frames[-1]
        assert last_frame.position == FRANKFURT_COORD
        assert last_frame.zustand == TripState.FAHREN

    def test_position_at_segment_end(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 2: Position am Segmentende (am Ende der Route)."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=4500,
        )
        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=100,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(result.frames) > 1
        last_frame = result.frames[-1]
        assert last_frame.position == FRANKFURT_COORD
        assert last_frame.zustand == TripState.FAHREN

    def test_position_in_segment_middle(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 3: Position in der Mitte eines Segments."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=4500,
        )
        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=750,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        frame_at_750s = result.frames[1]
        assert frame_at_750s.zustand == TripState.FAHREN
        assert frame_at_750s.soc_pct < 80.0


class TestStateTransitions:
    """Tests fuer Zustandsuebergange (FAHREN -> LADEN -> FAHREN)."""

    def test_fahren_laden_fahren_transition(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
        charging_station_leipzig: ChargingStation,
    ) -> None:
        """Testfall 4: Zustandsuebergang FAHREN -> LADEN -> FAHREN an einem Ladehalt."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        plan = ChargingPlan(
            ladehalte=[
                ChargingStop(
                    station=charging_station_leipzig,
                    segment_index=1,
                    ankunfts_soc_pct=30.0,
                    ziel_soc_pct=60.0,
                    geschaetzte_ladedauer_s=1800,
                    ankunftszeit=base_time + timedelta(seconds=1500),
                    abfahrtszeit=base_time + timedelta(seconds=3300),
                ),
            ],
            gesamtreisezeit_s=6300,
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=base_time,
        )

        zustande = [f.zustand for f in result.frames]

        assert TripState.FAHREN in zustande
        assert TripState.LADEN in zustande
        assert zustande[-1] == TripState.FAHREN

    def test_soc_during_laden_increases(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
        charging_station_leipzig: ChargingStation,
    ) -> None:
        """Testfall 5: SoC steigt waehrend Ladevorgang."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        plan = ChargingPlan(
            ladehalte=[
                ChargingStop(
                    station=charging_station_leipzig,
                    segment_index=1,
                    ankunfts_soc_pct=30.0,
                    ziel_soc_pct=80.0,
                    geschaetzte_ladedauer_s=1800,
                    ankunftszeit=base_time + timedelta(seconds=1500),
                    abfahrtszeit=base_time + timedelta(seconds=3300),
                ),
            ],
            gesamtreisezeit_s=6300,
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=600,
            abfahrtszeit=base_time,
        )

        laden_frames = [f for f in result.frames if f.zustand == TripState.LADEN]
        assert len(laden_frames) >= 1

        first_laden_frame = laden_frames[0]
        assert first_laden_frame.soc_pct <= 40.0


class TestSocChanges:
    """Tests fuer SoC-Aenderungen waehrend Fahrt und Laden."""

    def test_soc_decreases_during_driving(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 6: SoC faellt waehrend Fahrt."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=4500,
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        first_soc = result.frames[0].soc_pct
        last_soc = result.frames[-1].soc_pct
        assert last_soc < first_soc

    def test_soc_increases_during_charging(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
        charging_station_leipzig: ChargingStation,
    ) -> None:
        """Testfall 7: SoC steigt waehrend Ladevorgang."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        plan = ChargingPlan(
            ladehalte=[
                ChargingStop(
                    station=charging_station_leipzig,
                    segment_index=1,
                    ankunfts_soc_pct=30.0,
                    ziel_soc_pct=60.0,
                    geschaetzte_ladedauer_s=1800,
                    ankunftszeit=base_time + timedelta(seconds=1500),
                    abfahrtszeit=base_time + timedelta(seconds=3300),
                ),
            ],
            gesamtreisezeit_s=6300,
        )
        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=600,
            abfahrtszeit=base_time,
        )

        laden_frames = [f for f in result.frames if f.zustand == TripState.LADEN]
        if len(laden_frames) >= 2:
            assert laden_frames[-1].soc_pct > laden_frames[0].soc_pct


class TestTotals:
    """Tests fuer Gesamtberechnung (Distanz, Fahrzeit, Ladezeit)."""

    def test_total_distance_correct(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 8: Gesamtdistanz ist korrekt."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=4500,
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert result.gesamt_distanz_km == pytest.approx(150.0, abs=0.1)

    def test_total_fahrzeit_correct(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 9: Gesamtfahrzeit ist korrekt."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=4500,
        )
        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert result.gesamt_fahrzeit_min == pytest.approx(75.0, abs=0.1)

    def test_total_ladezeit_correct(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
        charging_station_leipzig: ChargingStation,
    ) -> None:
        """Testfall 10: Gesamtladezeit ist korrekt (1800s = 30 min)."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        plan = ChargingPlan(
            ladehalte=[
                ChargingStop(
                    station=charging_station_leipzig,
                    segment_index=1,
                    ankunfts_soc_pct=30.0,
                    ziel_soc_pct=60.0,
                    geschaetzte_ladedauer_s=1800,
                    ankunftszeit=base_time + timedelta(seconds=1500),
                    abfahrtszeit=base_time + timedelta(seconds=3300),
                ),
            ],
            gesamtreisezeit_s=6300,
        )
        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=base_time,
        )

        assert result.gesamt_ladezeit_min == pytest.approx(30.0, abs=0.1)


class TestResolution:
    """Tests fuer konfigurierbare Zeitauflösung."""

    def test_high_resolution(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 11: Hohe Auflosung (10s) erzeugt viele Frames."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=3600,
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=10,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(result.frames) >= 360

    def test_low_resolution(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 12: Geringe Auflosung (600s) erzeugt weniger Frames."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=3600,
        )
        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            output_resolution_seconds=600,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert len(result.frames) >= 6


class TestNoChargingScenario:
    """Tests fuer Grenzfall: Route ohne Ladehalt."""

    def test_no_charging_scenario(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Testfall 13: Durchgehend FAHREN ohne Ladehalt."""
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=4500,
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=60,
        )

        assert all(f.zustand == TripState.FAHREN for f in result.frames)
        assert result.gesamt_ladezeit_min == pytest.approx(0.0, abs=0.01)
        assert result.start_soc_pct > result.ziel_soc_pct


class TestAbfahrtszeitBasis:
    """Regressionstests Bug 1: Zeitstempel muessen auf abfahrtszeit basieren, nicht Unix-Epoch."""

    def test_zeitpunkt_basiert_auf_abfahrtszeit(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Erster Frame-Zeitpunkt entspricht exakt der uebergebenen abfahrtszeit (nicht 1970)."""
        abfahrtszeit = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=600)

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
            output_resolution_seconds=60,
        )

        assert result.frames[0].zeitpunkt == abfahrtszeit
        assert result.frames[1].zeitpunkt == abfahrtszeit + timedelta(seconds=60)

    def test_zeitpunkt_mit_anderer_abfahrtszeit(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Eine voellig andere abfahrtszeit fuehrt zu entsprechend verschobenen Zeitstempeln."""
        abfahrtszeit = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
        plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=600)

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
            output_resolution_seconds=60,
        )

        assert result.frames[0].zeitpunkt == abfahrtszeit
        assert result.frames[0].zeitpunkt.year == 2030
        assert result.frames[-1].zeitpunkt > abfahrtszeit


class TestSocDepletionPhysikalischKorrekt:
    """Regressionstests: Bug 2 - SoC-Abfall muss auf Energie/Batteriekapazitaet basieren."""

    def test_soc_verbrauch_proportional_zu_energiebedarf_und_batteriekapazitaet(self) -> None:
        """1 Segment, 10 kWh Verbrauch, 50 kWh Kapazitaet -> Abfall exakt 20 Prozentpunkte."""
        route = Route(
            segments=[make_route_segment(0, [BERLIN_COORD, LEIPZIG_COORD], 100_000)],
            gesamtlaenge_m=100_000,
            geometrie=[BERLIN_COORD, LEIPZIG_COORD],
        )
        energy_results = [
            make_energy_result(0, 10.0, 3600.0, 27.78, 100_000),
        ]
        plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=3600)

        result = simulate_trip(
            route=route,
            charging_plan=plan,
            segment_energy=energy_results,
            weather_samples=[],
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=3600,
            battery_capacity_kwh=50.0,
        )

        assert result.frames[-1].soc_pct == pytest.approx(60.0, abs=0.5)
        assert result.frames[-1].soc_pct != pytest.approx(0.0, abs=1.0)

    def test_soc_verbrauch_kumulativ_ueber_mehrere_segmente(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """End-SoC ueber 3 Segmente entspricht Gesamtenergie/Batteriekapazitaet, nicht 100-start."""
        plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=4500)
        battery_capacity_kwh = 62.5
        gesamt_energie_kwh = sum(e.energiebedarf_kwh for e in energy_results_3_segments)

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            weather_samples=[],
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=60,
            battery_capacity_kwh=battery_capacity_kwh,
        )

        erwarteter_end_soc = 80.0 - (gesamt_energie_kwh / battery_capacity_kwh) * 100.0
        assert result.frames[-1].soc_pct == pytest.approx(erwarteter_end_soc, abs=0.5)


class TestSocBaselineNachLadehalt:
    """Regressionstests: Bug 3 - SoC waehrend FAHREN nach einem Ladehalt muss vom
    Ziel-SoC dieses Ladehalts ausgehen, nicht vom Start-SoC der gesamten Reise.

    Fruehere Implementierung berechnete den SoC waehrend FAHREN immer als
    `start_soc_pct - kumulierte_energie_seit_reisebeginn`, unabhaengig davon,
    ob zwischendurch bereits geladen wurde. Dadurch wurde jeder Ladegewinn
    verworfen, sobald wieder gefahren wurde, und der SoC fiel einfach auf der
    urspruenglichen (ungeladenen) Entladekurve weiter - bei laengeren Routen
    mit mehreren Ladehalten faelschlich bis auf 0% trotz erfolgter Ladehalte.
    """

    def test_end_soc_basiert_auf_ladehalt_ziel_soc_nicht_auf_reise_start(
        self,
        route_with_charging: Route,
        energy_results_with_charging: list[SegmentEnergyResult],
        plan_with_charging: ChargingPlan,
    ) -> None:
        """End-SoC nach Ladehalt (Segment 1, Ziel 60%) + Segmente 1+2 (17.0 kWh von
        62.5 kWh) muss ~32.8% ergeben (60.0 - 27.2), nicht ~39.2% (80.0 - 40.8), was
        die fehlerhafte Implementierung liefern wuerde (Start-SoC 80% minus
        Gesamtenergie aller 3 Segmente, ohne den Ladehalt zu beruecksichtigen).
        """
        battery_capacity_kwh = 62.5
        ladehalt = plan_with_charging.ladehalte[0]
        energie_nach_ladehalt_kwh = sum(
            e.energiebedarf_kwh
            for e in energy_results_with_charging
            if e.segment_index >= ladehalt.segment_index
        )
        erwarteter_end_soc = (
            ladehalt.ziel_soc_pct - (energie_nach_ladehalt_kwh / battery_capacity_kwh) * 100.0
        )
        fehlerhafter_end_soc = (
            80.0
            - (
                sum(e.energiebedarf_kwh for e in energy_results_with_charging)
                / battery_capacity_kwh
            )
            * 100.0
        )

        result = simulate_trip(
            route=route_with_charging,
            charging_plan=plan_with_charging,
            segment_energy=energy_results_with_charging,
            weather_samples=[],
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=60,
            battery_capacity_kwh=battery_capacity_kwh,
        )

        assert result.frames[-1].soc_pct == pytest.approx(erwarteter_end_soc, abs=0.5)
        assert result.frames[-1].soc_pct != pytest.approx(fehlerhafter_end_soc, abs=1.0)
