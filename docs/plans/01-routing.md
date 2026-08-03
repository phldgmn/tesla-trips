# Implementierungsplan: `routing`-Modul (Phase 1)

## 1. Zweck & Scope

Das `routing`-Modul berechnet eine oder mehrere Straßenrouten zwischen Start, Ziel und gegebenenfalls Zwischenstopps (Pflicht-Wegpunkten) mithilfe von GraphHopper. Es kennt **nicht** die Energieverbrauchsdaten, Ladeplanung oder Wetterbedingungen — diese werden erst in nachgelagerten Modulen (`energy`, `optimization`) bearbeitet.

**Scope:**
- HTTP-Client für GraphHopper-Server mit Docker-Setup (lokale Instanz)
- Berechnung einer einzigen Route für gegebene Waypoints (Start → Zwischenstopps → Ziel)
- Extrahieren aller für nachgelagerte Module relevanten Segmentinformationen: Geometrie, Länge, Straßenklasse, Tempolimit, Steigung (sofern verfügbar)
- Behandlung von Zwischenstopps als Pflicht-Wegpunkte, die in der Reihenfolge durchlaufen werden müssen

**Nicht-Scope:**
- Keine eigene OSM-Datenverarbeitung — ausschließlich GraphHopper als Datenquelle nutzen
- Keine Energierouting-Optimierung (keine Berücksichtigung von Steigungen für Kostenfunktion im aktuellen Scope)
- Keine mehreren Routenalternativen mit energetischem Vergleich (Später / nicht jetzt umsetzen, siehe "Offene Punkte" in `06-offene-punkte-widersprueche.md`)
- Keine Live-Verkehrsdaten (explizit nicht Teil des Projekts)

## 2. Abhängigkeiten & Phasenzuordnung

**Phase:** Phase 1 (unabhängige Datenquell-Module, parallelisierbar)

**Fremde Typen (nur Lesen, exakt wie im Register definiert):**
- `tripplanner.trip_input.models.TripRequest` (Startkoordinate, Zielkoordinate, zwischenstopps: list[Waypoint], abfahrtszeit, fahrzeugprofil: VehicleProfile, praeferenzen)
- `tripplanner.trip_input.models.Waypoint` (koordinate, aufenthaltsdauer: timedelta | None)
- `tripplanner.routing.models.Route` (Segmentsammlung)
- `tripplanner.routing.models.RouteSegment` (segment_index, geometrie/koordinaten, laenge_m, strassenklasse, oberflaeche, tempolimit_kmh, steigung_rohdaten, bearing_deg)

**Hinweis zum Fahrzeugprofil:** Der `VehicleProfile` aus `trip_input` wird aktuell **nicht** zur Beeinflussung des GraphHopper-Routings verwendet, da GraphHopper sein eigenes Fahrzeugprofil (`profile`) erfordert und die individuellen Tesla-spezifischen Parameter erst in Phase 2 (`energy`) wirksam werden. Zukünftig könnte das Fahrzeugprofil via `custom_model` eingebunden werden, ist aber aktuell bewusst nicht umgesetzt (keine Steigungsbestrafung im Routing selbst — nur Tempolimit-Gewichtung).

## 3. Datenmodelle

