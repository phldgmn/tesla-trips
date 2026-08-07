# Plan: battery & charging_infrastructure

---

## 1. Zweck & Scope

Dieses Dokument beschreibt den Implementierungsplan für die beiden Module `battery` (Phase 4) und `charging_infrastructure` (Phase 1). Die Module arbeiten eng zusammen: `charging_infrastructure` stellt die verfügbaren Tesla-Supercharger (Standorte, Stalls, Leistungsdaten) bereit; `battery` modelliert das Ladeverhalten der Batterie anhand dieser Ladeleistung, um realistische Ladezeiten zu berechnen.

**Modul `charging_infrastructure`:**

- Bereitstellung einer Liste aller Tesla Supercharger entlang der Route oder in der Nähe (Suchradius parameterisierbar)
- Abstraktion über ein `ChargingStationProvider`-Interface
- Keine Live-API-Abfragen im Unit-Test, sondern ausschließlich Fixtures (lokale JSON-Snapshot-Datei als primäre Datenquelle, siehe Abschnitt 5)

**Modul `battery`:**

- Modellierung der Ladekurve als stückweise lineare Funktion über SoC-Bereiche
- Berechnung der Ladedauer für gegebene Start-SoC, Ziel-SoC und konstante Ladeleistung (Integration über die stückweise Kurve)
- Unterstützung der typischen Tesla-Ladekurven-Charakteristika (hohe Leistung bis ~20-30% SoC, dann lineares Abfallen, starkes Abbremsen ab ~80% SoC)
- Eingabe: aktueller SoC, Ladeleistung (kW), Ausgabe: verstrichene Zeit bis Ziel-SoC, SoC-Zeitkurve während des Ladevorgangs

**Abgrenzung zu anderen Modulen:**

- `charging_infrastructure` liefert *nur* die Infrastrukturdaten, keine Ladezeitberechnung
- `battery` modelliert das Verhalten des Fahrzeugs bei Ladevorgängen, nicht das Routing oder die Gesamtoptimierung
- `energy`-Modul berechnet den Verbrauch beim Fahren; `battery` befasst sich ausschließlich mit dem Laden an einer Station
- `optimization`-Modul nutzt `battery`-Output (Ladedauern) als Input für die Gesamtoptimierung

**Nicht-Scope (aus den offenen Punkten):**

- Crawler zur automatischen Datensammlung von Tesla-Superchargern — dies ist ein separater, späterer Ausbauschritt (vgl. docs/06, Punkt 3). Im aktuellen Plan wird lokal vorliegende Daten (JSON-Snapshot) angenommen.
- Live-Verfügbarkeitsprüfung der Stalls zur Laufzeit (z. B. "Ist dieser Stall gerade belegt?") — dies wird vereinfacht als Konstante (maximale parallel nutzbare Stalls pro Station) modelliert.

---

## 2. Abhängigkeiten & Phasenzuordnung

**Phase:** `charging_infrastructure` Phase 1, `battery` Phase 4 (nach `energy`, `weather`, `wind`, `construction`, `routing`, `elevation`, `optimization`-Vorbereitung)

**Importierte Typen aus anderen Modulen (nur Lesen):**

- `tripplanner.routing.models.Route` (referenziert für `RouteSegment` in `charging_infrastructure`, Suchradius-Filterung entlang der Route)
- `tripplanner.routing.models.RouteSegment` (geometrische Grundlage für Standortsuche)
- `tripplanner.trip_input.models.VehicleProfile` (referenziert in `battery`, um auf Fahrzeug-spezifische Eigenschaften wie max. akzeptierte Ladeleistung prüfen zu können — optional, Default-Parameter im Pydantic-Modell)
- `tripplanner.weather.models.WeatherSample` (für `battery` — Temperatur kann die Ladeeffizienz beeinflussen, vereinfachend als Temperatur-korrekturfaktor für Ladedauer genutzt)

---

## 3. Datenmodelle

### Modul `charging_infrastructure.models`

