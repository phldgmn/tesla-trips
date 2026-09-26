"""data models für das `charging_infrastructure`-Modul.

Pydantic-modele zur modelierung von Tesla-Supercharger-Stationen,
inklusive provider-Protocol für den abstrakten datazugriff.
Alle Koordinaten folgen der Konvention: `Coordinate = tuple[float, float]`
mit `(lat, lon)` in Dezimalgrad (WGS84).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from tripplanner.geo import Coordinate


class StallType(Enum):
    """Bezeichnung für den Stall-Typ, based auf supercharge.info data."""

    V2 = "V2"
    """V2-Supercharger (typisch 150 kW pro Stall)."""
    V3 = "V3"
    """V3-Supercharger (typisch 250 kW pro Stall)."""
    V3_ULTRA = "V3Ultra"
    """V3 Ultra (250 kW pro Stall, teilweise als V3+ bezeichnet)."""
    V4 = "V4"
    """V4-Supercharger (bis zu 325 kW pro Post, aktuelle Installationen)."""


class ConnectorType(Enum):
    """Steckertypen, wie auf supercharge.info üblich."""

    CCS1 = "CCS1"
    """Combined Charging System 1 (Type 1 AC + DC)."""
    CCS2 = "CCS2"
    """Combined Charging System 2 (Type 2 AC + DC)."""
    TYPE2 = "Type2"
    """Type 2 AC nur (Mennekes)."""
    TFC = "TFC"
    """Tesla Family Charging (Type 2 AC + DC)."""
    CBCC = "CBCC"
    """Tesla Supercharger (CCS Combo)."""
    TESLA = "Tesla"
    """Tesla Roadster Plug (Nema 14-50)."""
    CHADEMO = "chademo"
    """CHAdeMO (Japanisch)."""
    GBDC = "GBDC"
    """GB/T (Chinesisch)."""
    GB_T = "GB/T"
    """GB/T (Chinesisch, alternative Schreibweise)."""
    NACS = "NACS"
    """North American Charging Standard (Tesla)."""


class ChargingStation(BaseModel):
    """model einer Tesla Supercharger-Station.

    Basierend auf supercharge.info JSON-Struktur und OpenChargeMap-Referenzdaten.
    Koordinaten nach WGS84 (GPS).
    """

    station_id: str = Field(
        ...,
        description="Eindeutige ID der Station (supercharge.info-GUID oder interner Code)",
        min_length=1,
    )
    name: str = Field(
        ...,
        description=(
            "Name/Bezeichnung des Standorts (z. B. 'Tesla Supercharger - Interstate 80, Reno')"
        ),
        min_length=1,
    )
    coordinate: Coordinate = Field(
        ...,
        description="Standortkoordinate als (lat, lon) Tuple (WGS84, Decimal Degrees)",
    )
    stalls: dict[StallType, int] = Field(
        ...,
        description=(
            "Anzahl Stalls pro Typ. Beispiel: "
            "{StallType.V3: 8, StallType.V3_ULTRA: 4, StallType.V2: 0}"
        ),
    )
    max_ladeleistung_kw: float = Field(
        ...,
        ge=0,
        description="Maximale kombinierte DC-Leistung der Station (kW). Summiert über alle Stalls.",
    )
    connector_types: list[ConnectorType] = Field(
        ...,
        description="Verfügbare Steckertypen an der Station",
    )
    country: Literal["DE", "DK", "SE"] = Field(
        ...,
        description="ISO-Laendercode, wo sich die Station befindet",
    )
    ist_24_7: bool = Field(
        default=True,
        description="Tesla Supercharger sind typischerweise 24/7 zugaenglich",
    )
    status: Literal["online", "offline", "wartung", "temporaer_geschlossen"] = Field(
        default="online",
        description="Status der Station (optional, Default online)",
    )
    letzte_datenAktualisierung: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="timestamp der letzten Datenaktualisierung (Snapshot-Datum)",
    )

    @field_validator("coordinate")
    @classmethod
    def validate_coordinate(cls, v: Coordinate) -> Coordinate:
        """Validiert die Koordinaten (lat: -90..90, lon: -180..180)."""
        lat, lon = v
        MIN_LAT, MAX_LAT = -90.0, 90.0
        MIN_LON, MAX_LON = -180.0, 180.0
        if not (MIN_LAT <= lat <= MAX_LAT):
            raise ValueError("Latitude must be between -90 and 90")
        if not (MIN_LON <= lon <= MAX_LON):
            raise ValueError("Lon must be between -180 and 180")
        if not all(math.isfinite(x) for x in v):
            raise ValueError("Coordinates must not be NaN or Inf")
        return v

    @field_validator("max_ladeleistung_kw")
    @classmethod
    def validate_max_ladeleistung_kw(cls, v: float) -> float:
        """Validiert die maximale charging_power (muss positiv und realistisch sein)."""
        if v <= 0:
            raise ValueError("max_ladeleistung_kw muss positiv sein")
        # Realistischer Maximalwert: V4-Supercharger ~325 kW pro Post * 8 Posts = 2600 kW
        MAX_REALISTIC_KW = 5000.0
        if v > MAX_REALISTIC_KW:
            raise ValueError("max_ladeleistung_kw scheint unrealistisch high")
        return v

    def anzahl_verfuegbare_stalls(self) -> int:
        """Gesamtanzahl der Stalls unabhaengig vom Typ."""
        return sum(self.stalls.values())

    def max_parallel_usability(self) -> int:
        """Schaetzung, wie viele Stalls gleichzeitig genutzt werden können.

        V2/V3 nutzen oft gemeinsame Kabinette (z. B. 4 Posts teilen 1 MW).
        Vereinfachung: pro 4 Posts ein gemeinsamer Kabinetttakt.
        """
        v2 = self.stalls.get(StallType.V2, 0)
        v3 = self.stalls.get(StallType.V3, 0) + self.stalls.get(StallType.V3_ULTRA, 0)
        v4 = self.stalls.get(StallType.V4, 0)

        # V2/V3: typischerweise 4 Posts pro Kabinett (1 MW), V4: 8 Posts pro Kabinett (1.2 MW)
        # Simplifikation: alle V2/V3 teilen sich die Cabinet-Gruppe, alle V4 ebenfalls.
        return (v2 + v3 + 3) // 4 + (v4 + 7) // 8

    @property
    def distance_to_km(self) -> float:
        """Distance zur letzten Suchkoordinate (in km).

        Wird von provider gesetzt, um Distanzberechnung zu vermeiden.
        """
        # Wird von provider gesetzt
        return 0.0


@runtime_checkable
class ChargingStationProvider(Protocol):
    """Protocol für den Zugriff auf Supercharger-data.

    Ermöglicht Austausch der dataquelle (lokale Datei, Crawler, API).
    """

    async def get_stations_in_radius(
        self,
        coordinate: Coordinate,
        radius_km: float,
        country_filter: Literal["DE", "DK", "SE"] | None = None,
    ) -> list[ChargingStation]:
        """Liefert alle Supercharger innerhalb des gegebenen Radius um die Koordinate.

        Args:
            coordinate: (lat, lon) als Tuple (WGS84)
            radius_km: Suchradius in Kilometern (Flugdistanz)
            country_filter: Optionaler Laenderfilter (DE/DK/SE)

        Returns:
            Liste von ChargingStation, sortiert nach distance (aufsteigend)
        """
        raise NotImplementedError

    async def get_stations_along_route(
        self,
        route: Any,  # tripplanner.routing.models.Route importiert bei Verwendung
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Liefert alle Supercharger entlang einer Route.

        Args:
            route: Die geplante Route
            search_radius_km: Radius um jeden segment-Mittelpunkt

        Returns:
            Dict mapping segment_index -> liste von ChargingStation
            (nur segmente mit mindestens einer Station)
        """
        raise NotImplementedError