```python
# src/tripplanner/routing/models.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Tuple
from datetime import timedelta

# Repräsentiert eine Koordinate (Breitengrad, Längengrad)
Coordinate = Tuple[float, float]  # (lat, lon)


class RouteSegment(BaseModel):
    """Ein Segment der Route mit allen für nachgelagerte Module relevanten Attributen."""
    segment_index: int = Field(..., description="Nullbasierter Index dieses Segments in der Route")
    geometrie: List[Coordinate] = Field(..., description="Liste von (lat, lon) Koordinaten, die das Segment beschreiben")
    laenge_m: float = Field(..., gt=0, description="Länge des Segments in Metern")
    strassenklasse: str = Field(..., description="Straßenklasse (MOTORWAY, TRUNK, PRIMARY, SECONDARY, TRACK, etc.)")
    oberflaeche: str | None = Field(default=None, description="Straßenbelag aus GraphHopper Path-Detail `surface` (z. B. asphalt, gravel, dirt), None wenn nicht verfügbar. Wird von `energy` für den Rollwiderstands-Faktor konsumiert (siehe docs/plans/06-energy.md, Abschnitt 5.1.1).")
    tempolimit_kmh: int | None = Field(default=None, ge=0, description="Tempolimit in km/h (None wenn nicht verfügbar)")
    steigung_rohdaten: float | None = Field(default=None, ge=-100, le=100, description="Steigung in Prozent (None wenn nicht verfügbar)")
    bearing_deg: float = Field(..., ge=0.0, lt=360.0, description="Fahrtrichtung (Bearing) am Segmentanfang in Grad (0°=Nord, 90°=Ost), vom routing-Modul aus Start-/Endkoordinate des Segments berechnet (Vorwärtsazimut, WGS84-Großkreis). Wird von `wind` zur Windkomponenten-Projektion konsumiert.")


class Route(BaseModel):
    """Die gesamte berechnete Route mit Metadaten."""
    segments: List[RouteSegment] = Field(..., description="Liste aller Route-Segmente in Fahrtrichtung")
    gesamtlaenge_m: float = Field(..., gt=0, description="Gesamtlänge der Route in Metern")
    geometrie: List[Coordinate] = Field(..., description="Vollständige Geometrie der Route als Liste von Koordinaten")
    bbox: Tuple[float, float, float, float] | None = Field(default=None, description="Bounding box [min_lat, min_lon, max_lat, max_lon] (optional)")


class GraphHopperResponse(BaseModel):
    """Interne Darstellung einer GraphHopper /route API Antwort (nur zur internen Verarbeitung)."""
    paths: List[GraphHopperPath]
    info: GraphHopperInfo


class GraphHopperPath(BaseModel):
    """Ein Pfad (in der Regel nur einer) aus der GraphHopper Antwort."""
    distance: float  # Meter
    time: int  # Millisekunden
    points: str  # Encodierte Polyline (points_encoded=True)
    points_encoded: bool = True
    details: dict[str, list[str | float]] = Field(default_factory=dict)  # Details wie road_class, max_speed, average_slope
    instructions: list = Field(default_factory=list)


class GraphHopperInfo(BaseModel):
    """Meta-Informationen zur GraphHopper Antwort."""
    copyright: list[str]
    hints: list[dict] = Field(default_factory=list)
    took: int  # Millisekunden
```

**Zusätzliche Hilfstypen (nicht im Register enthalten, aber notwendig):**
- `Coordinate`: Tuple[float, float] — (Breitengrad, Längengrad). **Konsolidierungshinweis:** Dieser Alias ist identisch mit dem in `docs/plans/00-foundation-tooling.md` vorgesehenen `tripplanner.geo.Coordinate`-Primitiv. `routing` definiert ihn hier lokal, da `routing` das erste Modul in der Pipeline ist; sobald `tripplanner.geo` in Phase 0 existiert, importiert `routing.models` von dort statt lokal neu zu definieren (kein funktionaler Unterschied, nur eine Quelle der Wahrheit).
- `GraphHopperResponse`/`GraphHopperPath` — Nur zur internen Verarbeitung, keine Cross-Modul-Schnittstelle
- **Koordinaten-Konvention (verbindlich für das gesamte Projekt):** Alle `Coordinate`-Tupel sind `(lat, lon)`, niemals `(lon, lat)`. Eine Umwandlung nach GeoJSON-Reihenfolge `(lon, lat)` erfolgt ausschließlich an der Serialisierungsgrenze zum Frontend (siehe `docs/plans/08-simulation-visualization-api.md`, Abschnitt 5.2), nicht in Domänenmodellen.

## 4. Öffentliche Schnittstelle

```python
# src/tripplanner/routing/__init__.py
from .models import Route, RouteSegment, Coordinate, GraphHopperResponse, GraphHopperPath
from .providers import RoutingProvider, FakeRoutingProvider
from .client import GraphHopperClient

__all__ = [
    "Route",
    "RouteSegment",
    "Coordinate",
    "GraphHopperResponse",
    "GraphHopperPath",
    "RoutingProvider",
    "FakeRoutingProvider",
    "GraphHopperClient",
]
```