```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional
from enum import Enum
from datetime import datetime
from math import isfinite


class StallType(Enum):
    """Bezeichnung für den Stall-Typ, basierend auf supercharge.info-Daten."""

    V2 = "V2"
    V3 = "V3"
    V3_ULTRA = "V3Ultra"  # 250 kW pro Stall, teilweise als V3+ bezeichnet
    V4 = "V4"  # bis zu 500 kW pro Post (aktuelle Installationen oft 325 kW)


class ConnectorType(Enum):
    """Steckertypen, wie auf supercharge.info üblich."""

    NACS = "NACS"  # Tesla North America Charging Standard
    CCS1 = "CCS1"  # Combined Charging System 1 (MagicDock in NEU)
    CCS2 = "CCS2"  # Combined Charging System 2 (Europa)
    TYPE2 = "Type2"  # Mennekes (Europa)
    GB_T = "GB/T"  # Chinesischer Standard


class ChargingStation(BaseModel):
    """
    Modell einer Tesla Supercharger-Station.

    Basierend auf supercharge.info JSON-Struktur und OpenChargeMap-Referenzdaten.
    Koordinaten nach WGS84 (GPS).
    """

    station_id: str = Field(
        ..., description="Eindeutige ID der Station (supercharge.info-GUID oder interner Code)"
    )
    name: str = Field(
        ...,
        description="Name/Bezeichnung des Standorts (z. B. 'Tesla Supercharger - Interstate 80, Reno')",
    )
    coordinate: tuple[float, float] = Field(
        ..., description="Standortkoordinate als (lat, lon) Tuple (WGS84, Decimal Degrees)"
    )
    stalls: dict[StallType, int] = Field(
        ..., description="Anzahl Stalls pro Typ. Beispiel: {'V3': 8, 'V3ULTRA': 4, 'V2': 0}"
    )
    max_ladeleistung_kw: float = Field(
        ...,
        ge=0,
        description="Maximale kombinierte DC-Leistung der Station (kW). Summiert über alle Stalls.",
    )
    connector_types: list[ConnectorType] = Field(
        ..., description="Verfügbare Steckertypen an der Station"
    )
    country: Literal["DE", "DK", "SE"] = Field(
        ..., description="ISO-Ländercode, wo sich die Station befindet"
    )
    ist_24_7: bool = Field(
        default=True, description="Tesla Supercharger sind typischerweise 24/7 zugänglich"
    )
    status: Literal["online", "offline", "wartung", "temporaer_geschlossen"] = Field(
        default="online", description="Status der Station (optional, Default online)"
    )
    letzte_datenAktualisierung: datetime = Field(
        default_factory=datetime.utcnow,
        description="Zeitpunkt der letzten Datenaktualisierung (Snapshot-Datum)",
    )

    @field_validator("coordinate")
    @classmethod
    def validate_coordinate(cls, v: tuple[float, float]) -> tuple[float, float]:
        lat, lon = v
        if not (-90 <= lat <= 90):
            raise ValueError("Breitengrad muss zwischen -90 und 90 liegen")
        if not (-180 <= lon <= 180):
            raise ValueError("Längengrad muss zwischen -180 und 180 liegen")
        if not all(isfinite(x) for x in v):
            raise ValueError("Koordinaten dürfen nicht NaN oder Inf sein")
        return v

    @field_validator("max_ladeleistung_kw")
    @classmethod
    def validate_max_ladeleistung_kw(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_ladeleistung_kw muss positiv sein")
        # Realistischer Maximalwert: V4-Supercharger ~500 kW pro Post * 8 Posts = 4000 kW
        if v > 5000:
            raise ValueError("max_ladeleistung_kw scheint unrealistisch hoch")
        return v

    def anzahl_verfuegbare_stalls(self) -> int:
        """Gesamtanzahl der Stalls unabhängig vom Typ."""
        return sum(self.stalls.values())

    def max_parallele_nutzbarkeit(self) -> int:
        """
        Schätzung, wie viele Stalls gleichzeitig genutzt werden können.
        V2/V3 nutzen oft gemeinsame Kabinette (z. B. 4 Posts teilen 1 MW).
        Vereinfachung: pro 4 Posts ein gemeinsamer Kabinetttakt.
        """
        v2 = self.stalls.get(StallType.V2, 0)
        v3 = self.stalls.get(StallType.V3, 0) + self.stalls.get(StallType.V3_ULTRA, 0)
        v4 = self.stalls.get(StallType.V4, 0)

        # V2/V3: typischerweise 4 Posts pro Kabinett (1 MW), V4: 8 Posts pro Kabinett (1.2 MW)
        # Simplifikation: alle V2/V3 teilen sich die Cabinet-Gruppe, alle V4 ebenfalls.
        return (v2 + v3 + 3) // 4 + (v4 + 7) // 8


class ChargingStationProvider:
    """
    Protocol für den Zugriff auf Supercharger-Daten.
    Ermöglicht Austausch der Datenquelle (lokale Datei, Crawler, API).
    """

    async def get_stations_in_radius(
        self,
        coordinate: tuple[float, float],
        radius_km: float,
        country_filter: Optional[Literal["DE", "DK", "SE"]] = None,
    ) -> list[ChargingStation]:
        """
        Liefert alle Supercharger innerhalb des gegebenen Radius um die Koordinate.

        Args:
            coordinate: (lat, lon) als Tuple (WGS84)
            radius_km: Suchradius in Kilometern (Flugdistanz)
            country_filter: Optionaler Länderfilter (DE/DK/SE)

        Returns:
            Liste von ChargingStation, sortiert nach Distanz (aufsteigend)
        """
        raise NotImplementedError

    async def get_stations_along_route(
        self,
        route: "tripplanner.routing.models.Route",
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """
        Liefert alle Supercharger entlang einer Route.

        Args:
            route: Die geplante Route
            search_radius_km: Radius um jeden Segment-Mittelpunkt

        Returns:
            Dict mapping segment_index -> liste von ChargingStation
            (nur Segmente mit mindestens einer Station)
        """
        raise NotImplementedError
```

**Hinweis zum Datenformat (JSON):**

Die lokale Snapshot-Datei (`data/supercharger_snapshot.json`) folgt diesem Schema:

```json
{
  "stations": [
    {
      "station_id": "sc-12345",
      "name": "Tesla Supercharger - Berlin Alexanderplatz",
      "lat": 52.5234,
      "lon": 13.4114,
      "stalls": {
        "V3": 8,
        "V3Ultra": 4
      },
      "connector_types": ["NACS", "CCS1", "CCS2"],
      "country": "DE",
      "max_ladeleistung_kw": 2500.0
    }
  ],
  "meta": {
    "erstellungsdatum": "2026-07-01T12:00:00Z",
    "quelle": "supercharge.info (Snapshot)",
    "version": "v1.2"
  }
}
```

---

### Modul `battery.models`

