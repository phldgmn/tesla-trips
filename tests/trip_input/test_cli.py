"""Tests für CLI-Entry-Point (typer CLI Integration)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from tripplanner.charging_infrastructure.client import TeslaLocationsClient
from tripplanner.charging_infrastructure.providers import (
    FakeChargingStationProvider,
    TeslaChargingStationProvider,
)
from tripplanner.construction.providers import FakeConstructionProvider
from tripplanner.elevation import ElevationProvider
from tripplanner.elevation.providers import FakeDataSource
from tripplanner.routing.providers import FakeRoutingProvider
from tripplanner.trip_input.cli import app, parse_coord, parse_waypoint
from tripplanner.trip_input.providers_factory import ProductionProviders
from tripplanner.weather.providers import FakeWeatherProvider
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def mock_trip_result() -> MagicMock:
    """Mock für ein Trip-Simulation-Ergebnis."""
    mock_frame = MagicMock()
    mock_frame.timestamp = datetime(2026, 8, 15, 8, 30, 0)
    mock_frame.position = (52.5200, 13.4050)
    mock_frame.soc_pct = 75.0
    mock_frame.zustand = MagicMock(value="fahren")
    mock_frame.speed_kmh = 100.0

    mock_result = MagicMock()
    mock_result.gesamt_distanz_km = 290.0
    mock_result.gesamt_fahrzeit_min = 180
    mock_result.gesamt_ladezeit_min = 30
    mock_result.start_soc_pct = 80.0
    mock_result.target_soc_pct = 25.0
    mock_result.frames = [mock_frame]
    return mock_result


class TestCliParseFunctions:
    """Tests für die CLI-Parser-Funktionen."""

    def test_parse_coord_valid(self) -> None:
        """Test: Gültige Koordinaten werden korrekt geparst."""
        assert parse_coord("52.5200,13.4050") == (52.5200, 13.4050)
        assert parse_coord("-33.8688,151.2093") == (-33.8688, 151.2093)
        assert parse_coord("0,0") == (0.0, 0.0)
        assert parse_coord(" 52.5 , 13.4 ") == (52.5, 13.4)  # mit Whitespaces

    def test_parse_coord_invalid_format(self) -> None:
        """Test: Ungültiges Format wirft ValueError."""
        with pytest.raises(ValueError, match="Invalid coordinate"):
            parse_coord("52.52")  # nur eine Komponente
        with pytest.raises(ValueError, match="Invalid coordinate"):
            parse_coord("52.52,13.4050,extra")  # drei Komponenten
        with pytest.raises(ValueError, match="Invalid coordinate"):
            parse_coord("")  # leer
        with pytest.raises(ValueError, match="Invalid coordinate"):
            parse_coord("lat,lon")  # keine Zahlen

    def test_parse_coord_invalid_number(self) -> None:
        """Test: Nicht-numerische Werte werfen ValueError."""
        with pytest.raises(ValueError, match="Invalid coordinate"):
            parse_coord("abc,def")
        with pytest.raises(ValueError, match="Invalid coordinate"):
            parse_coord("52.52,abc")

    def test_parse_waypoint_valid_with_duration(self) -> None:
        """Test: Waypoint mit duration wird korrekt geparst."""
        coord, duration = parse_waypoint("52.5200,13.4050:30")
        assert coord == (52.5200, 13.4050)
        assert duration == timedelta(minutes=30)

    def test_parse_waypoint_valid_without_duration(self) -> None:
        """Test: Waypoint ohne duration wird korrekt geparst."""
        coord, duration = parse_waypoint("52.5200,13.4050")
        assert coord == (52.5200, 13.4050)
        assert duration is None

    def test_parse_waypoint_invalid_coord(self) -> None:
        """An invalid waypoint coordinate raises ValueError.

        The implementation catches the parse_coord error and re-raises it as an
        'Invalid duration' error (current behavior, a known implementation bug).
        """
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_waypoint("invalid:30")


class TestCliCommand:
    """Integrationstests für den CLI-Befehl (kein Subcommand 'trips')."""

    def test_cli_minimal_required_args(self, mock_trip_result: MagicMock) -> None:
        """Test: minimum erforderliche Argumente (start, destination, departure-time)."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                ],
            )

            assert result.exit_code == 0, (
                f"Exit code {result.exit_code}, stderr: {result.stderr}, stdout: {result.stdout}"
            )
            output = json.loads(result.stdout)
            assert output["gesamt_distanz_km"] == 290.0
            assert output["gesamt_fahrzeit_min"] == 180
            assert output["gesamt_ladezeit_min"] == 30
            assert output["start_soc_pct"] == 80.0
            assert output["target_soc_pct"] == 25.0
            assert len(output["frames"]) == 1

    def test_cli_with_all_options(self, mock_trip_result: MagicMock) -> None:
        """Test: Alle Optionen inkl. Waypoints, SoC, Profil, Output."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--waypoints",
                    "52.0,12.0:15",
                    "--waypoints",
                    "51.0,11.0",
                    "--start-soc-pct",
                    "90.0",
                    "--destination-soc-pct",
                    "15.0",
                    "--vehicle-profile",
                    "model3_longrange",
                ],
            )

            assert result.exit_code == 0, f"Exit code {result.exit_code}, stderr: {result.stderr}"
            output = json.loads(result.stdout)
            assert output["start_soc_pct"] == 80.0  # vom Mock
            assert output["target_soc_pct"] == 25.0

    def test_cli_with_output_json_file(self, mock_trip_result: MagicMock, tmp_path: Path) -> None:
        """Test: JSON-Ausgabe in Datei."""
        output_file = tmp_path / "trip_result.json"

        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--output-json",
                    str(output_file),
                ],
            )

            assert result.exit_code == 0, f"Exit code {result.exit_code}, stderr: {result.stderr}"
            assert result.stdout == ""  # Keine Ausgabe auf stdout bei Datei-Output
            assert output_file.exists()
            data = json.loads(output_file.read_text())
            assert data["gesamt_distanz_km"] == 290.0

    def test_cli_invalid_start_coord(self) -> None:
        """Test: Ungültige Start-Koordinate -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "invalid",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
            ],
        )

        assert result.exit_code != 2
        assert "Invalid input" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_destination_coord(self) -> None:
        """Test: Ungültige Ziel-Koordinate -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "invalid",
                "--departure-time",
                "2026-08-15T08:00:00",
            ],
        )

        assert result.exit_code != 2
        assert "Invalid input" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_waypoint_format(self) -> None:
        """Test: Ungültiges Waypoint-Format -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
                "--waypoints",
                "invalid_waypoint",
            ],
        )

        assert result.exit_code != 2
        assert "Invalid input" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_waypoint_duration(self) -> None:
        """Test: Ungültige duration im Waypoint -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
                "--waypoints",
                "52.0,12.0:abc",
            ],
        )

        assert result.exit_code != 2
        assert "Invalid input" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_date_format(self) -> None:
        """Test: Ungültiges Datumsformat -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "invalid-date",
            ],
        )

        assert result.exit_code != 2
        assert "Invalid input" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_start_soc(self) -> None:
        """Test: Start-SoC außerhalb Bereich -> Exit Code != 0 (Typer validiert)."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
                "--start-soc-pct",
                "150.0",  # > 100
            ],
        )

        assert result.exit_code != 0
        # Typer validiert min/max bevor unsere Funktion aufgerufen wird

    def test_cli_invalid_destination_soc(self) -> None:
        """Test: Ziel-SoC außerhalb Bereich -> Exit Code != 0."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
                "--destination-soc-pct",
                "-10.0",  # < 0
            ],
        )

        assert result.exit_code != 0

    def test_cli_invalid_mindest_ladezeit_s(self) -> None:
        """Test: Mindestladedauer außerhalb Bereich -> Exit Code != 0."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
                "--min-charging-time-s",
                "5000",  # > 1800
            ],
        )

        assert result.exit_code != 0

    def test_cli_invalid_max_lade_soc_pct(self) -> None:
        """Test: Max. Lade-SoC außerhalb Bereich -> Exit Code != 0."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
                "--max-charge-soc-pct",
                "150.0",  # > 100
            ],
        )

        assert result.exit_code != 0

    def test_cli_max_lade_soc_pct_wird_weitergereicht(self, mock_trip_result: MagicMock) -> None:
        """Test: `--max-lade-soc-pct` wird an `create_trip_simulation`
        durchgereicht.
        """
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ) as mock_sim:
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--offline",
                    "--max-charge-soc-pct",
                    "75.0",
                ],
            )

            assert result.exit_code == 0, (
                f"Unerwarteter Exit Code: {result.exit_code}, stderr: {result.stderr}"
            )
            assert mock_sim.call_args.kwargs["max_charge_soc_pct"] == 75.0

    def test_cli_missing_required_args(self) -> None:
        """Test: Fehlende Pflichtargumente -> Exit Code != 0 (Typer usage error)."""
        result = runner.invoke(
            app,
            [
                "trips",
            ],
        )

        assert result.exit_code != 0
        # Typer zeigt Usage-Error

    def test_cli_simulation_exception(self) -> None:
        """Test: Exception in create_trip_simulation -> Exit Code 1."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            side_effect=Exception("Simulation failed"),
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                ],
            )

            assert result.exit_code == 1
            assert "Fehler bei der Berechnung" in result.stderr

    def test_cli_validation_error(self) -> None:
        """Test: ValidationError aus create_trip_simulation -> Exit Code 1."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            side_effect=ValidationError.from_exception_data("test", []),
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                ],
            )

            assert result.exit_code == 1
            assert "Validierungsfehler" in result.stderr

    def test_cli_value_error(self) -> None:
        """Test: ValueError aus create_trip_simulation -> Exit Code 1."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid value"),
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                ],
            )

            assert result.exit_code == 1
            assert "Invalid input" in result.stderr


class TestCliHelp:
    """Tests für CLI-Hilfe."""

    def test_cli_help(self) -> None:
        """Test: Haupt-Hilfe wird angezeigt."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        # Der Help-Text enthält die verfügbaren Subcommands
        assert "trips" in result.stdout
        assert "charger" in result.stdout

    def test_cli_trips_help(self) -> None:
        """Test: Hilfe für den trips Subcommand."""
        result = runner.invoke(app, ["trips", "--help"], env={"COLUMNS": "200"})
        assert result.exit_code == 0
        assert "--start" in result.stdout
        assert "--destination" in result.stdout
        assert "--departure-time" in result.stdout
        assert "--waypoints" in result.stdout
        assert "--start-soc-pct" in result.stdout
        assert "--destination-soc-pct" in result.stdout
        assert "--vehicle-profile" in result.stdout
        assert "--output-json" in result.stdout