class ChargingPricingTier(BaseModel):
    """Ein Preistier mit optionalen zeitbasierten Raten.

    Kann eine Flatrate (time_label=None) oder zeitabhaengige Raten abbilden.
    """

    tier_label: str = Field(
        ...,
        description='Name des Preistiers, z.B. "Charging Fees for Tesla Owner"',
    )
    time_label: str | None = Field(
        default=None,
        description=(
            'Zeitfenster als Text, z.B. "4:00 PM - 8:00 PM". None = Flatrate (immer gültig)'
        ),
    )
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Waehrung als ISO-4217-Code (EUR, SEK, DKK, ...)",
    )
    amount: float = Field(
        ...,
        gt=0,
        description="Preis pro Einheit (z.B. 0.39 EUR/kWh)",
    )
    unit: Literal["kWh", "min"] = Field(
        ...,
        description='Abrechnungseinheit: "kWh" (energy) oder "min" (time)',
    )
    idle_fee_text: str | None = Field(
        default=None,
        description="Idle-Fee als Rohtext, z.B. '0.50 EUR/min idle'",
    )


class ChargingStationWithPricing(BaseModel):
    """ChargingStation mit zugehörigen Preisdaten."""

    station: ChargingStation = Field(
        ...,
        description="Die zugehörige charging station",
    )
    pricing: list[ChargingPricingTier] = Field(
        default_factory=list,
        description="Preisinformationen für diese Station",
    )