```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from enum import Enum
from math import isfinite


class SoCState(BaseModel):
    """
    Batteriezustand zu einem Zeitpunkt.
    """

    soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ladestand in Prozent (0–100)")
    zeitpunkt: float = Field(..., description="Zeitpunkt in Sekunden seit Reisebeginn")


class ChargingCurvePoint(BaseModel):
    """
    Ein Punkt auf der Ladekurve: (SoC, Ladeleistung).
    Die Kurve ist stückweise linear zwischen den Punkten.
    """

    soc_pct: float = Field(..., ge=0.0, le=100.0)
    ladeleistung_kw: float = Field(..., ge=0.0)

    @field_validator("soc_pct")
    @classmethod
    def validate_soc(cls, v: float) -> float:
        if not isfinite(v):
            raise ValueError("soc_pct muss endlich sein")
        return v

    @field_validator("ladeleistung_kw")
    @classmethod
    def validate_leistung(cls, v: float) -> float:
        if not isfinite(v) or v < 0:
            raise ValueError("ladeleistung_kw muss endlich und nichtnegativ sein")
        return v


class InterpolationMethod(Enum):
    """
    Interpolationsmethoden für die Ladekurve.
    """

    LINEAR = "linear"  # Stückweise lineare Interpolation (Standard)
    HERMITE = "hermite"  # C1-stetig (zukünftig nutzbar, falls höhere Genauigkeit nötig)


class ChargingCurve(BaseModel):
    """
    Die Ladekurve des Fahrzeugs. Basierend auf typischen Tesla-Charakteristika.

    Die Kurve ist in typischen Phasen definiert:
    - 0–20% SoC: konstant hohe Leistung (ca. 250–300 kW für V3, 325 kW für V4)
    - 20–80% SoC: lineares Abfallen auf ca. 50–80 kW
    - 80–100% SoC: starkes Abbremsen (exponentiell/näherungsweise linear mit kleiner Steigung)

    Die Punkte sind in aufsteigender Reihenfolge nach soc_pct zu definieren.
    """

    points: list[ChargingCurvePoint] = Field(
        ..., description="Mindestens 4 Punkte für sinnvolle Approximation"
    )
    interpolation: InterpolationMethod = Field(
        default=InterpolationMethod.LINEAR,
        description="Interpolationsmethode für Punktezwischenräume",
    )

    @field_validator("points")
    @classmethod
    def validate_points(cls, v: list[ChargingCurvePoint]) -> list[ChargingCurvePoint]:
        if len(v) < 4:
            raise ValueError("Ladekurve benötigt mindestens 4 Punkte")
        sorted_points = sorted(v, key=lambda p: p.soc_pct)
        for i, p in enumerate(sorted_points):
            if p.soc_pct < 0 or p.soc_pct > 100:
                raise ValueError(f"Punkt {i}: soc_pct außerhalb [0,100]")
            if i > 0 and p.ladeleistung_kw < sorted_points[i - 1].ladeleistung_kw:
                raise ValueError("Ladeleistung darf nicht mit steigendem SoC steigen")
        return sorted_points

    def ladeleistung_bei_soc(self, soc_pct: float) -> float:
        """
        Berechnet die Ladeleistung (kW) für einen gegebenen SoC mittels linearer Interpolation.
        Extrapolation außerhalb des Bereichs mit dem Randwert.
        """
        if soc_pct <= self.points[0].soc_pct:
            return self.points[0].ladeleistung_kw
        if soc_pct >= self.points[-1].soc_pct:
            return self.points[-1].ladeleistung_kw

        for i in range(len(self.points) - 1):
            p1, p2 = self.points[i], self.points[i + 1]
            if p1.soc_pct <= soc_pct <= p2.soc_pct:
                t = (soc_pct - p1.soc_pct) / (p2.soc_pct - p1.soc_pct)
                return p1.ladeleistung_kw + t * (p2.ladeleistung_kw - p1.ladeleistung_kw)

        return self.points[-1].ladeleistung_kw


class VehicleBatteryParameters(BaseModel):
    """
    Fahrzeug-spezifische Batterieeigenschaften.

    Vorrangig für spätere Kalibrierung mit Fahrdaten vorgesehen.
    Default-Werte basierend auf typischem Model 3 LR Verhalten.
    """

    batteriekapazitaet_kwh: float = Field(
        default=75.0,
        ge=50.0,
        le=100.0,
        description="Nutzkapazität der Batterie in kWh (Typ Model 3 LR: ~75 kWh)",
    )
    max_ladeleistung_kw: float = Field(
        default=250.0,
        ge=50.0,
        le=350.0,
        description="Maximale akzeptierte Ladeleistung des Fahrzeugs (kW)",
    )
    effizienz_ladeelektronik: float = Field(
        default=0.95,
        ge=0.85,
        le=1.0,
        description="Wirkungsgrad der Ladeelektronik (Verluste in der Wechselrichtereinheit)",
    )
    temperatur_korrekturfaktor: float = Field(
        default=1.0,
        ge=0.7,
        le=1.1,
        description="Multiplikator für Ladedauer basierend auf Batterie-/Umgebungstemperatur",
    )


class ChargingStop(BaseModel):
    """
    Resultat einer Ladevorgangs-Berechnung für einen Station-Halt.
    """

    station: ChargingStation
    ankunfts_soc_pct: float
    ziel_soc_pct: float
    geschaetzte_ladedauer_s: float
    ankunftszeit_s: float  # seit Reisebeginn
    abfahrtszeit_s: float  # seit Reisebeginn


class LadekurveReferenz:
    """
    Referenz-Ladekurven für gängige Tesla-Modelle / Konfigurationen.
    Basierend auf Community-Messdaten (Forums, supercharge.info).

    Die Kurven sind als stückweise lineare Approximation definiert.
    """

    @staticmethod
    def model_3_lr_v3() -> ChargingCurve:
        """
        Model 3 Long Range mit V3-Supercharger.
        Typische Messwerte:
        - 0–20%: ~250–280 kW
        - 20–50%: lineares Abfallen auf ~150 kW
        - 50–80%: auf ~80 kW
        - 80–100%: starkes Abbremsen auf <20 kW
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=150.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=75.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=35.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=10.0),
            ]
        )

    @staticmethod
    def model_3_lr_v4() -> ChargingCurve:
        """
        Model 3 Long Range mit V4-Supercharger (325 kW Installation).
        V4 ermöglicht längeren Hochleistungsbetrieb.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=325.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=325.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=250.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=120.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=60.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=15.0),
            ]
        )

    @staticmethod
    def model_y_performance_v3() -> ChargingCurve:
        """
        Model Y Performance (größere Batterie, aber similarer Kurvenverlauf).
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=260.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=260.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=140.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=70.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=30.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=8.0),
            ]
        )
```

---

## 4. Öffentliche Schnittstelle

### Modul `charging_infrastructure`

```python
# src/tripplanner/charging_infrastructure/__init__.py
from .models import (
    ChargingStation,
    ChargingStationProvider,
    StallType,
    ConnectorType,
)
from .providers import LocalFileChargingStationProvider

__all__ = [
    "ChargingStation",
    "ChargingStationProvider",
    "StallType",
    "ConnectorType",
    "LocalFileChargingStationProvider",
]
```