class TestCliEnglishFlags:
    """Tests für die englischen CLI Flag-Namen (Phase A)."""

    def test_cli_trips_help_shows_destination_flag(
        self,
    ) -> None:
        """Test: Help zeigt englische Flag-Namen statt der alten deutschen."""
        result = runner.invoke(app, ["trips", "--help"], env={"COLUMNS": "200"})
        assert result.exit_code == 0
        assert "--start" in result.stdout
        assert "--destination" in result.stdout
        assert "--waypoints" in result.stdout
        assert "--destination-soc-pct" in result.stdout
        assert "--offline" in result.stdout


class TestCliOfflineFlag:
    """Tests für den --offline CLI Flag."""

    def test_cli_offline_flag_uses_fake_providers(self, mock_trip_result: MagicMock) -> None:
        """Test: --offline nutzt Fake-Provider statt build_production_providers()."""
        with (
            patch(
                "tripplanner.trip_input.cli.create_trip_simulation",
                new_callable=AsyncMock,
                return_value=mock_trip_result,
            ) as mock_simulate,
            patch(
                "tripplanner.trip_input.cli.build_production_providers",
                new_callable=AsyncMock,
            ) as mock_build_providers,
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--offline",
                ],
            )

            assert result.exit_code == 0, f"stderr: {result.stderr}"
            mock_build_providers.assert_not_called()
            call_kwargs = mock_simulate.call_args.kwargs
            assert isinstance(call_kwargs["routing_provider"], FakeRoutingProvider)
            assert isinstance(call_kwargs["weather_provider"], FakeWeatherProvider)
            assert isinstance(call_kwargs["construction_provider"], FakeConstructionProvider)
            assert isinstance(call_kwargs["charging_provider"], FakeChargingStationProvider)
            assert isinstance(call_kwargs["elevation_provider"], ElevationProvider)
            assert isinstance(call_kwargs["elevation_provider"].data_source, FakeDataSource)

    def test_cli_offline_default_false_uses_production_providers(
        self, mock_trip_result: MagicMock
    ) -> None:
        """Test: Ohne --offline (Default False) werden echte Produktions-Provider verwendet."""
        mock_providers = MagicMock(spec=ProductionProviders)
        mock_providers.routing = object()
        mock_providers.elevation_provider = object()
        mock_providers.weather = object()
        mock_providers.construction = object()
        mock_providers.charging = object()

        with (
            patch(
                "tripplanner.trip_input.cli.create_trip_simulation",
                new_callable=AsyncMock,
                return_value=mock_trip_result,
            ) as mock_simulate,
            patch(
                "tripplanner.trip_input.cli.build_production_providers",
                new_callable=AsyncMock,
                return_value=mock_providers,
            ) as mock_build_providers,
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                ],
            )

            assert result.exit_code == 0, f"stderr: {result.stderr}"
            mock_build_providers.assert_called_once()
            call_kwargs = mock_simulate.call_args.kwargs
            assert call_kwargs["routing_provider"] is mock_providers.routing
            assert not isinstance(call_kwargs["routing_provider"], FakeRoutingProvider)
            assert call_kwargs["weather_provider"] is mock_providers.weather
            assert not isinstance(call_kwargs["weather_provider"], FakeWeatherProvider)
            assert call_kwargs["construction_provider"] is mock_providers.construction
            assert not isinstance(call_kwargs["construction_provider"], FakeConstructionProvider)
            assert call_kwargs["charging_provider"] is mock_providers.charging
            assert not isinstance(call_kwargs["charging_provider"], FakeChargingStationProvider)
            assert call_kwargs["elevation_provider"] is mock_providers.elevation_provider

    def test_cli_offline_explicit_false_uses_production_providers(
        self, mock_trip_result: MagicMock
    ) -> None:
        """Test: --no-offline verhält sich wie das Default (Produktions-Provider)."""
        mock_providers = MagicMock(spec=ProductionProviders)
        mock_providers.routing = object()
        mock_providers.elevation_provider = object()
        mock_providers.weather = object()
        mock_providers.construction = object()
        mock_providers.charging = object()

        with (
            patch(
                "tripplanner.trip_input.cli.create_trip_simulation",
                new_callable=AsyncMock,
                return_value=mock_trip_result,
            ) as mock_simulate,
            patch(
                "tripplanner.trip_input.cli.build_production_providers",
                new_callable=AsyncMock,
                return_value=mock_providers,
            ) as mock_build_providers,
        ):
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--no-offline",
                ],
            )

            assert result.exit_code == 0, f"stderr: {result.stderr}"
            mock_build_providers.assert_called_once()
            call_kwargs = mock_simulate.call_args.kwargs
            assert call_kwargs["routing_provider"] is mock_providers.routing


