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
from tripplanner.optimization.models import ChargingPlan, ChargingStop, ZwischenstoppAufenthalt
from tripplanner.routing.models import Coordinate, Route, RouteSegment
from tripplanner.simulation import TripState, simulate_trip
from tripplanner.weather.models import WeatherSample

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
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert result.gesamt_distanz_km == pytest.approx(150.0, abs=0.1)

    def test_total_driving_time_correct(
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
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert result.gesamt_fahrzeit_min == pytest.approx(75.0, abs=0.1)

    def test_total_charge_time_correct(
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
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=60,
        )

        assert all(f.zustand == TripState.FAHREN for f in result.frames)
        assert result.gesamt_ladezeit_min == pytest.approx(0.0, abs=0.01)
        assert result.start_soc_pct > result.ziel_soc_pct


class TestDepartureTimeBasis:
    """Regressionstests Bug 1: Zeitstempel muessen auf abfahrtszeit basieren, nicht Unix-Epoch."""

    def test_timepoint_based_on_departuretime(
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
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
            output_resolution_seconds=60,
        )

        assert result.frames[0].zeitpunkt == abfahrtszeit
        assert result.frames[1].zeitpunkt == abfahrtszeit + timedelta(seconds=60)

    def test_timepoint_with_other_departuretime(
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
            start_soc_pct=80.0,
            abfahrtszeit=abfahrtszeit,
            output_resolution_seconds=60,
        )

        assert result.frames[0].zeitpunkt == abfahrtszeit
        assert result.frames[0].zeitpunkt.year == 2030
        assert result.frames[-1].zeitpunkt > abfahrtszeit


class TestSocDepletionPhysikalischKorrekt:
    """Regressionstests: Bug 2 - SoC-Abfall muss auf Energie/Batteriekapazitaet basieren."""

    def test_soc_consumption_proportional_to_energy_usage_and_battery_capacity(self) -> None:
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
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=3600,
            battery_capacity_kwh=50.0,
        )

        assert result.frames[-1].soc_pct == pytest.approx(60.0, abs=0.5)
        assert result.frames[-1].soc_pct != pytest.approx(0.0, abs=1.0)

    def test_soc_consumption_cumulative_across_multiple_segments(
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
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=60,
            battery_capacity_kwh=battery_capacity_kwh,
        )

        erwarteter_end_soc = 80.0 - (gesamt_energie_kwh / battery_capacity_kwh) * 100.0
        assert result.frames[-1].soc_pct == pytest.approx(erwarteter_end_soc, abs=0.5)