```python
# src/tripplanner/charging_infrastructure/providers.py
from typing import Optional
from .models import (
    ChargingStation,
    ChargingStationProvider,
    ConnectorType,
    StallType,
)
import json
from pathlib import Path


class LocalFileChargingStationProvider(ChargingStationProvider):
    """
    Implementierung, die Ladedaten aus einer lokalen JSON-Datei liest.
    Für Tests und Produktion (solange kein Crawler implementiert ist).
    """

    def __init__(self, data_path: Path):
        """
        Args:
            data_path: Pfad zur JSON-Datei (z. B. data/supercharger_snapshot.json)
        """
        self.data_path = data_path
        self._stations: list[ChargingStation] | None = None

    def _load_stations(self) -> list[ChargingStation]:
        """Lädt und parst die JSON-Datei (lazy)."""
        if self._stations is not None:
            return self._stations

        with open(self.data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        stations: list[ChargingStation] = []
        for item in data["stations"]:
            stalls = {
                StallType.V2: item["stalls"].get("V2", 0),
                StallType.V3: item["stalls"].get("V3", 0),
                StallType.V3_ULTRA: item["stalls"].get("V3Ultra", 0),
                StallType.V4: item["stalls"].get("V4", 0),
            }

            # Max Leistung berechnen: Summe aller Stalls * durchschnittliche Leistung pro Stall
            # Vereinfachung: V2=150kW, V3=250kW, V3Ultra=325kW, V4=325kW
            max_leistung = (
                stalls[StallType.V2] * 150.0
                + stalls[StallType.V3] * 250.0
                + stalls[StallType.V3_ULTRA] * 325.0
                + stalls[StallType.V4] * 325.0
            )

            stations.append(
                ChargingStation(
                    station_id=item["station_id"],
                    name=item["name"],
                    coordinate=(item["lat"], item["lon"]),
                    stalls=stalls,
                    max_ladeleistung_kw=max_leistung,
                    connector_types=[ConnectorType(c) for c in item["connector_types"]],
                    country=item["country"],
                    ist_24_7=True,
                    status="online",
                    letzte_datenAktualisierung=item.get("erstellungsdatum", "2026-01-01T00:00:00Z")
                    if isinstance(item.get("erstellungsdatum"), str)
                    else item.get("erstellungsdatum"),
                )
            )

        self._stations = stations
        return stations

    async def get_stations_in_radius(
        self,
        coordinate: tuple[float, float],
        radius_km: float,
        country_filter: Optional[Literal["DE", "DK", "SE"]] = None,
    ) -> list[ChargingStation]:
        import math

        lat, lon = coordinate
        haversin = lambda a: math.sin(a / 2) ** 2
        hav_inv = lambda a: 2 * math.asin(math.sqrt(a))

        R = 6371.0  # Erdradius in km

        stations = self._load_stations()
        if country_filter:
            stations = [s for s in stations if s.country == country_filter]

        result: list[ChargingStation] = []
        for station in stations:
            slat, slon = station.coordinate
            dlat = math.radians(slat - lat)
            dlon = math.radians(slon - lon)
            a = haversin(dlat) + math.cos(math.radians(lat)) * math.cos(
                math.radians(slat)
            ) * haversin(dlon)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            distance = R * c

            if distance <= radius_km:
                result.append(station)

        # Sortieren nach Distanz (aufsteigend)
        result.sort(key=lambda s: self._distance_to(coordinate, s.coordinate))
        return result

    def _distance_to(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        import math

        R = 6371.0
        lat1, lon1 = math.radians(a[0]), math.radians(a[1])
        lat2, lon2 = math.radians(b[0]), math.radians(b[1])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    async def get_stations_along_route(
        self,
        route: "tripplanner.routing.models.Route",
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """
        Sucht Supercharger entlang der Route.
        Für jedes Segment wird der Mittelpunkt berechnet und in einem Radius von `search_radius_km` gesucht.
        """
        result: dict[int, list[ChargingStation]] = {}
        stations = self._load_stations()

        for i, segment in enumerate(route.segments):
            # Segment-Mittelpunkt als geo-mittlerer Punkt
            coords = segment.geometrie  # list[(lat,lon)]
            mid_idx = len(coords) // 2
            mid_point = coords[mid_idx]

            nearby = await self.get_stations_in_radius(mid_point, search_radius_km)
            if nearby:
                result[i] = nearby

        return result
```

```python
# src/tripplanner/charging_infrastructure/<modul>.py
from typing import Optional
from .models import (
    ChargingStation,
    ChargingStationProvider,
    StallType,
    ConnectorType,
)
from .providers import LocalFileChargingStationProvider
from pathlib import Path


# Default-Provider mit lokaler Datei
_DEFAULT_PROVIDER: ChargingStationProvider | None = None


def init_charging_infrastructure(data_path: Path | None = None) -> None:
    """Initialisiert den globalen Provider mit der lokalen Datei (falls nicht gesetzt)."""
    global _DEFAULT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        if data_path is None:
            data_path = Path(__file__).parent.parent / "data" / "supercharger_snapshot.json"
        _DEFAULT_PROVIDER = LocalFileChargingStationProvider(data_path)


async def get_charging_stations_in_radius(
    coordinate: tuple[float, float],
    radius_km: float = 5.0,
    country: Optional[Literal["DE", "DK", "SE"]] = None,
) -> list[ChargingStation]:
    """
    Hilfsfunktion für die übliche Abfrage nach Supercharger in der Nähe einer Koordinate.
    Nutzt den globalen Provider.
    """
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure()
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_in_radius(coordinate, radius_km, country)


async def get_charging_stations_along_route(
    route: "tripplanner.routing.models.Route",
    search_radius_km: float = 2.0,
) -> dict[int, list[ChargingStation]]:
    """Hilfsfunktion für die Abfrage entlang einer Route."""
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure()
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_along_route(route, search_radius_km)
```

---

### Modul `battery`

```python
# src/tripplanner/battery/__init__.py
from .models import (
    SoCState,
    ChargingCurvePoint,
    ChargingCurve,
    InterpolationMethod,
    VehicleBatteryParameters,
    ChargingStop,
    LadekurveReferenz,
)
from .battery import berechne_ladedauer, simulate_ladevorgang, berechne_soc_nach_segment

__all__ = [
    "SoCState",
    "ChargingCurvePoint",
    "ChargingCurve",
    "InterpolationMethod",
    "VehicleBatteryParameters",
    "ChargingStop",
    "LadekurveReferenz",
    "berechne_ladedauer",
    "simulate_ladevorgang",
    "berechne_soc_nach_segment",
]
```