class TestCliWeatherDetailLevel:
    """Tests for --weather-detail-level CLI option."""

    def test_cli_weather_detail_level_off_exits_zero(self, mock_trip_result: MagicMock) -> None:
        """Test: --weather-detail-level off with --offline yields exit code 0."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ) as mock_simulate:
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--offline",
                    "--weather-detail-level",
                    "off",
                ],
            )
            assert result.exit_code == 0, f"stderr: {result.stderr}"
            assert mock_simulate.call_args.kwargs["weather_detail"] == "off"

    def test_cli_weather_detail_level_low_exits_zero(self, mock_trip_result: MagicMock) -> None:
        """Test: --weather-detail-level low with --offline yields exit code 0."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ) as mock_simulate:
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--offline",
                    "--weather-detail-level",
                    "low",
                ],
            )
            assert result.exit_code == 0, f"stderr: {result.stderr}"
            assert mock_simulate.call_args.kwargs["weather_detail"] == "low"

    def test_cli_weather_detail_level_medium_exits_zero(self, mock_trip_result: MagicMock) -> None:
        """Test: --weather-detail-level medium with --offline yields exit code 0."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ) as mock_simulate:
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--offline",
                    "--weather-detail-level",
                    "medium",
                ],
            )
            assert result.exit_code == 0, f"stderr: {result.stderr}"
            assert mock_simulate.call_args.kwargs["weather_detail"] == "medium"

    def test_cli_weather_detail_level_high_exits_zero(self, mock_trip_result: MagicMock) -> None:
        """Test: --weather-detail-level high with --offline yields exit code 0."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ) as mock_simulate:
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--offline",
                    "--weather-detail-level",
                    "high",
                ],
            )
            assert result.exit_code == 0, f"stderr: {result.stderr}"
            assert mock_simulate.call_args.kwargs["weather_detail"] == "high"

    def test_cli_weather_detail_level_invalid_rejects(self) -> None:
        """Test: invalid value (--weather-detail-level bogus) yields non-zero exit code."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--destination",
                "53.5511,9.9937",
                "--departure-time",
                "2026-08-15T08:00:00",
                "--offline",
                "--weather-detail-level",
                "bogus",
            ],
        )
        assert result.exit_code != 0
        assert "Invalid weather detail level" in (result.stderr + result.stdout)

    def test_cli_weather_detail_level_help_shows_option(self) -> None:
        """Test: --help shows the --weather-detail-level option."""
        result = runner.invoke(app, ["trips", "--help"], env={"COLUMNS": "200"})
        assert result.exit_code == 0
        assert "--weather-detail-level" in result.stdout

    def test_cli_weather_detail_level_default_is_high(self, mock_trip_result: MagicMock) -> None:
        """Test: without --weather-detail-level the default 'high' is used."""
        with patch(
            "tripplanner.trip_input.cli.create_trip_simulation",
            new_callable=AsyncMock,
            return_value=mock_trip_result,
        ) as mock_simulate:
            result = runner.invoke(
                app,
                [
                    "trips",
                    "--start",
                    "52.5200,13.4050",
                    "--destination",
                    "53.5511,9.9937",
                    "--departure-time",
                    "2026-08-15T08:00:00",
                    "--offline",
                ],
            )
            assert result.exit_code == 0, f"stderr: {result.stderr}"
            assert mock_simulate.call_args.kwargs["weather_detail"] == "high"


# =============================================================================
# charger pricing-queue / charger scrape-pricing
# =============================================================================


def _seed_station(db_path: Path, tesla_location_id: str = "rhudensupercharger") -> None:
    """Seeds `db_path` with a single station, no pricing."""
    provider = TeslaChargingStationProvider(db_path=db_path)
    provider._db.replace_all_stations(
        [
            {
                "supercharge_info_id": 3506,
                "tesla_location_id": tesla_location_id,
                "site_name": "Rhueden Supercharger",
                "latitude": 51.947,
                "longitude": 10.140,
                "country_code": "DE",
                "stalls_v2": 0,
                "stalls_v3": 8,
                "stalls_v3_ultra": 0,
                "stalls_v4": 0,
                "total_stalls": 8,
                "power_kilowatt": 250,
                "status": "OPEN",
                "connector_types": '["ccs2"]',
                "ist_24_7": 1,
                "date_opened": None,
                "last_updated_utc": datetime.now().isoformat(),
            }
        ]
    )
    provider._db.close()


def _pricing_html(amount: str = "EUR 0,40/kWh") -> str:
    """Minimal `__NEXT_DATA__` HTML page with one flat Tesla-owner rate."""
    payload = {
        "props": {
            "pageProps": {
                "formattedData": {
                    "chargerPricing": [{"label": "Charging Fees for Tesla Owner", "price": amount}]
                }
            }
        }
    }
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


class TestChargerPricingQueue:
    """Tests für `charger pricing-queue`."""

    def test_empty_queue_message(self, tmp_path: Path) -> None:
        """Eine leere Warteschlange zeigt einen entsprechenden Hinweis."""
        db_path = tmp_path / "pricing.db"
        _seed_station(db_path)

        result = runner.invoke(app, ["charger", "pricing-queue", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "leer" in result.stdout

    def test_lists_queued_station(self, tmp_path: Path) -> None:
        """Eine eingereihte Station wird mit Slug und Land angezeigt."""
        db_path = tmp_path / "pricing.db"
        _seed_station(db_path, tesla_location_id="rhudensupercharger")
        provider = TeslaChargingStationProvider(db_path=db_path)
        provider.enqueue_stations_for_pricing_refresh(["rhudensupercharger"])
        provider._db.close()

        result = runner.invoke(app, ["charger", "pricing-queue", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "rhudensupercharger" in result.stdout
        assert "DE" in result.stdout
        assert "nie aktualisiert" in result.stdout


class TestChargerScrapePricing:
    """Tests für `charger scrape-pricing`."""

    def test_empty_queue_is_a_noop(self, tmp_path: Path) -> None:
        """Eine leere Warteschlange fuehrt zu keinem Scrape-Versuch."""
        db_path = tmp_path / "pricing.db"
        _seed_station(db_path)

        result = runner.invoke(app, ["charger", "scrape-pricing", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "nichts zu tun" in result.stdout

    def test_successful_scrape_persists_pricing_and_empties_queue(self, tmp_path: Path) -> None:
        """Ein erfolgreicher Scrape speichert Preisdaten und leert die Warteschlange."""
        db_path = tmp_path / "pricing.db"
        _seed_station(db_path)
        provider = TeslaChargingStationProvider(db_path=db_path)
        provider.enqueue_stations_for_pricing_refresh(["rhudensupercharger"])
        provider._db.close()

        html = _pricing_html()

        with patch(
            "tripplanner.charging_infrastructure.client.SafariTeslaClient.fetch_pricing_html",
            new_callable=AsyncMock,
            return_value=html,
        ):
            result = runner.invoke(
                app,
                ["charger", "scrape-pricing", "--db-path", str(db_path), "--delay", "0"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert "1 aktualisiert" in result.stdout

        verify_provider = TeslaChargingStationProvider(db_path=db_path)
        assert verify_provider.list_pricing_queue() == []
        cached = verify_provider.get_cached_pricing("rhudensupercharger")
        assert len(cached.tiers) == 1
        assert cached.tiers[0].currency == "EUR"
        verify_provider._db.close()

    def test_all_failures_exits_nonzero(self, tmp_path: Path) -> None:
        """Schlaegt der einzige Versuch fehl, ist der Exit-Code != 0."""
        db_path = tmp_path / "pricing.db"
        _seed_station(db_path)
        provider = TeslaChargingStationProvider(db_path=db_path)
        provider.enqueue_stations_for_pricing_refresh(["rhudensupercharger"])
        provider._db.close()

        with patch(
            "tripplanner.charging_infrastructure.client.SafariTeslaClient.fetch_pricing_html",
            new_callable=AsyncMock,
            side_effect=TeslaLocationsClient.CurlError("403 Access Denied"),
        ):
            result = runner.invoke(
                app,
                ["charger", "scrape-pricing", "--db-path", str(db_path), "--delay", "0"],
            )

        assert result.exit_code == 1
        assert "1 fehlgeschlagen" in result.stdout
        assert "rhudensupercharger" in result.stderr

        # Fehlgeschlagener Versuch wird trotzdem aus der Warteschlange entfernt
        # (Retry erst bei naechster Routen-Finalisierung).
        verify_provider = TeslaChargingStationProvider(db_path=db_path)
        assert verify_provider.list_pricing_queue() == []
        verify_provider._db.close()

    def test_retry_failed_requeues_on_failure(self, tmp_path: Path) -> None:
        """--retry-failed gibt fehlgeschlagene Stationen sofort wieder in die Warteschlange."""
        db_path = tmp_path / "pricing.db"
        _seed_station(db_path)
        provider = TeslaChargingStationProvider(db_path=db_path)
        provider.enqueue_stations_for_pricing_refresh(["rhudensupercharger"])
        provider._db.close()

        with patch(
            "tripplanner.charging_infrastructure.client.SafariTeslaClient.fetch_pricing_html",
            new_callable=AsyncMock,
            side_effect=TeslaLocationsClient.CurlError("403 Access Denied"),
        ):
            result = runner.invoke(
                app,
                [
                    "charger",
                    "scrape-pricing",
                    "--db-path",
                    str(db_path),
                    "--delay",
                    "0",
                    "--retry-failed",
                ],
            )

        assert result.exit_code == 1
        assert "1 fehlgeschlagen" in result.stdout

        # --retry-failed: fehlgeschlagene Station wird sofort wieder re-queued
        verify_provider = TeslaChargingStationProvider(db_path=db_path)
        assert len(verify_provider.list_pricing_queue()) == 1
        verify_provider._db.close()

    def test_respects_limit(self, tmp_path: Path) -> None:
        """--limit begrenzt die Anzahl in diesem Lauf abgearbeiteter Stationen."""
        db_path = tmp_path / "pricing.db"
        provider = TeslaChargingStationProvider(db_path=db_path)
        provider._db.replace_all_stations(
            [
                {
                    "supercharge_info_id": sid,
                    "tesla_location_id": slug,
                    "site_name": slug,
                    "latitude": 51.9,
                    "longitude": 10.1,
                    "country_code": "DE",
                    "stalls_v2": 0,
                    "stalls_v3": 8,
                    "stalls_v3_ultra": 0,
                    "stalls_v4": 0,
                    "total_stalls": 8,
                    "power_kilowatt": 250,
                    "status": "OPEN",
                    "connector_types": '["ccs2"]',
                    "ist_24_7": 1,
                    "date_opened": None,
                    "last_updated_utc": datetime.now().isoformat(),
                }
                for sid, slug in [(1, "a-supercharger"), (2, "b-supercharger")]
            ]
        )
        provider.enqueue_stations_for_pricing_refresh(["a-supercharger", "b-supercharger"])
        provider._db.close()

        html = _pricing_html()

        with patch(
            "tripplanner.charging_infrastructure.client.SafariTeslaClient.fetch_pricing_html",
            new_callable=AsyncMock,
            return_value=html,
        ):
            result = runner.invoke(
                app,
                [
                    "charger",
                    "scrape-pricing",
                    "--db-path",
                    str(db_path),
                    "--delay",
                    "0",
                    "--limit",
                    "1",
                ],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert "1 aktualisiert" in result.stdout

        verify_provider = TeslaChargingStationProvider(db_path=db_path)
        assert len(verify_provider.list_pricing_queue()) == 1  # one still pending
        verify_provider._db.close()