```python
# src/tripplanner/routing/providers.py
from abc import ABC, abstractmethod
from typing import Protocol
from tripplanner.trip_input.models import TripRequest
from tripplanner.routing.models import Route


class RoutingProvider(Protocol):
    """Interface für Routing-Anbieter. Ermöglicht Fake-Implementierungen für Tests."""

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Berechnet eine Route für die gegebene TripRequest."""
        ...

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        ziel: Coordinate,
        zwischenstopps: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Berechnet eine Route mit expliziten Zwischenstopps."""
        ...


class GraphHopperRoutingProvider:
    """Konkrete Implementierung über GraphHopper HTTP API."""

    def __init__(self, client: GraphHopperClient, use_custom_model: bool = False):
        self.client = client
        self.use_custom_model = use_custom_model

    async def berechne_route(self, anfrage: TripRequest) -> Route:
        """Berechnet eine Route für eine TripRequest (inkl. Zwischenstopps)."""
        # Umwandlung TripRequest → GraphHopper Parameter
        # Anruf self.client.route(...)
        # Mapping GraphHopperResponse → Route
        ...

    async def berechne_route_mit_waypoints(
        self,
        start: Coordinate,
        ziel: Coordinate,
        zwischenstopps: list[tuple[Coordinate, timedelta | None]],
    ) -> Route:
        """Berechnet eine Route mit Zwischenstopps über GraphHopper."""
        # Aufruf client.route mit points=[start] + [zwischenstopps] + [ziel]
        ...
```

```python
# src/tripplanner/routing/client.py
from typing import List, Tuple
from httpx import AsyncClient
from tripplanner.routing.models import GraphHopperResponse, GraphHopperPath

Coordinate = Tuple[float, float]


class GraphHopperClient:
    """HTTP-Client für GraphHopper API. Handles Authentifizierung, Request/Response Mapping."""

    def __init__(self, base_url: str = "http://localhost:8989", api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = AsyncClient(base_url=base_url, timeout=60.0)

    async def route(
        self,
        points: List[Coordinate],
        profile: str = "car",
        elevation: bool = False,
        details: List[str] | None = None,
        custom_model: dict | None = None,
    ) -> GraphHopperResponse:
        """
        GraphHopper /route HTTP Endpoint.

        Args:
            points: Liste von [lon, lat] Koordinaten (mindestens 2)
            profile: GraphHopper profile (z. B. "car", "bike", "foot", oder benutzerdefiniert)
            elevation: Falls True, Elevation in Polyline einbeziehen
            details: Liste von gewünschten Path Details (z. B. ["road_class", "max_speed", "average_slope", "surface"])
            custom_model: Optionaler custom_model JSON für individuelles Fahrzeugprofil

        Returns:
            GraphHopperResponse mit decoded Polyline und Details

        Raises:
            ValueError: Wenn weniger als 2 Punkte übergeben werden
            httpx.HTTPStatusError: Bei HTTP-Fehlern (4xx/5xx)
        """
        # Umwandlung points: (lat, lon) → [lon, lat]
        gh_points = [[lon, lat] for lat, lon in points]
        payload = {"point": gh_points, "profile": profile, "elevation": elevation}

        if details:
            payload["details"] = details

        if custom_model:
            payload["custom_model"] = custom_model

        response = await self._client.post("/route", json=payload)
        response.raise_for_status()

        return GraphHopperResponse.model_validate(response.json())

    async def close(self) -> None:
        """Schließt den HTTP Client."""
        await self._client.aclose()

    async def __aenter__(self) -> "GraphHopperClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
```

## 5. Externe Integration / Algorithmus-Details

### GraphHopper Docker Setup

**Version:** 11.0 (aktuelle Stable Release, 14. Oktober 2025)

**Docker-Image:** `israelhikingmap/graphhopper` oder `harelmazor/graphhopper` (beide aktuell gepflegt)

**Docker Compose Beispiel (für lokalen Development-Server):**

```yaml
version: "3.8"
services:
  graphhopper:
    image: israelhikingmap/graphhopper:11.0
    container_name: tesla-trips-graphhopper
    ports:
      - "8989:8989"
    volumes:
      - ./data:/data
    environment:
      - GRAPHHOPPER_JAVA_OPTS=-Xms1g -Xmx4g
    restart: unless-stopped
```

**OSM-Datenquelle:** Geofabrik-Extrakte
- Deutschland: `germany-latest.osm.pbf` (~4.5 GB)
- Dänemark: `denmark-latest.osm.pbf` (aus `europe/denmark.html`)
- Schweden: `sweden-latest.osm.pbf` (~772 MB)