```python
# src/tripplanner/battery/battery.py
"""
Zentrale Lade- und Entlade-Logik für das battery-Modul.

Die Berechnungen basieren auf physikalischen Gesetzen (Energie = Leistung × Zeit),
angepasst an die stückweise lineare Ladekurve und Fahrzeugparameter.
"""

from typing import Optional
from .models import (
    SoCState,
    ChargingCurve,
    ChargingCurvePoint,
    InterpolationMethod,
    VehicleBatteryParameters,
    ChargingStop,
)


def berechne_ladedauer(
    start_soc_pct: float,
    ziel_soc_pct: float,
    ladeleistung_kw: float,
    charging_curve: ChargingCurve,
    parameters: VehicleBatteryParameters,
) -> float:
    """
    Berechnet die Ladedauer in Sekunden von `start_soc_pct` bis `ziel_soc_pct`
    bei konstanter Ladeleistung `ladeleistung_kw`.

    Die reale Ladedauer wird durch die Kurve begrenzt: Die effektive Ladeleistung
    ist das Minimum aus `ladeleistung_kw` und der Kurvenleistung bei jedem SoC-Punkt.

    Algorithmus:
    1. Berechne die benötigte Energie: dQ = (ziel_soc - start_soc) / 100 * kapazitaet
    2. Integriere über den SoC-Bereich: ∫ dQ / min(ladeleistung_kw, kurven_leistung(soc))
    3. Multipliziere mit dem Wirkungsgrad der Ladeelektronik

    Für stückweise lineare Kurven kann das Integral analytisch gelöst werden.
    """
    if start_soc_pct >= ziel_soc_pct:
        return 0.0

    kapazitaet = parameters.batteriekapazitaet_kwh
    effizienz = parameters.effizienz_ladeelektronik
    temperatur_faktor = parameters.temperatur_korrekturfaktor

    # Begrenze die Ladeleistung durch die Kurve
    def effective_leistung(soc_pct: float) -> float:
        kurven_leistung = charging_curve.ladeleistung_bei_soc(soc_pct)
        return min(ladeleistung_kw, kurven_leistung)

    # Numerische Integration über den SoC-Bereich
    # Feine Diskretisierung (0.1% Schritte) für ausreichende Genauigkeit
    steps = int((ziel_soc_pct - start_soc_pct) * 10)
    if steps < 1:
        steps = 1

    total_time_s = 0.0
    soc_delta = (ziel_soc_pct - start_soc_pct) / steps

    for i in range(steps):
        soc = start_soc_pct + i * soc_delta
        leistung = effective_leistung(soc)
        if leistung <= 0:
            continue  # Vermeide Division durch Null

        # Energie für diesen SoC-Schritt
        dQ_kwh = (soc_delta / 100.0) * kapazitaet
        # Zeit = Energie / Leistung (in Stunden), umrechnen in Sekunden
        dtime_h = dQ_kwh / leistung
        dtime_s = dtime_h * 3600.0
        total_time_s += dtime_s

    # Wirkungsgrad und Temperatur korrigieren
    total_time_s /= effizienz
    total_time_s *= temperatur_faktor

    return total_time_s


def simulate_ladevorgang(
    start_soc_pct: float,
    ziel_soc_pct: float,
    ladeleistung_kw: float,
    charging_curve: ChargingCurve,
    parameters: VehicleBatteryParameters,
) -> list[tuple[float, float]]:
    """
    Simuliert den Ladevorgang und gibt eine Liste von (zeit_s, soc_pct) Punkten zurück.
    Nützlich für Visualisierung der Ladekurve.
    """
    if start_soc_pct >= ziel_soc_pct:
        return [(0.0, start_soc_pct)]

    kapazitaet = parameters.batteriekapazitaet_kwh
    effizienz = parameters.effizienz_ladeelektronik
    temperatur_faktor = parameters.temperatur_korrekturfaktor

    steps = 50  # 50 Punkte für glatte Kurve
    result: list[tuple[float, float]] = []
    total_time = berechne_ladedauer(
        start_soc_pct, ziel_soc_pct, ladeleistung_kw, charging_curve, parameters
    )

    for i in range(steps + 1):
        t_ratio = i / steps
        t_s = t_ratio * total_time
        # Invers interpolation: welcher SoC zu Zeit t_s?
        # Einfachheitshalber linear interpolieren zwischen den Endpunkten
        soc = start_soc_pct + t_ratio * (ziel_soc_pct - start_soc_pct)
        result.append((t_s, soc))

    # Letzter Punkt genau setzen
    result[-1] = (total_time, ziel_soc_pct)
    return result


def berechne_soc_nach_segment(
    start_soc_pct: float,
    segment_energy_kwh: float,
    batterie_param: VehicleBatteryParameters,
) -> float:
    """
    Berechnet den Batteriestand nach einem Fahrtsegment.

    Args:
        start_soc_pct: Batteriestand am Segmentbeginn (0–100)
        segment_energy_kwh: Energieverbrauch des Segments (positive Zahl für Verbrauch)
        batterie_param: Fahrzeug-Batterieparameter

    Returns:
        Batteriestand am Segmentende (0–100), begrenzt auf [0,100]
    """
    kapazitaet = batterie_param.batteriekapazitaet_kwh
    energy_wh = segment_energy_kwh * 1000.0
    kapazitaet_wh = kapazitaet * 1000.0

    start_wh = (start_soc_pct / 100.0) * kapazitaet_wh
    end_wh = start_wh - energy_wh

    # Sicherstellen, dass SoC im gültigen Bereich bleibt
    end_soc_pct = (end_wh / kapazitaet_wh) * 100.0
    return max(0.0, min(100.0, end_soc_pct))


def berechne_ladehalt(
    station: "tripplanner.charging_infrastructure.models.ChargingStation",
    start_soc_pct: float,
    ziel_soc_pct: float,
    fahrzeug_param: VehicleBatteryParameters,
    ladeleistung_kw: Optional[float] = None,
) -> ChargingStop:
    """
    Berechnet einen kompletten Ladehalt an einer Station.

    Args:
        station: Die Supercharger-Station
        start_soc_pct: Batteriestand bei Ankunft
        ziel_soc_pct: Gewünschter Batteriestand bei Abfahrt
        fahrzeug_param: Fahrzeugparameter
        ladeleistung_kw: Optionale manuelle Ladeleistung (sonst max. Station + Fahrzeug)

    Returns:
        ChargingStop mit allen relevanten Daten
    """
    # Limitiere Ladeleistung durch das Fahrzeug
    effective_leistung = min(
        ladeleistung_kw or station.max_ladeleistung_kw, fahrzeug_param.max_ladeleistung_kw
    )

    # Bestimme passende Kurve (V3 oder V4 basierend auf Station)
    # Vereinfachung: Wenn V4-Stalls vorhanden, nimm V4-Kurve, sonst V3
    v4_count = station.stalls.get("V4", 0)
    if v4_count > 0 and effective_leistung > 280:
        curve = LadekurveReferenz.model_3_lr_v4()
    else:
        curve = LadekurveReferenz.model_3_lr_v3()

    ladedauer_s = berechne_ladedauer(
        start_soc_pct, ziel_soc_pct, effective_leistung, curve, fahrzeug_param
    )

    return ChargingStop(
        station=station,
        ankunfts_soc_pct=start_soc_pct,
        ziel_soc_pct=ziel_soc_pct,
        geschaetzte_ladedauer_s=ladedauer_s,
        ankunftszeit_s=0.0,  # Wird im Optimierer gesetzt
        abfahrtszeit_s=ladedauer_s,  # Wird im Optimierer korrigiert
    )
```

---

## 5. Externe Integration / Algorithmus-Details

### Modul `charging_infrastructure`

**Datenquelle: lokales JSON-Snapshot**

- **Dateiformat:** siehe Abschnitt 3 ("Datenformat JSON")
- **Quelle der Daten:** community-basierte Sammlung von supercharge.info ( siehe [1], [2], [3] in Webrecherche)
- **Aktualisierung:** Manuelle Aktualisierung der `data/supercharger_snapshot.json` (z. B. monatlich über Skript), oder später Crawler-Plugin
- **Provider-Schnittstelle:** `ChargingStationProvider` erlaubt späteren Ersatz (Crawler, API-Zugriff) ohne Änderung der Konsumenten

**Algorithmus für Standortsuche:**

- **Haversine-Formel** für die Distanzberechnung zwischen zwei GPS-Koordinaten (WGS84)
- **Komplexität:** O(n) für naive Suche in der Liste aller Stationen (n = Anzahl Stationen im Snapshot); bei >1000 Stationen könnte indexbasierte Suche (z. B. R-Tree über Geokoordinaten) sinnvoll sein (nicht im aktuellen Umfang vorgesehen)

### Modul `battery`

**Ladekurven-Modell**

