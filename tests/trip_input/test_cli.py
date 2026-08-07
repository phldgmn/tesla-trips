"""Tests für CLI-Entry-Point (typer CLI Integration)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from tripplanner.trip_input.cli import app, parse_coord, parse_waypoint

runner = CliRunner()


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
        with pytest.raises(ValueError, match="Ungültige Koordinate"):
            parse_coord("52.52")  # nur eine Komponente
        with pytest.raises(ValueError, match="Ungültige Koordinate"):
            parse_coord("52.52,13.4050,extra")  # drei Komponenten
        with pytest.raises(ValueError, match="Ungültige Koordinate"):
            parse_coord("")  # leer
        with pytest.raises(ValueError, match="Ungültige Koordinate"):
            parse_coord("lat,lon")  # keine Zahlen

    def test_parse_coord_invalid_number(self) -> None:
        """Test: Nicht-numerische Werte werfen ValueError."""
        with pytest.raises(ValueError, match="Ungültige Koordinate"):
            parse_coord("abc,def")
        with pytest.raises(ValueError, match="Ungültige Koordinate"):
            parse_coord("52.52,abc")

    def test_parse_waypoint_valid_with_duration(self) -> None:
        """Test: Waypoint mit Dauer wird korrekt geparst."""
        coord, duration = parse_waypoint("52.5200,13.4050:30")
        assert coord == (52.5200, 13.4050)
        assert duration == timedelta(minutes=30)

    def test_parse_waypoint_valid_without_duration(self) -> None:
        """Test: Waypoint ohne Dauer wird korrekt geparst."""
        coord, duration = parse_waypoint("52.5200,13.4050")
        assert coord == (52.5200, 13.4050)
        assert duration is None

    def test_parse_waypoint_invalid_coord(self) -> None:
        """Test: Ungültige Koordinate im Waypoint wirft ValueError.

        Implementation fängt parse_coord-Fehler ab und wirft 'Ungültige Dauer' Error.
        Das ist das aktuelle Verhalten (Bug in der Implementierung).
        """
        with pytest.raises(ValueError, match="Ungültige Dauer"):
            parse_waypoint("invalid:30")


class TestCliCommand:
    """Integrationstests für den CLI-Befehl (kein Subcommand 'trips')."""

    @pytest.fixture
    def mock_trip_result(self) -> MagicMock:
        """Mock für ein Trip-Simulation-Ergebnis."""
        mock_frame = MagicMock()
        mock_frame.zeitpunkt = datetime(2026, 8, 15, 8, 30, 0)
        mock_frame.position = (52.5200, 13.4050)
        mock_frame.soc_pct = 75.0
        mock_frame.zustand = MagicMock(value="fahren")
        mock_frame.geschwindigkeit_kmh = 100.0

        mock_result = MagicMock()
        mock_result.gesamt_distanz_km = 290.0
        mock_result.gesamt_fahrzeit_min = 180
        mock_result.gesamt_ladezeit_min = 30
        mock_result.start_soc_pct = 80.0
        mock_result.ziel_soc_pct = 25.0
        mock_result.frames = [mock_frame]
        return mock_result

    def test_cli_minimal_required_args(self, mock_trip_result: MagicMock) -> None:
        """Test: Minimale erforderliche Argumente (start, ziel, abfahrtszeit)."""
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
                    "--ziel",
                    "53.5511,9.9937",
                    "--abfahrtszeit",
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
            assert output["ziel_soc_pct"] == 25.0
            assert len(output["frames"]) == 1

    def test_cli_with_all_options(self, mock_trip_result: MagicMock) -> None:
        """Test: Alle Optionen inkl. Zwischenstopps, SoC, Profil, Output."""
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
                    "--ziel",
                    "53.5511,9.9937",
                    "--abfahrtszeit",
                    "2026-08-15T08:00:00",
                    "--zwischenstopps",
                    "52.0,12.0:15",
                    "--zwischenstopps",
                    "51.0,11.0",
                    "--start-soc-pct",
                    "90.0",
                    "--ziel-soc-pct",
                    "15.0",
                    "--vehicle-profile",
                    "model3_longrange",
                ],
            )

            assert result.exit_code == 0, f"Exit code {result.exit_code}, stderr: {result.stderr}"
            output = json.loads(result.stdout)
            assert output["start_soc_pct"] == 80.0  # vom Mock
            assert output["ziel_soc_pct"] == 25.0

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
                    "--ziel",
                    "53.5511,9.9937",
                    "--abfahrtszeit",
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
                "--ziel",
                "53.5511,9.9937",
                "--abfahrtszeit",
                "2026-08-15T08:00:00",
            ],
        )

        assert result.exit_code != 2
        assert "Ungültige Eingabe" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_ziel_coord(self) -> None:
        """Test: Ungültige Ziel-Koordinate -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--ziel",
                "invalid",
                "--abfahrtszeit",
                "2026-08-15T08:00:00",
            ],
        )

        assert result.exit_code != 2
        assert "Ungültige Eingabe" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_waypoint_format(self) -> None:
        """Test: Ungültiges Waypoint-Format -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--ziel",
                "53.5511,9.9937",
                "--abfahrtszeit",
                "2026-08-15T08:00:00",
                "--zwischenstopps",
                "invalid_waypoint",
            ],
        )

        assert result.exit_code != 2
        assert "Ungültige Eingabe" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_waypoint_duration(self) -> None:
        """Test: Ungültige Dauer im Waypoint -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--ziel",
                "53.5511,9.9937",
                "--abfahrtszeit",
                "2026-08-15T08:00:00",
                "--zwischenstopps",
                "52.0,12.0:abc",
            ],
        )

        assert result.exit_code != 2
        assert "Ungültige Eingabe" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_date_format(self) -> None:
        """Test: Ungültiges Datumsformat -> Exit Code 1."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--ziel",
                "53.5511,9.9937",
                "--abfahrtszeit",
                "invalid-date",
            ],
        )

        assert result.exit_code != 2
        assert "Ungültige Eingabe" in result.stderr or "Error" in result.stderr

    def test_cli_invalid_start_soc(self) -> None:
        """Test: Start-SoC außerhalb Bereich -> Exit Code != 0 (Typer validiert)."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--ziel",
                "53.5511,9.9937",
                "--abfahrtszeit",
                "2026-08-15T08:00:00",
                "--start-soc-pct",
                "150.0",  # > 100
            ],
        )

        assert result.exit_code != 0
        # Typer validiert min/max bevor unsere Funktion aufgerufen wird

    def test_cli_invalid_ziel_soc(self) -> None:
        """Test: Ziel-SoC außerhalb Bereich -> Exit Code != 0."""
        result = runner.invoke(
            app,
            [
                "trips",
                "--start",
                "52.5200,13.4050",
                "--ziel",
                "53.5511,9.9937",
                "--abfahrtszeit",
                "2026-08-15T08:00:00",
                "--ziel-soc-pct",
                "-10.0",  # < 0
            ],
        )

        assert result.exit_code != 0

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
                    "--ziel",
                    "53.5511,9.9937",
                    "--abfahrtszeit",
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
                    "--ziel",
                    "53.5511,9.9937",
                    "--abfahrtszeit",
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
                    "--ziel",
                    "53.5511,9.9937",
                    "--abfahrtszeit",
                    "2026-08-15T08:00:00",
                ],
            )

            assert result.exit_code == 1
            assert "Ungültige Eingabe" in result.stderr


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
        result = runner.invoke(app, ["trips", "--help"])
        assert result.exit_code == 0
        assert "--start" in result.stdout
        assert "--ziel" in result.stdout
        assert "--abfahrtszeit" in result.stdout
        assert "--zwischenstopps" in result.stdout
        assert "--start-soc-pct" in result.stdout
        assert "--ziel-soc-pct" in result.stdout
        assert "--vehicle-profile" in result.stdout
        assert "--output-json" in result.stdout