**Extrakt-Methode für DE/DK/SE:**

```bash
# Option 1: Einzelne Länder herunterladen
wget https://download.geofabrik.de/europe/germany-latest.osm.pbf
wget https://download.geofabrik.de/europe/denmark-latest.osm.pbf
wget https://download.geofabrik.de/europe/sweden-latest.osm.pbf

# Option 2: Europe-Gesamtextrakt + osmconvert für Bereichsausschnitt
# Bounding Box: west= -10, south= 47, east= 34, north= 71 (ungefähr DE/DK/SE)
wget https://download.geofabrik.de/europe-latest.osm.pbf
osmconvert europe-latest.osm.pbf -b=-10,47,34,71 -o=de-dk-se.osm.pbf
```

**GraphHopper mit Custom Model:**

```yaml
# In config.yml des GraphHopper-Containers
profiles:
  - name: tesla_model3
    vehicle: car
    custom_model_files: [tesla_model3.json]

custom_models.directory: /data/models
```

**Tesla Model 3 Custom Model JSON (`tesla_model3.json`):**

```json
{
  "speed": [
    {
      "if": "road_class == MOTORWAY",
      "limit_to": 130
    },
    {
      "if": "true",
      "limit_to": 100
    }
  ],
  "priority": [
    {
      "if": "road_class == MOTORWAY",
      "multiply_by": 1.0
    }
  ],
  "distance_influence": 0
}
```

*Begründung:*
- Tempolimits anpassen: Auf Autobahnen in DE/DK/SE typische 130 km/h statt Default (meist 120 km/h für car), in Städten auf 100 km/h beschränken.
- Keine Motorway-Avoidance — Tesla Model 3 darf Autobahnen nutzen.
- `distance_influence: 0` → bevorzugt schnellste Route (kein Zwang zu kürzeren Wegen bei gleicher Fahrzeit).
- Steigungen: Aktuell **nicht** über `custom_model` einbeziehen — erst in Phase 2 (`energy`) durch Recherche der Steigungen anhand Elevation.

### GraphHopper `/route` HTTP API Parameter

**Endpoint:** `POST /route`

**Relevante Parameter (für dieses Modul):**

| Parameter | Typ | Obligatorisch | Beschreibung |
|-----------|-----|---------------|--------------|
| `point` | array[lon, lat] | Ja | Mindestens 2 Koordinaten (Start, [Zwischenstopps], Ziel) |
| `profile` | string | Ja | GraphHopper Profilname (z. B. "car", "tesla_model3") |
| `elevation` | boolean | Nein | Falls `true`, Elevation in Polyline inkludieren (für Steigungsberechnung) |
| `points_encoded` | boolean | Nein | Standard: `true` (Poliyline-Encode), `false` → GeoJSON |
| `details` | array[string] | Nein | Gewünschte Path Details: `["road_class", "max_speed", "average_slope", "max_slope", "surface"]` |

**Relevante Path Details (für nachgelagerte Module):**

| Detail | Typ | Beschreibung |
|--------|-----|--------------|
| `road_class` | string | MOTORWAY, TRUNK, PRIMARY, SECONDARY, TRACK, STEPS, CYCLEWAY, FOOTWAY, OTHER |
| `max_speed` | number | Tempolimit in km/h (0 = kein Limit, -1 = nicht verfügbar) |
| `average_slope` | number | durchschnittliche Steigung in Prozent (100 * Δh / d) |
| `max_slope` | number | maximale Steigung im Segment in Prozent (längs des Segments) |
| `surface` | string | PAVED, GRAVEL, DIRT, GRASS, etc. (für spätere Rollwiderstandsberechnung) |

**Beispiel-Request (Python/httpx):**

```python
payload = {
    "point": [[lon1, lat1], [lon2, lat2], [lon3, lat3]],  # [lon, lat] Reihenfolge!
    "profile": "car",
    "elevation": True,
    "details": ["road_class", "max_speed", "average_slope", "surface"]
}
response = await client.post("/route", json=payload)
```

**Beispiel-Response-Ausschnitt (path details):**