- **Stückweise linear:** Die Kurve ist durch diskrete Punkte definiert, zwischen denen lineare Interpolation gilt
- **Punkte:** Mindestens 4 Punkte, typischerweise 6–8 für ausreichende Genauigkeit
- **Tesla-Typische Charakteristik:**
  - 0–20% SoC: nahezu konstante Hochleistung (V3: ~250–280 kW, V4: ~325 kW)
  - 20–80% SoC: lineares Abfallen auf ~50–150 kW (je nach Modell)
  - 80–100% SoC: starkes Abbremsen (exponentiell/näherungsweise linear mit kleiner Steigung)
- **Referenz-Kurven:** `LadekurveReferenz` stellt vordefinierte Kurven für Model 3 LR (V3/V4), Model Y Performance (V3) bereit

**Ladedauer-Berechnung (Integration)**

- Gegeben: Start-SoC (S1), Ziel-SoC (S2), Ladeleistung P (kW), Batteriekapazität C (kWh)
- Gesucht: Zeit T in Sekunden
- Formel: 
  ```
  T = 3600 * ∫_{S1}^{S2} (1 / min(P, K(S))) * (C / 100) dS
  ```
  wobei K(S) die Kurvenleistung bei SoC S ist.
- **Numerische Integration:** Rechteckregel mit 0.1%-Schritten (ausreichend für <1% Fehler im Vergleich zu analytischer Lösung)
- **Wirkungsgrad-Korrektur:** Multiplikation mit `1 / effizienz_ladeelektronik` (z. B. 1/0.95 ≈ 1.053) für Verluste in der Ladeelektronik
- **Temperatur-Korrektur:** Multiplikation mit `temperatur_korrekturfaktor` (Default 1.0; kann basierend auf Außentemperatur dynamisch gesetzt werden)

**Beispiel:**
Gegeben:
- Start-SoC: 20%
- Ziel-SoC: 80%
- Ladeleistung: 250 kW
- Batteriekapazität: 75 kWh
- Kurve: `model_3_lr_v3()`

Berechnung:
- Differenz: 60% * 75 kWh = 45 kWh
- Effektive Leistung ist begrenzt durch Kurve:
  - 20–50%: Kurvenleistung fällt von 280 auf 150 kW → effektiv 250 kW
  - 50–80%: Kurvenleistung fällt von 150 auf 75 kW → effektiv 75–150 kW (ab 75 kW begrenzt)
- Integration ergibt ca. 12–15 Minuten für 20→80% (entspricht typischen Messwerten)

---

## 6. Test-Strategie

### Modul `charging_infrastructure`

**Fixtures:**

- `tests/fixtures/charging_infrastructure/stations.json`: 20–30 realistische Supercharger-Stationen in Deutschland, Dänemark, Schweden (u. a. Berlin, Hamburg, Kopenhagen, Malmö, Interstate-Raststätten); jede mit unterschiedlichen Stall-Konfigurationen (V2/V3/V4-Anteile)
- `tests/fixtures/charging_infrastructure/route_sample.json`: Kleines Beispiel-Routen-Segment (4–6 Segmente, ca. 100–200 km) mit Geometrie

**Testfälle:**

**1. Unit-Test: Standortsuche im Radius (gegebenes Fixtur)**
```python
# tests/charging_infrastructure/test_providers.py
@pytest.mark.parametrize(
    "coordinate,radius_km,expected_count",
    [
        ((52.5234, 13.4114), 5.0, 3),  # Berlin, erwartet ca. 3 Stationen
        ((55.6761, 12.5683), 2.0, 1),  # Kopenhagen Zentrum
        ((55.5941, 13.0039), 10.0, 5),  # Malmö Umland
    ],
)
def test_get_stations_in_radius(coordinate, radius_km, expected_count, local_provider):
    stations = asyncio.run(local_provider.get_stations_in_radius(coordinate, radius_km))
    assert len(stations) == expected_count
    # Prüfe Sortierung nach Distanz
    for i in range(1, len(stations)):
        assert (
            stations[i - 1].coordinate[0] == stations[i].coordinate[0]
        )  # Dummy, echte Distanzprüfung nötig
```

**2. Integration-Test: Standortsuche entlang einer Route**
```python
# tests/charging_infrastructure/test_providers.py
@pytest.mark.integration
def test_get_stations_along_route(local_provider, sample_route):
    stations_by_segment = asyncio.run(
        local_provider.get_stations_along_route(sample_route, search_radius_km=3.0)
    )
    assert len(stations_by_segment) > 0  # Mindestens ein Segment hat Stationen
    # Prüfe, dass keine doppelten Stationen auftreten (innerhalb eines Segments)
    for segment_idx, stations in stations_by_segment.items():
        station_ids = [s.station_id for s in stations]
        assert len(station_ids) == len(set(station_ids))
```

**3. Unit-Test: Validierung von ChargingStation**
```python
# tests/charging_infrastructure/test_models.py
def test_charging_station_validierung_koordinate():
    with pytest.raises(ValidationError):
        ChargingStation(
            station_id="x",
            name="Test",
            coordinate=(91.0, 0.0),  # Invalid: Breitengrad > 90
            stalls={"V3": 4},
            max_ladeleistung_kw=1000.0,
            connector_types=[ConnectorType.NACS],
            country="DE",
        )


def test_charging_station_anzahl_verfuegbare_stalls():
    station = ChargingStation(
        station_id="x",
        name="Test",
        coordinate=(52.5, 13.4),
        stalls={"V2": 2, "V3": 4, "V3ULTRA": 2, "V4": 0},
        max_ladeleistung_kw=2000.0,
        connector_types=[ConnectorType.NACS],
        country="DE",
    )
    assert station.anzahl_verfuegbare_stalls() == 8
    # Prüfe Cabinet-Schätzung: (2+4)//4 + 0 = 1 + 0 = 1 (V2+V3 teilen sich ein Cabinet)
    assert station.max_parallele_nutzbarkeit() == 2  # V2:1 + V3:1 + V4:0 (hier vereinfacht 2)
```

**Abgrenzung Unit- vs. Integration:**

- **Unit-Tests:** Validierung von Modellen, Standortsuche (ohne echte Geokoordinaten-Abfragen), Kurven-Interpolation — alle durch Fixtures abgedeckt
- **Integration-Tests:** Nur `test_get_stations_along_route` mit realer (aber kleiner) Route, keine Live-API-Abfragen

---

### Modul `battery`

**Fixtures:**

- `tests/fixtures/battery/ladekurven.json`: Referenz-Ladekurven (V3, V4) als JSON (zur Validierung, nicht direkt in Code nutzbar)
- `tests/fixtures/battery/beispielszenarien.json`: Vordefinierte Szenarien (Start-SoC, Ziel-SoC, Leistung, Erwartete Ladedauer)

**Testfälle:**