class TestSocBaselineAfterChargingStop:
    """Regressionstests: Bug 3 - SoC waehrend FAHREN nach einem Ladehalt muss vom
    Ziel-SoC dieses Ladehalts ausgehen, nicht vom Start-SoC der gesamten Reise.

    Fruehere Implementierung berechnete den SoC waehrend FAHREN immer als
    `start_soc_pct - kumulierte_energie_seit_reisebeginn`, unabhaengig davon,
    ob zwischendurch bereits geladen wurde. Dadurch wurde jeder Ladegewinn
    verworfen, sobald wieder gefahren wurde, und der SoC fiel einfach auf der
    urspruenglichen (ungeladenen) Entladekurve weiter - bei laengeren Routen
    mit mehreren Ladehalten faelschlich bis auf 0% trotz erfolgter Ladehalte.
    """

    def test_end_soc_based_on_charging_stop_end_soc_not_travel_start(
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
        energy_after_charging_stop_kwh = sum(
            e.energiebedarf_kwh
            for e in energy_results_with_charging
            if e.segment_index >= ladehalt.segment_index
        )
        erwarteter_end_soc = (
            ladehalt.ziel_soc_pct - (energy_after_charging_stop_kwh / battery_capacity_kwh) * 100.0
        )
        wrong_end_soc = (
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
            start_soc_pct=80.0,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            output_resolution_seconds=60,
            battery_capacity_kwh=battery_capacity_kwh,
        )

        assert result.frames[-1].soc_pct == pytest.approx(erwarteter_end_soc, abs=0.5)
        assert result.frames[-1].soc_pct != pytest.approx(wrong_end_soc, abs=1.0)


class TestZwischenstoppAufenthalt:
    """Regressionstests: eine erzwungene Zwischenstopp-Wartezeit
    (`ChargingPlan.zwischenstopp_aufenthalte`) muss als stationaere Phase
    simuliert werden (Fahrzeug steht an der Zwischenstopp-Koordinate) statt
    als zusaetzliche, ueber die gesamte Route verschmierte Fahrzeit - sonst
    "kriecht" das Fahrzeug waehrend der Wartezeit langsam entlang der Route
    weiter, statt an der tatsaechlichen Stopp-Position stehen zu bleiben.
    """

    def test_wartezeit_ohne_ladeleistung_ergibt_pause_frame_an_stopp_koordinate(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Ein Zwischenstopp-Aufenthalt ohne Ladeleistung erzeugt PAUSE-Frames
        exakt an dessen Koordinate, mit unveraendertem SoC."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        stopp_koordinate = (51.0, 12.0)
        aufenthalt = ZwischenstoppAufenthalt(
            koordinate=stopp_koordinate,
            segment_index=1,
            ankunftszeit=base_time + timedelta(seconds=1500),
            abfahrtszeit=base_time + timedelta(seconds=3300),
            ladeleistung_kw=None,
            ankunfts_soc_pct=55.0,
            ziel_soc_pct=55.0,
        )
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=6300,
            zwischenstopp_aufenthalte=[aufenthalt],
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=base_time,
        )

        pause_frames = [f for f in result.frames if f.zustand == TripState.PAUSE]
        assert len(pause_frames) >= 1
        for frame in pause_frames:
            assert frame.position == stopp_koordinate
            assert frame.geschwindigkeit_kmh == 0.0
            assert frame.soc_pct == pytest.approx(55.0, abs=0.5)
        assert result.gesamt_wartezeit_min == pytest.approx(30.0, abs=0.1)
        assert result.waypoint_stops[0].position == stopp_koordinate
        assert result.waypoint_stops[0].ladeleistung_kw is None

    def test_wartezeit_mit_ladeleistung_ergibt_laden_frame_mit_steigendem_soc(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Ein Zwischenstopp-Aufenthalt MIT Ladeleistung erzeugt LADEN-Frames
        mit zwischen Ankunfts-/Ziel-SoC interpoliertem SoC."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        stopp_koordinate = (51.0, 12.0)
        aufenthalt = ZwischenstoppAufenthalt(
            koordinate=stopp_koordinate,
            segment_index=1,
            ankunftszeit=base_time + timedelta(seconds=1500),
            abfahrtszeit=base_time + timedelta(seconds=3300),
            ladeleistung_kw=11.0,
            ankunfts_soc_pct=40.0,
            ziel_soc_pct=60.0,
        )
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=6300,
            zwischenstopp_aufenthalte=[aufenthalt],
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=base_time,
        )

        laden_frames = [f for f in result.frames if f.zustand == TripState.LADEN]
        assert len(laden_frames) >= 1
        for frame in laden_frames:
            assert frame.position == stopp_koordinate
        assert laden_frames[0].soc_pct <= 45.0
        assert laden_frames[-1].soc_pct > laden_frames[0].soc_pct
        # Zwischenstopp-Aufenthalt zaehlt trotz Ladeleistung als WARTEzeit,
        # nicht als Ladezeit - nur tatsaechliche Ladestopps gehen in
        # `gesamt_ladezeit_min` ein.
        assert result.gesamt_wartezeit_min == pytest.approx(30.0, abs=0.1)
        assert result.gesamt_ladezeit_min == pytest.approx(0.0, abs=0.1)
        assert result.waypoint_stops[0].ladeleistung_kw == 11.0
        assert result.waypoint_stops[0].energie_geladen_kwh > 0.0

    def test_soc_nach_wartezeit_bleibt_baseline_fuer_folgefahrt(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Nach einer Zwischenstopp-Ladung muss der SoC waehrend der
        anschliessenden Fahrt vom dort erreichten Ziel-SoC ausgehen, nicht vom
        Start-SoC der gesamten Reise (analog zu Ladehalten an Superchargern,
        siehe `TestSocBaselineAfterChargingStop`)."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        aufenthalt = ZwischenstoppAufenthalt(
            koordinate=(51.0, 12.0),
            segment_index=1,
            ankunftszeit=base_time + timedelta(seconds=1500),
            abfahrtszeit=base_time + timedelta(seconds=3300),
            ladeleistung_kw=11.0,
            ankunfts_soc_pct=30.0,
            ziel_soc_pct=70.0,
        )
        plan = ChargingPlan(
            ladehalte=[],
            gesamtreisezeit_s=6300,
            zwischenstopp_aufenthalte=[aufenthalt],
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=base_time,
        )

        fahren_frames_nach_aufenthalt = [
            f
            for f in result.frames
            if f.zustand == TripState.FAHREN and f.zeitpunkt > aufenthalt.abfahrtszeit
        ]
        assert fahren_frames_nach_aufenthalt
        # Erster FAHREN-Frame nach der Ladung darf nicht weit unter 70% liegen
        # (waere er von 80% Start-SoC ausgegangen, laege er deutlich tiefer).
        assert fahren_frames_nach_aufenthalt[0].soc_pct > 60.0

    def test_distanz_m_ist_geometrisch_exakt_trotz_zeitskalen_drift(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """`WaypointStopSummary.distanz_m` MUSS exakt der kumulierten Distanz
        am `segment_index` des Zwischenstopps entsprechen - unabhaengig von
        der zeitbasierten Positionsrekonstruktion (`_find_segment_for_time`),
        die bei einer globalen `time_scale != 1` (z. B. wenn `gesamtreisezeit_s`
        stark von der Summe der rohen Segment-Fahrzeiten abweicht, wie es bei
        GraphHopper-Faehrsegmenten mit unrealistisch kurzer Roh-Fahrzeit, aber
        langer echter Ueberfahrtsdauer vorkommt) systematisch danebenliegt.

        `segment_index=2` liegt bei exakt 110.000 m (50.000 + 60.000 m, Ende
        von Segment 1 = Beginn von Segment 2). Die rohen Segment-Fahrzeiten
        summieren sich auf 4500s; `gesamtreisezeit_s` wird hier bewusst auf
        das Doppelte der reinen Fahrzeit gesetzt (`time_scale = 2.0`) - die
        zeitbasierte Rekonstruktion wuerde den Zwischenstopp dann faelschlich
        bei 55.000 m verorten (Segment 1 statt Segment 2, siehe Testkommentare
        unten), 55 km vom tatsaechlichen Zwischenstopp entfernt."""
        base_time = datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)
        aufenthalt = ZwischenstoppAufenthalt(
            koordinate=(50.5, 9.0),
            segment_index=2,
            ankunftszeit=base_time + timedelta(seconds=3300),
            abfahrtszeit=base_time + timedelta(seconds=3900),
            ladeleistung_kw=11.0,
            ankunfts_soc_pct=30.0,
            ziel_soc_pct=70.0,
        )
        plan = ChargingPlan(
            ladehalte=[],
            # total_driving_time_s = 9600 - 600 = 9000s = 2x der rohen
            # Segment-Fahrzeitsumme (4500s) -> time_scale = 2.0.
            gesamtreisezeit_s=9600,
            zwischenstopp_aufenthalte=[aufenthalt],
        )

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=base_time,
        )

        assert result.waypoint_stops[0].distanz_m == pytest.approx(110_000.0)