```json
{
  "paths": [{
    "distance": 12345.678,
    "time": 543210,
    "points_encoded": true,
    "points": "o}`jH...",
    "details": {
      "road_class": ["PRIMARY", "SECONDARY", "TRACK"],
      "max_speed": [100, 80, 50],
      "average_slope": [1.2, -0.5, 3.8]
    }
  }]
}
```

### Steigungsberechnung aus GraphHopper Details

- **`average_slope`** ist bereits in Prozent geliefert (signed decimal), benötigt keine weitere Berechnung.
- **`max_slope`** ist die maximale Steigung innerhalb des Segments (wichtiger für Rekuperation).
- Falls `elevation: true` gesetzt ist, kann auch die Roh-Polyline mit Höheninformationen abgerufen werden (für spätere präzisere Berechnung).

## 6. Test-Strategie

### Unit Tests (Mock/Fake, ohne GraphHopper Server)

**Fixtures:**
- `tests/fixtures/routing/graphhopper_response_basic.json`: Minimale Antwort ohne Details
- `tests/fixtures/routing/graphhopper_response_with_details.json`: Antwort mit `road_class`, `max_speed`, `average_slope`, `surface` details
- `tests/fixtures/routing/expected_route_model.json`: Erwartetes Pydantic-`Route`-Objekt
- `tests/fixtures/routing/waypoints_testcase.json`: Beispiel `TripRequest` mit Zwischenstopps

**Testfälle:**

1. **Given:** Gültige `TripRequest` ohne Zwischenstopps  
   **When:** `berechne_route()` wird aufgerufen  
   **Then:** `Route`-Objekt wird zurückgegeben, `gesamtlaenge_m` > 0, `segments` ≥ 1, `tempolimit_kmh` und `strassenklasse` sind gesetzt (oder `None` falls nicht verfügbar)

2. **Given:** `TripRequest` mit 2 Zwischenstopps  
   **When:** Route wird berechnet  
   **Then:** Route enthält Segmente in korrekter Reihenfolge Start → Stopp1 → Stopp2 → Ziel, GesamtlängeSumme der Segmentlängen (innerhalb Toleranz 1%)

3. **Given:** GraphHopper-Antwort mit `average_slope` details  
   **When:** `RouteSegment` wird extrahiert  
   **Then:** `steigung_rohdaten` ist korrekt gesetzt (oder `None` falls `average_slope` fehlt), `tempolimit_kmh` ≥ 0 oder `None`, `strassenklasse` ist ein gültiger Wert aus `["MOTORWAY", "TRUNK", "PRIMARY", "SECONDARY", "TRACK", "OTHER"]`

### Integration Tests (gegen lokalen GraphHopper Server)

**Fixture:** Lokaler GraphHopper-Container (via `pytest-docker` oder manueller Start) mit `germany-latest.osm.pbf`

**Testfälle:**

1. **Given:** Start/Ende in Deutschland (z. B. Berlin → Hamburg)  
   **When:** `GraphHopperClient.route()` wird aufgerufen  
   **Then:** HTTP Status 200, Antwort enthält gültige Polyline, `distance` ≈ 250 km (innerhalb Toleranz ±5 km)

2. **Given:** Route mit Zwischenstopp in Dänemark (Berlin → Kopenhagen → Malmö)  
   **When:** Route berechnet  
   **Then:** Route enthält mind. 3 Segmente (Start→Stopp, Stopp→Ziel), `gesamtlaenge_m` ≈ 750 km (innerhalb Toleranz)

**Markierung:** `@pytest.mark.integration`

**Test-Datei-Struktur:**

```
tests/routing/
├── test_routing.py          # Unit Tests (fake provider)
├── test_providers.py        # Integration Tests (gegen GraphHopper)
└── conftest.py              # Fixture: graphhopper_client, example_trip_request
```

## 7. Aufgaben-Checkliste

- [ ] **Task 1:** Erstelle Modul-Skeleton (`src/tripplanner/routing/`, `tests/routing/`, `docs/plans/01-routing.md` existiert bereits). Erstelle `pyproject.toml`-Einträge für `rasterio`-Abhängigkeit nicht nötig, da `rasterio` nur `elevation`-Modul benötigt. Füge `httpx` hinzu (bereits im Root-`pyproject.toml` enthalten).

- [ ] **Task 2:** Implementiere `src/tripplanner/routing/models.py` mit `RouteSegment`, `Route`, `GraphHopperResponse`, `GraphHopperPath`. Definiere `Coordinate = Tuple[float, float]`. Füge Validatoren hinzu (`gt=0` für Längen, `ge=-100, le=100` für Steigung).

- [ ] **Task 3:** Implementiere `src/tripplanner/routing/client.py` mit `GraphHopperClient.route()`. Implementiere Request-Mapping (Python `Coordinate` → GraphHopper `[lon, lat]`), Parameterübergabe (`elevation`, `details`), Response-Parsing (`GraphHopperResponse.model_validate(response.json())`). Implementiere Context-Manager (`__aenter__`/`__aexit__`).

- [ ] **Task 4:** Implementiere `src/tripplanner/routing/providers.py` mit `RoutingProvider` Protocol und `GraphHopperRoutingProvider`. Implementiere `berechne_route()` (Umwandlung `TripRequest` → `GraphHopperClient.route()` mit points, profile="car", elevation=True, details=["road_class","max_speed","average_slope","surface"]). Implementiere `berechne_route_mit_waypoints()` (explizite Waypoint-Liste).

- [ ] **Task 5:** Implementiere Mapping-Logik zwischen `GraphHopperPath` und `Route`. Dekodiere Polyline (GraphHopper `points` → Liste `Coordinate`), extrahiere `details` in Segment-Attribute (`road_class` → `strassenklasse`, `max_speed` → `tempolimit_kmh`, `average_slope` → `steigung_rohdaten`, `surface` → `oberflaeche`). Generiere `Route.gesamtlaenge_m = sum(s.laenge_m for s in segments)`.

- [ ] **Task 6:** Erstelle Unit Tests in `tests/routing/test_routing.py`. Teste Mapping von `GraphHopperResponse` → `Route`. Teste Umgang mit fehlenden Details (`details` leer → `tempolimit_kmh=None`, `steigung_rohdaten=None`).

- [ ] **Task 7:** Erstelle Integration Tests in `tests/routing/test_providers.py`. Teste `GraphHopperClient.route()` gegen lokalen GraphHopper-Server (Docker-Container). Teste `GraphHopperRoutingProvider.berechne_route()` mit `TripRequest` (Berlin → Hamburg).

- [ ] **Task 8:** Erstelle Fixtures: `tests/fixtures/routing/graphhopper_response_basic.json`, `tests/fixtures/routing/graphhopper_response_with_details.json`, `tests/fixtures/routing/expected_route_model.json`, `tests/fixtures/routing/waypoints_testcase.json`. Nutze echte GraphHopper-Antworten aus Docker-Test oder synthetische Beispiele.

- [ ] **Task 9:** Schreibe Docstrings für alle öffentlichen Funktionen gemäß Google-Style (httpx, pydantic, ruff-D-Regeln). Beispiel: `GraphHopperClient.route()`: Args/Returns/Raises gemäß Schema im Plan beschrieben.

- [ ] **Task 10:** Implementiere `src/tripplanner/routing/__init__.py` mit Export von `Route`, `RouteSegment`, `Coordinate`, `GraphHopperResponse`, `GraphHopperPath`, `RoutingProvider`, `FakeRoutingProvider`, `GraphHopperClient`.

- [ ] **Task 11:** Erstelle `docker-compose.yml` für lokalen GraphHopper (Version 11.0, Volume `./data:/data`, Ports `8989:8989`, JVM-Options `-Xms1g -Xmx4g`). Schreibe README-Snippet für Start: `docker-compose up -d`, warten auf Log "GraphHopper is starting..." (ca. 2–5 Minuten).

- [ ] **Task 12:** Implementiere `FakeRoutingProvider` (für Unit Tests ohne GraphHopper). Führe Dummy-`Route` zurück, die `RouteSegment` mit synthetischen Daten enthält (Längen ≈ 100 km, `tempolimit_kmh=100`, `strassenklasse="PRIMARY"`, `oberflaeche="asphalt"`, `steigung_rohdaten=1.5`).

- [ ] **Task 13:** Führe `ruff check src/tripplanner/routing/ tests/routing/` aus (select E,F,I,UP,B,SIM,PL,RUF) und korrigiere alle Meldungen. Führe `mypy src/tripplanner/routing/` mit `--strict` aus und korrigiere Typprüfungsfehler (vollständige Typannotationen, keine `Any`-Fallbacks).

- [ ] **Task 14:** Schreibe `docs/plans/01-routing.md` vollständig (dieser Plan). Prüfe, ob alle Abschnitte (Zweck & Scope, Abhängigkeiten, Datenmodelle, öffentliche Schnittstelle, Externe Integration, Test-Strategie, Aufgaben-Checkliste, Risiken) enthalten sind und keine "TBD"-Placehalter stehen.

- [ ] **Task 15:** Ergänze `RouteSegment.bearing_deg`-Berechnung in `providers.py`/`routing.py`: Vorwärtsazimut aus erster und letzter Koordinate von `segment.geometrie` (Formel: `atan2(sin(Δlon)·cos(lat2), cos(lat1)·sin(lat2) − sin(lat1)·cos(lat2)·cos(Δlon))`, normalisiert auf `[0, 360)`). Unit-Test mit bekannten Himmelsrichtungen (Nord/Ost/Süd/West).

## 8. Risiken & offene technische Fragen

1. **Polyline-Dekodierung:** GraphHopper nutzt die gleiche Polyline-Encodierung wie Google Maps (Encoded Polyline Algorithm). Verwendung einer etablierten Bibliothek (` polyline` PyPI-Paket) ist empfohlen. Falls nicht verfügbar, Implementierung der Dekodierung gemäß offiziellem Algorithmus.

2. **Grenzfälle mit `max_speed`:** GraphHopper liefert `max_speed: 0` für Straßen ohne Schild (z. B. Spielstraßen in DE) oder `-1` falls nicht bekannt. Das `routing`-Modul muss diese Werte entweder als `None` (kein Limit) oder als typische Default-Geschwindigkeit interpretieren (entscheidet `energy`-Modul später für die Berechnung). Im `routing`-Modul wird `0` oder `-1` als `tempolimit_kmh=None` gespeichert.

3. **Elevation-Details ohne Elevation-Daten:** Falls GraphHopper mit `elevation: true` gestartet wurde, aber keine DEM-Daten für die Route verfügbar sind, liefert `average_slope` möglicherweise `null` oder `0`. Im `routing`-Modul wird dies als `steigung_rohdaten=None` behandelt.

4. **Mehrfachrouten in GraphHopper Antwort:** Die Antwort kann mehrere Pfade enthalten (bei `alt=true` Parameter). Das `routing`-Modul nutzt aktuell **nur den ersten Pfad** (`paths[0]`). Falls Mehrfachrouten gewünscht sind (Später / nicht jetzt), muss das Modul erweitert werden.

5. **Stauprognosen:** GraphHopper kann live Verkehr berücksichtigen (via `weighting=shortest` mit `traffic=true`). Ist aktuell nicht vorgesehen (Verkehr ist "nicht Bestandteil dieses Projekts"), daher wird `weighting=fastest` ohne Verkehrsdaten verwendet.

6. **Tempolimit-Interpolation:** Falls `max_speed` nur segmentweise vorliegt (Pro Edge), aber `RouteSegment` aus mehreren Edges besteht (bei langen Straßenabschnitten), kann die Durchschnittsgeschwindigkeit berechnet werden. Für den aktuellen Scope wird das erste oder durchschnittliche `max_speed` des Segments verwendet.

7. **Cross-Border-Routing (DE/DK/SE):** GraphHopper unterstützt Cross-Border-Routing out-of-the-box, solange die OSM-Daten zusammenhängend sind (Deutschland, Dänemark, Schweden sind im Europe-Extrakt enthalten). Keine额外 Handlung notwendig.

8. **Fehlende Straßenklassen:** Falls eine Straße keine `road_class` hat (z. B. private Zufahrten), liefert GraphHopper `"OTHER"`. Das Modul akzeptiert diesen Wert.

9. **GraphHopper-Container-Startzeit:** Der erste Start nach `docker-compose up` kann 2–10 Minuten dauern (OSM-Import). In CI/CD-Pipelines muss eine Warte-Logik (Polling auf `/health` Endpoint) implementiert werden.

10. **Reproducibility:** GraphHopper nutzt intern eine Graph-Cache (`/data/graph-cache`). Für reproduzierbare Tests (selbe OSM-Datei → selbe Route) ist sicherzustellen, dass keine externen Changes (z. B. Waze-Traffic-Updates) erfolgen. In der Praxis ist die Route für dieselbe OSM-Datei reproduzierbar.