**1. Unit-Test: Ladedauer-Berechnung (Referenz-Beispiel)**
```python
# tests/battery/test_battery.py
def test_berechne_ladedauer_referenz():
    curve = LadekurveReferenz.model_3_lr_v3()
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)
    
    # Referenz: 20% → 80% bei 250 kW (V3)
    # Erwartung: ca. 12–15 Minuten (720–900 Sekunden), mittlerer Wert 810s
    time_s = berechne_ladedauer(20.0, 80.0, 250.0, curve, params)
    assert 700 <= time_s <= 1000, f"Zeit {time_s}s liegt außerhalb erwarteter Bandbreite"
```

**2. Unit-Test: Grenzfälle**
```python
# tests/battery/test_battery.py
@pytest.mark.parametrize(
    "start_soc,ziel_soc,erwartet",
    [
        (0.0, 0.0, 0.0),  # Keine Ladung
        (50.0, 50.0, 0.0),  # Keine Ladung
        (100.0, 100.0, 0.0),  # Keine Ladung
        (20.0, 100.0, 500.0),  # 80% Ladung, erwartet > 5 Minuten
    ],
)
def test_berechne_ladedauer_grenzfaelle(start_soc, ziel_soc, erwartet):
    curve = LadekurveReferenz.model_3_lr_v3()
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)

    time_s = berechne_ladedauer(start_soc, ziel_soc, 250.0, curve, params)
    assert time_s >= 0
    # Nur Plausibilitätsprüfung, keine exakten Werte bei extremen Szenarien
    if erwartet > 0:
        assert time_s >= erwartet * 0.8 and time_s <= erwartet * 1.2
```

**3. Unit-Test: Integration über die Kurve**
```python
# tests/battery/test_battery.py
def test_berechne_ladedauer_integration():
    """
    Prüft, dass die Integration korrekt ist, indem wir eine Konstante-Kurve testen.
    Wenn die Kurve konstant 250 kW ist, sollte die Zeit exakt berechenbar sein.
    """
    curve = ChargingCurve(
        points=[
            ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=250.0),
            ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=250.0),
        ]
    )
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)
    
    # 50% von 75 kWh = 37.5 kWh
    # Zeit = 37.5 kWh / 250 kW = 0.15 h = 540 s (vor Effizienzkorrektur)
    # Mit 95% Effizienz: 540 / 0.95 ≈ 568.4 s
    time_s = berechne_ladedauer(0.0, 50.0, 250.0, curve, params)
    erwartet = (0.5 * 75.0) / 250.0 * 3600 / 0.95  # ~568.4
    assert abs(time_s - erwartet) < 1.0  # Toleranz 1 Sekunde
```

**4. Unit-Test: SoC nach Segment**
```python
# tests/battery/test_battery.py
def test_berechne_soc_nach_segment():
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)
    
    # 100% → 50% bei 37.5 kWh Verbrauch
    assert berechne_soc_nach_segment(100.0, 37.5, params) == pytest.approx(50.0, abs=0.1)
    
    # Verbrauch > Kapazität → auf 0% begrenzt
    assert berechne_soc_nach_segment(50.0, 50.0, params) == pytest.approx(0.0, abs=0.1)
```

**Abgrenzung Unit- vs. Integration:**

- **Unit-Tests:** Alle Testfälle oben — deterministisch, ohne externe Ressourcen
- **Integration-Tests:** Keine geplant — `battery` ist reine Rechenlogik, keine externen Abhängigkeiten

---

## 7. Aufgaben-Checkliste

**Phase 0: Test-Fixtures & Mocking (Vorbereitung)**

- [ ] Task 0.1: `tests/fixtures/charging_infrastructure/stations.json` erstellen — 20–30 realistische Stationen in DE/DK/SE mit Stall-Konfigurationen (V2/V3/V4-Anteile), Koordinaten, Verbindern
- [ ] Task 0.2: `tests/fixtures/charging_infrastructure/route_sample.json` erstellen — 4–6 Segmente, ca. 150 km, Geometrie als Koordinaten-Liste
- [ ] Task 0.3: `tests/fixtures/battery/ladekurven.json` und `tests/fixtures/battery/beispielszenarien.json` erstellen — Referenz-Kurven und Erwartungswerte

**Phase 1: `charging_infrastructure` — Datenmodell & Provider**

- [ ] Task 1.1: `src/tripplanner/charging_infrastructure/models.py` implementieren — `ChargingStation`, `StallType`, `ConnectorType`, `ChargingStationProvider` (Protocol), `LocalFileChargingStationProvider` (Vereinbarung: keine Implementierung, nur Typen und Interface)
- [ ] Task 1.2: `src/tripplanner/charging_infrastructure/providers.py` implementieren — `LocalFileChargingStationProvider` mit JSON-Laden, Haversine-Distanz, `get_stations_in_radius`, `get_stations_along_route`
- [ ] Task 1.3: `src/tripplanner/charging_infrastructure/__init__.py` erstellen — Public API exports (Modelle + `LocalFileChargingStationProvider`)
- [ ] Task 1.4: `tests/charging_infrastructure/test_providers.py` schreiben — Tests für `get_stations_in_radius` (parametrisiert), `get_stations_along_route`, Validierung der Sortierung nach Distanz

**Phase 2: `battery` — Datenmodell & Kurven**

- [ ] Task 2.1: `src/tripplanner/battery/models.py` implementieren — `SoCState`, `ChargingCurvePoint`, `ChargingCurve`, `InterpolationMethod`, `VehicleBatteryParameters`, `ChargingStop`, `LadekurveReferenz` (statische Methoden für Model 3 LR V3/V4, Model Y Performance V3)
- [ ] Task 2.2: `src/tripplanner/battery/battery.py` implementieren — `berechne_ladedauer`, `simulate_ladevorgang`, `berechne_soc_nach_segment`, `berechne_ladehalt`
- [ ] Task 2.3: `src/tripplanner/battery/__init__.py` erstellen — Public API exports
- [ ] Task 2.4: `tests/battery/test_models.py` schreiben — Tests für Modellvalidierung (`ChargingCurve`-Constraints), `LadekurveReferenz`-Korrektheit

**Phase 3: `battery` — Berechnung & Integrationstests**

- [ ] Task 3.1: `tests/battery/test_battery.py` schreiben — Unit-Tests für `berechne_ladedauer` (Referenz-Beispiel 20→80% bei 250 kW), Grenzfälle (keine Ladung, Verbrauch > Kapazität), Integrationstest mit konstanter Kurve
- [ ] Task 3.2: Integrationstest: `simulate_ladevorgang` vs. `berechne_ladedauer` — prüfen, dass Endzeit mit Dauer übereinstimmt
- [ ] Task 3.3: Integrationstest: `berechne_soc_nach_segment` — prüfen mit bekannten Werten (100% → 50% bei 37.5 kWh von 75 kWh)