class TestWetterWirdAnFramesAngehaengt:
    """`simulate_trip(weather_samples=...)` haengt Temperatur/Wind/Niederschlag
    an jeden Frame an - fuer den Routen-Hover-Tooltip im Frontend (siehe
    `SimulationFrame.temperatur_c` und `buildRouteHoverText` in `popups.ts`)."""

    @staticmethod
    def _make_weather_sample(
        koordinate: Coordinate,
        temperatur_c: float,
        windgeschwindigkeit_ms: float = 3.0,
        windrichtung_deg: float = 270.0,
        niederschlag_mm: float = 0.0,
    ) -> WeatherSample:
        return WeatherSample(
            koordinate=koordinate,
            zeitpunkt=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            temperatur_c=temperatur_c,
            windgeschwindigkeit_ms=windgeschwindigkeit_ms,
            windrichtung_deg=windrichtung_deg,
            niederschlag_mm=niederschlag_mm,
            schneefall_cm=0.0,
            luftdruck_hpa=1013.25,
            luftfeuchtigkeit_pct=60.0,
            globalstrahlung_wm2=400.0,
            bewoelkung_pct=20.0,
        )

    def test_frames_tragen_segmentweise_wetterwerte(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Jeder Frame erhaelt Temperatur/Wind/Niederschlag des `WeatherSample`
        seines aktuellen Segments (per Index, gleiche Reihenfolge wie
        `route.segments`)."""
        plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=4500)
        weather_samples = [
            self._make_weather_sample(BERLIN_COORD, temperatur_c=5.0, niederschlag_mm=2.0),
            self._make_weather_sample(LEIPZIG_COORD, temperatur_c=10.0),
            self._make_weather_sample(FRANKFURT_COORD, temperatur_c=15.0),
        ]

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
            weather_samples=weather_samples,
        )

        fahren_frames = [f for f in result.frames if f.zustand == TripState.FAHREN]
        assert fahren_frames
        for frame in fahren_frames:
            assert frame.temperatur_c is not None
            assert frame.windgeschwindigkeit_ms is not None
            assert frame.windrichtung_deg is not None
            assert frame.niederschlag_mm is not None
        # Erster Frame liegt auf Segment 0 -> dessen Wetter (5°C, 2mm Regen).
        assert fahren_frames[0].temperatur_c == pytest.approx(5.0)
        assert fahren_frames[0].niederschlag_mm == pytest.approx(2.0)
        # Letzter Frame liegt auf Segment 2 -> dessen Wetter (15°C).
        assert fahren_frames[-1].temperatur_c == pytest.approx(15.0)
        assert fahren_frames[0].windrichtung_deg == pytest.approx(270.0)

    def test_ohne_weather_samples_bleiben_wetterfelder_none(
        self,
        route_3_segments: Route,
        energy_results_3_segments: list[SegmentEnergyResult],
    ) -> None:
        """Ohne `weather_samples` (Standard) bleiben die Wetterfelder `None`
        statt irrefuehrende Platzhalterwerte zu tragen - z. B. wenn der
        Nutzer die Wetterberuecksichtigung deaktiviert hat (siehe
        `WeatherDetailLevel` 'off', verdrahtet in
        `tripplanner.trip_input.pipeline.create_trip_simulation`)."""
        plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=4500)

        result = simulate_trip(
            route=route_3_segments,
            charging_plan=plan,
            segment_energy=energy_results_3_segments,
            start_soc_pct=80.0,
            output_resolution_seconds=60,
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC),
        )

        assert result.frames
        for frame in result.frames:
            assert frame.temperatur_c is None
            assert frame.windgeschwindigkeit_ms is None
            assert frame.windrichtung_deg is None
            assert frame.niederschlag_mm is None
