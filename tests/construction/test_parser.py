"""Tests für construction-Parser: DATEX II XML Parsing und Feld-Mapping."""

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest
from tripplanner.construction.models import Land
from tripplanner.construction.parser import (
    _delay_band_to_speed,
    _parse_datetime,
    parse_datexii_xml,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "construction"


@pytest.fixture
def germany_xml() -> str:
    """Lese deutsche DATEX II Fixture."""
    return (FIXTURES_DIR / "datexii_germany_roadworks_example.xml").read_text()


@pytest.fixture
def denmark_xml() -> str:
    """Lese dänische DATEX II Fixture."""
    return (FIXTURES_DIR / "datexii_denmark_lane_closure.xml").read_text()


@pytest.fixture
def nrw_arbeitsstellen_xml() -> str:
    """Lese NRW-Mobilitätsdaten-Arbeitsstellen-Fixture (posList unter groupOfLocations)."""
    return (FIXTURES_DIR / "nrw_arbeitsstellen_autobahn_example.xml").read_text()


def test_parse_datexii_germany(germany_xml: str) -> None:
    """Parse einer deutschen DATEX II Nachricht."""
    zones = parse_datexii_xml(germany_xml, Land.DE)

    assert len(zones) >= 1
    zone = zones[0]

    assert "Roadworks" in zone.closure_type or "MaintenanceWorks" in zone.closure_type
    assert zone.gueltig_von.year == 2024
    assert zone.gueltig_von.month == 3
    assert zone.gueltig_von.day == 20

    assert zone.gueltig_bis is not None
    assert zone.gueltig_bis.year == 2024
    assert zone.gueltig_bis.month == 3
    assert zone.gueltig_bis.day == 21

    assert len(zone.koordinaten) >= 2
    assert zone.speed_limit_kmh == 80


def test_parse_datexii_nrw_arbeitsstellen(nrw_arbeitsstellen_xml: str) -> None:
    """Parse NRW Mobilitätsdaten Arbeitsstellen: posList nested under groupOfLocations.

    Unlike the generic `datexii_germany_roadworks_example.xml` fixture (which
    uses `geographicPosition`/`latitude`+`longitude`), the real NRW feed
    places its LineString geometry at
    `groupOfLocations/linearExtension/linearExtended/gmlLineString/posList`
    rather than under `locationReference` — this exercises the fallback
    added for that schema variant.
    """
    zones = parse_datexii_xml(nrw_arbeitsstellen_xml, Land.DE)

    assert len(zones) == 2

    maintenance_zone = next(z for z in zones if z.closure_type == "MaintenanceWorks")
    assert maintenance_zone.koordinaten == [
        (51.210673, 14.553138),
        (51.210674, 14.553149),
        (51.21149, 14.56774),
    ]
    assert maintenance_zone.gueltig_von.isoformat() == "2024-11-18T07:00:00+00:00"
    assert maintenance_zone.gueltig_bis is not None
    assert maintenance_zone.gueltig_bis.isoformat() == "2024-11-26T15:00:00+00:00"
    assert maintenance_zone.speed_limit_kmh is None

    construction_zone = next(z for z in zones if z.closure_type == "ConstructionWorks")
    assert len(construction_zone.koordinaten) == 2


def test_parse_datexii_denmark(denmark_xml: str) -> None:
    """Parse einer dänischen DATEX II Nachricht."""
    zones = parse_datexii_xml(denmark_xml, Land.DK)

    assert len(zones) >= 1
    zone = zones[0]

    assert "Lane" in zone.closure_type or "Roadworks" in zone.closure_type
    assert zone.gueltig_von.year == 2024
    assert zone.gueltig_von.month == 3
    assert zone.gueltig_von.day == 17

    assert zone.gueltig_bis is not None
    assert zone.gueltig_bis.year == 2024
    assert zone.gueltig_bis.month == 3
    assert zone.gueltig_bis.day == 17

    assert len(zone.koordinaten) >= 3
    assert zone.speed_limit_kmh == 100


def test_delay_band_to_speed() -> None:
    """Test _delay_band_to_speed mapping."""
    assert _delay_band_to_speed("upToTenMinutes") == 100
    assert _delay_band_to_speed("tenToTwentyMinutes") == 80
    assert _delay_band_to_speed("twentyToFortyMinutes") == 60
    assert _delay_band_to_speed("overFortyMinutes") == 40
    assert _delay_band_to_speed("unknownBand") == 60
    assert _delay_band_to_speed(None) is None


def test_parse_datetime() -> None:
    """Test _parse_datetime helper."""
    dt = _parse_datetime("2024-03-15T18:03:11Z")
    assert dt.year == 2024
    assert dt.month == 3
    assert dt.day == 15
    assert dt.hour == 18

    dt2 = _parse_datetime("2024-03-15T18:03:11+00:00")
    assert dt2.year == 2024
    assert dt2.month == 3
    assert dt2.day == 15

    dt3 = _parse_datetime(None)
    assert dt3 is not None
    assert isinstance(dt3, datetime)


def test_parse_empty_xml() -> None:
    """Test Parsing eines leeren/ungültigen XML."""
    zones = parse_datexii_xml("<root></root>", Land.DE)
    assert zones == []


def test_parse_invalid_xml() -> None:
    """Ungültiges XML wirft einen ParseError (kein stilles Leerergebnis)."""
    with pytest.raises(ET.ParseError):
        parse_datexii_xml("<invalid xml>", Land.DE)