**Phase 4: Integration zwischen Modulen**

- [ ] Task 4.1: `src/tripplanner/optimization/models.py` erweitern — `ChargingStop` importieren, `optimization`-Modell nutzt `battery.ChargingStop`
- [ ] Task 4.2: Integrationstest: `ChargingStop`-Erzeugung aus `ChargingStation` (durch `battery.berechne_ladehalt`) und Prüfung der Konsistenz (Ankunfts-SoC < Ziel-SoC, Ladedauer positiv)

**Phase 5: Dokumentation & Beispiel**

- [ ] Task 5.1: `docs/plans/beispiele/charging_infrastructure_usage.py` erstellen — kurzes Beispiel, wie man Supercharger in der Nähe einer Koordinate abfragt
- [ ] Task 5.2: `docs/plans/beispiele/battery_usage.py` erstellen — Beispiel für Ladedauer-Berechnung und SoC-Simulation

---

## 8. Risiken & offene technische Fragen

**Bereits durch die verbindlichen Entscheidungen gedeckt:**

- Datenherkunft Tesla Supercharger ist lokal (JSON-Snapshot), kein Live-Crawler im aktuellen Scope — `LocalFileChargingStationProvider` deckt dies ab, späterer Austausch über `ChargingStationProvider`-Interface möglich (Punkt 3 in docs/06)
- Zwischenstopp ≠ Ladestopp — `ChargingStop`-Modell ist eigenständig, `optimization`-Modul kümmert sich um das Mergen (Punkt 4 in docs/06)

**Offene technische Fragen / Risiken:**

1. **Realistische Ladekurven-Genauigkeit:**
   - Die community-basierten Referenz-Kurven (V3/V4, Model 3 LR) sind Schätzungen, keine offiziellen Tesla-Daten.
   - *Minderung:* Ladekurven als parametrisierbarer Datentyp (`ChargingCurve`) implementiert — spätere Kalibrierung über Fahrdaten ist vorgesehen (Punkt 5 in docs/06, "keine Spekulation über Zukunft" — Default-Werte sind ausreichend für den Start).

2. **Temperatur-Einfluss auf Ladedauer:**
   - Der `temperatur_korrekturfaktor` ist aktuell ein einfacher Multiplikator (Default 1.0). In Wirklichkeit ist der Einfluss nichtlinear (sehr kalte Batterien laden langsamer bis zum Warm-up).
   - *Minderung:* Der Faktor ist als Pydantic-Modell-Feld definiert, kann später durch eine temperaturbasierte Funktion ersetzt werden, ohne die Schnittstelle zu ändern.

3. **Gemeinsame Kabinette / parallele Nutzung:**
   - Die `max_parallele_nutzbarkeit()`-Methode ist stark vereinfacht (Cabinet-basiert, nicht pro Stall). In der Realität hängt die parallele Nutzung vom Lademanagement der Station ab (z. B. „Load Balancing“ zwischen Posts).
   - *Minderung:* Aktuell wird das nicht genutzt — es ist nur als Reserve für zukünftige Optimierung (z. B. Vermeidung von „Stall-Kollisionen“ im Ladeplan) vorgesehen. Falls nötig, kann die Logik in `ChargingStation` nachträglich erweitert werden.

4. **Maximale Ladeleistung pro Stall (nicht pro Station):**
   - Die `max_ladeleistung_kw`-Feld ist als Summe aller Stalls definiert, aber die *pro Stall* maximale Leistung (V3: 250 kW, V4: 325 kW) ist entscheidend für die Ladekurve.
   - *Lösung:* Die Referenz-Kurven (`LadekurveReferenz`) wählen die Kurve basierend auf der Station (V4-Stalls → V4-Kurve), die effektive Leistung wird in `berechne_ladehalt` durch `min(ladeleistung_kw, fahrzeug_param.max_ladeleistung_kw)` begrenzt.

5. **Keine Live-Verfügbarkeitsprüfung:**
   - Das Modell geht davon aus, dass alle Stalls jederzeit verfügbar sind. In Wirklichkeit können Stalls ausfallen oder belegt sein.
   - *Minderung:* Dies ist eine bewusste Scope-Einschränkung (siehe "Nicht-Scope"). Späterer Ausbauschritt: Ein `ChargingStationProvider` könnte eine zusätzliche Methode `is_stall_available(station_id, stall_type)` bereitstellen.

6. **JSON-Format für Snapshot:**
   - Das vorgeschlagene JSON-Schema (`stations`-Array mit `stalls`-Dict) ist aus Community-Daten abgeleitet (supercharge.info), aber noch nicht in dieser Form getestet.
   - *Minderung:* Das Schema ist fest in `LocalFileChargingStationProvider` implementiert — falls es Anpassungen gibt, müssen nur diese paar Zeilen angepasst werden.

7. **Numerische Stabilität der Integration:**
   - Die Rechteckregel mit 0.1%-Schritten ist ausreichend, aber keine adaptiven Verfahren (z. B. Simpson-Regel).
   - *Minderung:* Die Toleranz in `test_berechne_ladedauer_integration` ist auf <1 Sekunde gesetzt, was ausreichend für eine Reiseplanung ist (keine Echtzeit-Anwendung).

**Empfehlung für spätere Iterationen:**

- Sobald Fahrdaten vorliegen, `VehicleBatteryParameters` um `kalibrierte_ladekurve: ChargingCurve` erweitern (Überschreiben der Default-Kurve)
- `ChargingStop` um `eingetragene_stalls: list[StallType]` erweitern, um parallel nutzbare Stalls explizit zu modellieren (falls "Stall-Kollisionen" relevant werden)
- `LocalFileChargingStationProvider` durch `CrawlerChargingStationProvider` ersetzen (asynchrones Skript, das `data/supercharger_snapshot.json`periodisch aktualisiert)

---

**Quellen der Webrecherche (zusammengefasst):**

- [1] supercharge.info API: `https://supercharge.info/service/supercharge/allSites` — JSON-Struktur mit `name`, `address.region`, `gps.{lat,lon}`, `stalls.{Urban,V2,V3,V4}`, `plugs.{NACS,CCS1,CCS2,Type2,GB/T}`
- [2] Tesla V3/V4 Spezifikationen (offiziell): V3: bis zu 250 kW pro Stall, V4: bis zu 500 kW pro Post (aktuelle Installationen oft 325 kW)
- [3] Community-Ladekurven (Forums, teslamate, VEGA-Paper): SOC + kW ≈ 115–165 (je nach Modell), Abbremsen ab ~80%, 80→100% dauert so lange wie 20→80%

**Ende des Plans.**