# Implementierungsplan: `elevation`-Modul (Phase 1)

---

## 1. Zweck & Scope

**Zweck:** Das `elevation`-Modul extrahiert das Höhenprofil entlang einer gegebenen Route und berechnet daraus das Steigungs-/Gefälliprofil je Segment.

**Konkrete Leistungen:**
- Höhenwert (in Metern) pro Routenpunkt (aus DEM-Kacheln)
- Steigung in Prozent pro Segment berechnet aus Höhendifferenz und horizontaler Distanz
- Handling von Kachelgrenzen (automatische Kombination mehrerer DEM-Tiles)
- Umgang mit ungültigen Höhenwerten (z. B. Meeresgebiete → 0 m)

**Abgrenzung zu anderen Modulen:**
- Keine Wetter- oder Baustellen-Integration: Nur reine Geometrie/Höhe
- Keine Energieberechnung: Das Modul liefert Rohdaten (Höhe, Steigung), die von `energy` verwendet werden
- Kein Rerouting: Routenpunkte stammen aus `routing`, das Modul verändert die Route nicht

**Explizit NICHT-Scope:**
- DEM-Daten herunterladen/cachen (vorgelagerte Datenpipeline, siehe Abschnitt 5)
- Live-Aktualisierung des DEM-Stacks (nur statischer Datenbestand)
- Interpolation zwischen Routenpunkten (nur Punktwerte, keine Zwischenwerte)
- UTM-Zonengrenzen überschreitende Berechnung (Annahme: Europa-West, einheitliche UTM-Zone oder WGS84->UTM-Transformation vor dem Aufruf)

---

## 2. Abhängigkeiten & Phasenzuordnung

**Phase:** Phase 1 (Datenquell-Module)

**Abhängigkeiten zu anderen Modulen (konsumierte models.py-Typen):**
- `tripplanner.routing.models.Route`: Konsumiert `Route.segments`, um Koordinaten zu extrahieren
- `tripplanner.routing.models.RouteSegment`: Verwendet `RouteSegment.geometrie` für Koordinaten
- Keine importierten Implementierungsdetails — nur `tripplanner.routing.models` importieren

**Konsumierte externen Datenquellen:**
- Copernicus DEM GLO-30 (Cloud Optimized GeoTIFFs, EPSG:4326 WGS84 oder UTM-Zonen)
- Lokale DEM-Kacheln (entweder im Repo oder im `data/elevation/`-Verzeichnis)

**Erzeugte Datenmodelle für andere Module:**
- `tripplanner.elevation.models.ElevationPoint` (im Register definiert)
- `tripplanner.elevation.models.SegmentGradient` (im Register definiert)

**Phasen-Abhängigkeit:** Das Modul ist unabhängig von Phase 1-Modulen (`routing`, `weather`, `construction`, `charging_infrastructure`) — es verarbeitet die Routen-Geometrie aus `routing.models`, benötigt aber keine anderen Daten. Es ist VORAUSSETZUNG für `wind` (benötigt Höhenprofilliste) und `energy` (benötigt `SegmentGradient`).

---

## 3. Datenmodelle

Alle Pydantic-Modelle in `src/tripplanner/elevation/models.py`:

```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Tuple
from datetime import datetime
from geographiclib.geodesic import Geodesic


class ElevationPoint(BaseModel):
    """Höhenwert an einer Koordinate."""
    koordinate: Tuple[float, float] = Field(
        description="Breitengrad, Längengrad (WGS84, Grad)"
    )
    hoehe_m: float = Field(
        ge=-100, le=9000,
        description="Höhe über NN in Metern (ungültige Werte: -9999 → nicht belegt)"
    )
    
    @field_validator("koordinate")
    @classmethod
    def validate_koordinate(cls, v: Tuple[float, float]) -> Tuple[float, float]:
        lat, lon = v
        if not (-90 <= lat <= 90):
            raise ValueError("Breitengrad muss zwischen -90 und 90 Grad liegen")
        if not (-180 <= lon <= 180):
            raise ValueError("Längengrad muss zwischen -180 und 180 Grad liegen")
        return v


class SegmentGradient(BaseModel):
    """Steigung/Gefälle je Segment aus Höhendifferenz und horizontaler Distanz."""
    segment_index: int = Field(ge=0, description="Index des RouteSegments (0-basiert)")
    steigung_prozent: float = Field(
        description="Steigung in Prozent (positive = Steigung, negativ = Gefälle)"
    )
    hoehendifferenz_m: float = Field(
        description="Höhendifferenz zwischen Start- und Endpunkt des Segments in Metern"
    )
    horizontale_distanz_m: float = Field(
        description="Horizontale Distanz (nicht entlang der Route, sondern Luftlinienprojektion) in Metern"
    )
    
    @field_validator("steigung_prozent", "hoehendifferenz_m", "horizontale_distanz_m")
    @classmethod
    def validate_values(cls, v: float) -> float:
        if v < 0 and "steigung" not in cls.__name__:
            # hoehendifferenz_m und horizontale_distanz_m können negativ sein (Gefälle)
            return v
        # steigung_prozent kann negativ sein (Gefälle)
        return v
```

**Zusätzliche interne Typen (nicht im Register, aber intern im Modul):**

```python
class DEMTileKey(BaseModel):
    """Schlüssel für DEM-Kachel (Koordinaten-BBox + CRS-Referenz)."""
    min_lat: float = Field(ge=-90, le=90)
    max_lat: float = Field(ge=-90, le=90)
    min_lon: float = Field(ge=-180, le=180)
    max_lon: float = Field(ge=-180, le=180)
    crs_epsg: int = Field(default=4326, description="EPSG-Code des CRS")


class DEMTile(BaseModel):
    """In-Memory-Representation einer DEM-Kachel mit Metadaten."""
    key: DEMTileKey
    raster_data: bytes  # Rohe GeoTIFF-Daten (nur für Caching, nicht exportieren)
    transform: List[float] = Field(
        description="Affine Transform matrix [a, b, c, d, e, f] für pixel->world"
    )
    width: int
    height: int
    nodata_value: float = Field(default=-9999)
```

---

## 4. Öffentliche Schnittstelle

**Dateistruktur:**
```
src/tripplanner/elevation/
├── __init__.py        # exports: ElevationProvider, get_elevation_profile, calculate_segment_gradients
├── models.py           # siehe Abschnitt 3
├── elevation.py        # Kernlogik (öffentliche Funktionen + Provider-Interface)
└── providers.py        # DEMDataSourceProtocol + GeoTIFFDataSource + FakeDataSource
```

**Öffentliche API in `src/tripplanner/elevation/elevation.py`:**

```python
from typing import Protocol, List, Tuple
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.elevation.models import ElevationPoint, SegmentGradient


class DEMDataSourceProtocol(Protocol):
    """Protocol für DEM-Datenquellen (für Testbarkeit)."""
    def get_elevation(self, lat: float, lon: float) -> float:
        """Höhenwert an einer Koordinate abfragen. -9999 = ungültig."""
        ...
    
    def get_elevations_batch(self, coordinates: List[Tuple[float, float]]) -> List[float]:
        """Höhenwerte für mehrere Koordinaten (optimiert für Batch-Lookup)."""
        ...


class ElevationProvider:
    """Hauptprovider-Klasse für Höhendaten."""
    
    def __init__(self, data_source: DEMDataSourceProtocol):
        self.data_source = data_source
    
    def get_elevation_profile(
        self, 
        route: Route,
        sampling_distance_m: float = 100.0
    ) -> List[ElevationPoint]:
        """
        Extrahiert Höhenprofile entlang der Route.
        
        Args:
            route: Die Route mit segments (aus routing.models)
            sampling_distance_m: Sampling-Distanz in Metern (Standard: 100 m)
        
        Returns:
            Liste von ElevationPoint für jeden Sample-Punkt (inkl. Start/Ende jedes Segments)
        """
        ...
    
    def calculate_segment_gradients(
        self,
        elevation_points: List[ElevationPoint],
        route: Route
    ) -> List[SegmentGradient]:
        """
        Berechnet Steigung/Gefälle je Segment aus Höhendifferenz und horizontaler Distanz.
        
        Args:
            elevation_points: ElevationPoints in Reihenfolge der Route (Start->Ziel)
            route: Originale Route (für Segment-Geometrie)
        
        Returns:
            Liste von SegmentGradient (einer pro Segment)
        """
        ...


def calculate_horizontal_distance(
    coord1: Tuple[float, float], 
    coord2: Tuple[float, float]
) -> float:
    """
    Berechnet horizontale Distanz zwischen zwei Koordinaten (WGS84).
    
    Args:
        coord1: (breitengrad, laengengrad) Punkt 1
        coord2: (breitengrad, laengengrad) Punkt 2
    
    Returns:
        Horizontale Distanz in Metern (nicht entlang der Route!)
    """
    ...
```

**Export in `src/tripplanner/elevation/__init__.py`:**
```python
from .models import ElevationPoint, SegmentGradient
from .elevation import ElevationProvider, calculate_horizontal_distance

__all__ = [
    "ElevationPoint",
    "SegmentGradient", 
    "ElevationProvider",
    "calculate_horizontal_distance"
]
```

---

## 5. Externe Integration / Algorithmus-Details

### 5.1 DEM-Datenquelle: Copernicus DEM GLO-30

**Quelle:** AWS Open Data Registry `copernicus-dem-30m` (Bucket: `copernicus-dem-30m.s3.eu-central-1.amazonaws.com`)

**Begründung für AWS S3 (nicht OpenTopography API):**
- Kostenlose, uneingeschränkte Nutzung (Copernicus Open License)
- Keine API-Key-Registrierung nötig
- Cloud Optimized GeoTIFFs (COG) direkt nutzbar
- Region `eu-central-1` nahe am Projekt-Standort (geringere Latenz)
- Lesezugriff ohne AWS-Account (öffentliche Bucket-ACL)

**Dateiformat & Kachelschema:**
- Format: Cloud Optimized GeoTIFF (COG), LZW-Kompression
- Auflösung: 30 m × 30 m (GLO-30 Public)
- CRS: WGS84 / EPSG:4326 (Geodätisch) oder UTM-Zonen (abgeleitet)
- Tile-Größe: 1° × 1° (Breite × Länge)
- Dateinamen-Schema: `Copernicus_DSM_COG_{ZONENCODE}_{LAT}_{ZONENCODE}_{LON}_DEMSUB.tif`
  - Beispiel: `Copernicus_DSM_COG30_N50_00_E008_00_DEM.tif` (50°N, 8°E)
- Nodata-Wert: `-9999` (ungültige Daten, z. B. Ozeane)

**rasterio-Workflow für Höhen-Lookup:**

```python
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.crs import CRS
import numpy as np

def load_dem_tile(tile_path: str) -> rasterio.DatasetReader:
    """Lädt eine DEM-Kachel und validiert CRS."""
    src = rasterio.open(tile_path)
    if src.crs != CRS.from_epsg(4326):
        # UTM-Zonen nach WGS84 transformieren (nur einmal beim Laden)
        transform, width, height = calculate_default_transform(
            src.crs, CRS.from_epsg(4326), src.width, src.height, *src.bounds
        )
        # Reprojektion in temporäres Array (oder lazy loading)
        # Für Produktionscode: lazy via WarpedVRT
    return src

def sample_elevation(src: rasterio.DatasetReader, lat: float, lon: float) -> float:
    """
    Extrahiert Höhenwert an Koordinate.
    
    Args:
        src: Geöffneter rasterio.DatasetReader
        lat: Breitengrad (WGS84)
        lon: Längengrad (WGS84)
    
    Returns:
        Höhenwert in Metern oder -9999 (nodata)
    """
    # Prüfen ob Koordinate im Bounds liegt
    if not (src.bounds.left <= lon <= src.bounds.right and 
            src.bounds.bottom <= lat <= src.bounds.top):
        return -9999  # Ausserhalb des Tiles
    
    # Umrechnung World -> Pixel (row, col)
    row, col = src.index(lon, lat)  # rasterio index() nimmt (x, y) = (lon, lat) für EPSG:4326
    
    # Lesen des Wertes (band 1 = Höhe)
    band_data = src.read(1, window=((row, row+1), (col, col+1)))
    value = band_data[0, 0]
    
    # Nodata-Check
    if np.isnan(value) or value == src.nodata:
        return -9999
    return value
```

**Kachelgrenzen-Handling:**
- Koordinaten innerhalb eines Segments können mehrere Tiles betreffen
- Algorithmus: Ermittle alle relevanten Tile-Keys für das Segment-BBox
- Falls Koordinate außerhalb des aktuellen Tiles liegt, versuche Nachbartile
- Priorität: exaktes Tile > Nachbar-Tile > -9999 (undefined)

**Datenpipelinemodell (lokale Vorbereitung):**
1. DEM-Kacheln für Europa-West manuell herunterladen (oder Script `scripts/fetch_dem_tiles.py`)
2. Speicherort: `data/elevation/copernicus/` (im .gitignore)
3. Index-Datei `data/elevation/tile_index.json` mit BBox-Metadaten (optional, für schnelles Tile-Lookup)
4. Caching: `functools.lru_cache` für `rasterio.open()` (teures Opening vermeiden)

---

### 5.2 Steigungsberechnung

**Mathematische Formel (aus Websuche verifiziert):**

$$\text{Steigung (\%)} = \frac{\text{Höhendifferenz}\ [\text{m}]}{\text{Horizontale\ Distanz}\ [\text{m}]} \times 100$$

**Horizontale Distanz** ist die Luftlinienprojektion (nicht entlang der Route!):
- Gegeben: Zwei Koordinaten $(lat_1, lon_1)$ und $(lat_2, lon_2)$
- Berechne geodätische Distanz mit `geographiclib` (exakt auf Spheroid WGS84)

```python
from geographiclib.geodesic import Geodesic

def calculate_horizontal_distance(
    coord1: Tuple[float, float], 
    coord2: Tuple[float, float]
) -> float:
    """
    Berechnet horizontale Distanz zwischen zwei WGS84-Koordinaten.
    
    Args:
        coord1: (breitengrad, laengengrad)
        coord2: (breitengrad, laengengrad)
    
    Returns:
        Horizontale Distanz in Metern (nicht entlang der Route!)
    """
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    
    geod = Geodesic.WGS84
    inv = geod.Inverse(lat1, lon1, lat2, lon2)
    return inv["s12"]  # Distanz in Metern
```

**Algorithmus in `calculate_segment_gradients()`:**

```python
def calculate_segment_gradients(
    self,
    elevation_points: List[ElevationPoint],
    route: Route
) -> List[SegmentGradient]:
    """Berechnet Steigung je Segment."""
    gradients: List[SegmentGradient] = []
    
    # Route-Segments müssen mit ElevationPoints korrelieren
    # Annahme: elevation_points enthält Start+Ende jedes Segments in Reihenfolge
    
    point_idx = 0
    for seg_idx, segment in enumerate(route.segments):
        if point_idx + 1 >= len(elevation_points):
            break
        
        start_point = elevation_points[point_idx]
        end_point = elevation_points[point_idx + 1]
        
        # Höhendifferenz (Ende - Start; positiv = Steigung, negativ = Gefälle)
        dh = end_point.hoehe_m - start_point.hoehe_m
        
        # Horizontale Distanz berechnen (nicht Route-Länge!)
        horizontal_dist = calculate_horizontal_distance(
            start_point.koordinate, 
            end_point.koordinate
        )
        
        if horizontal_dist == 0:
            gradient_pct = 0.0  # Vermeide Division durch Null
        else:
            gradient_pct = (dh / horizontal_dist) * 100
        
        gradients.append(SegmentGradient(
            segment_index=seg_idx,
            steigung_prozent=gradient_pct,
            hoehendifferenz_m=dh,
            horizontale_distanz_m=horizontal_dist
        ))
        
        point_idx += 1  # Nächstes Segment startet am Endpunkt dieses
    
    return gradients
```

**Bemerkung:** Die Formel ist identisch mit der USGS-Definition (https://www.usgs.gov/educational-resources/determine-percent-slope-and-angle-slope). Für Winkel: $\text{Slope}^\circ = \arctan(\text{Steigung\%} / 100)$ — wird im `energy`-Modul für Neigungswinkel benötigt.

---

### 5.3 Fake-Implementierung für Tests (`providers.py`)

```python
from typing import List, Tuple
from tripplanner.elevation.providers import DEMDataSourceProtocol


class FakeDataSource(DEMDataSourceProtocol):
    """Synthetische DEM-Daten für Unit-Tests (kein echtes File I/O)."""
    
    def __init__(self, baseline_elevation: float = 100.0, noise_range: float = 5.0):
        self.baseline = baseline_elevation
        self.noise = noise_range
    
    def get_elevation(self, lat: float, lon: float) -> float:
        # Deterministisch basierend auf Koordinaten (nicht zufällig!)
        hash_val = hash((round(lat, 5), round(lon, 5))) % 1000
        noise = (hash_val / 1000.0 - 0.5) * 2 * self.noise  # -noise..+noise
        return self.baseline + noise
    
    def get_elevations_batch(self, coordinates: List[Tuple[float, float]]) -> List[float]:
        return [self.get_elevation(lat, lon) for lat, lon in coordinates]
```

---

## 6. Test-Strategie

### 6.1 Fixtures (in `tests/fixtures/elevation/`)

**Dateien:**
1. `copernicus_dem_test_tile.tif` — kleines synthetisches GeoTIFF (5×5 Pixel, 10m Auflösung, 30m-Bezug)
   - Koordinaten: 47.0°N, 8.0°E bis 47.00015°N, 8.00015°E (ca. 11m × 11m)
   - Werte: lineare Steigung von 100m (linkes oben) bis 120m (rechts unten)
2. `tile_index.json` — BBox-Metadaten für Test-Tile
3. `route_segment_example.json` — kleines RouteSegment Beispiel mit 4 Koordinatenpunkten

**Beispiel `route_segment_example.json`:**
```json
{
  "segment_index": 0,
  "koordinaten": [
    [47.0, 8.0],
    [47.00005, 8.00005],
    [47.00010, 8.00010],
    [47.00015, 8.00015]
  ],
  "laenge_m": 22.5,
  "strassenklasse": "A",
  "tempolimit_kmh": 120
}
```

### 6.2 Konkrete Testfälle

**Test 1: Einzelnes Elevation-Lookup (Unit)**

**Given:** `FakeDataSource` mit `baseline=150.0`, `noise=2.0`
**When:** `provider.get_elevation(47.0, 8.0)` aufgerufen
**Then:** Ergebnis ist im Bereich `[148.0, 152.0]` (± noise_range)

```python
def test_fake_data_source_elevation(fake_data_source: DEMDataSourceProtocol):
    """Testet deterministische Höhen-Abfrage."""
    elevation = fake_data_source.get_elevation(47.0, 8.0)
    assert 148.0 <= elevation <= 152.0
```

**Test 2: Steigungsberechnung mit echtem GeoTIFF (Integration)**

**Given:** Test-GeoTIFF mit linearer Steigung (100m → 120m über 11m Distanz → ~181.8%)
**When:** `ElevationProvider.calculate_segment_gradients()` aufgerufen mit 2 Punkten (Start/Ende)
**Then:** `steigung_prozent ≈ 181.8` (innerhalb ±1% Toleranz aufgrund Raster-Auflösung)

```python
def test_segment_gradient_with_real_dem(real_dem_provider: ElevationProvider, 
                                         route_with_two_points: Route):
    """Integrationstest mit echtem DEM-Tile."""
    points = real_dem_provider.get_elevation_profile(route_with_two_points, sampling_distance_m=10.0)
    gradients = real_dem_provider.calculate_segment_gradients(points, route_with_two_points)
    
    assert len(gradients) == 1
    assert gradients[0].hoehendifferenz_m == pytest.approx(20.0, abs=1.0)  # 120-100=20
    assert gradients[0].steigung_prozent == pytest.approx(181.8, abs=2.0)  # 20/11*100
```

**Test 3: Grenzfall — Kachelgrenze überschreitet (Integration)**

**Given:** Route, die exakt über Tile-Grenze geht (z. B. 47.00015°N)
**When:** `get_elevation_profile()` mit Real-DEM
**Then:** Kein `IndexError` oder `ValueError`, gültige Höhenwerte zurück (auch -9999 ist akzeptabel für Ozean)

```python
def test_tile_boundary_crossing(real_dem_provider: ElevationProvider):
    """Testet Umgang mit Kachelgrenzen."""
    coords = [(47.0000, 8.0000), (47.00016, 8.0000)]  # genau an Tile-Grenze
    # Route mit diesen Punkten simulieren
    route = mock_route_with_coords(coords)
    
    points = real_dem_provider.get_elevation_profile(route)
    assert len(points) == 2
    assert -9999 <= points[0].hoehe_m <= 5000  # valid range check
    assert -9999 <= points[1].hoehe_m <= 5000
```

### 6.3 Unit- vs. Integrationstest-Abgrenzung

| Test | Typ | Decorator | Beschreibung |
|------|-----|-----------|--------------|
| `test_fake_data_source_elevation` | Unit | - | FakeDataSource mit deterministischen Werten |
| `test_fake_data_source_batch` | Unit | - | Batch-Lookup mit mehreren Koordinaten |
| `test_horizontal_distance_calculation` | Unit | - | `calculate_horizontal_distance()` mit bekannten Koordinaten |
| `test_segment_gradient_calculation` | Unit | - | Manuelle Erstellung von ElevationPoints, Steigung berechnen |
| `test_real_dem_elevation_lookup` | Integration | `@pytest.mark.integration` | Echtes GeoTIFF-File einlesen, Koordinaten validieren |
| `test_tile_boundary_crossing` | Integration | `@pytest.mark.integration` | Route über Tile-Grenze, Edge Cases |

---

## 7. Aufgaben-Checkliste

**Voraussetzung:** `docs/plans/01-routing.md` ist umgesetzt (Route-Modelle stehen)

- [ ] **Task 1:** Datenmodelle definieren (`src/tripplanner/elevation/models.py`)
  - [ ] `ElevationPoint` mit Validatoren (lat/lon-Bereich, höhe_m-Grenzen)
  - [ ] `SegmentGradient` mit Validatoren (keine Division durch Null in Tests)
  - [ ] `__init__.py` für Exporte anlegen
  - **Akzeptanz:** `mypy src/tripplanner/elevation/models.py` ohne Errors

- [ ] **Task 2:** Fake-DataSource implementieren (`src/tripplanner/elevation/providers.py`)
  - [ ] `DEMDataSourceProtocol` (abc.Protocol)
  - [ ] `FakeDataSource` mit deterministischer Noise-Funktion
  - [ ] `get_elevation` und `get_elevations_batch` Methoden
  - **Akzeptanz:** `pytest tests/elevation/test_providers.py -v` → 100% Coverage

- [ ] **Task 3:** `calculate_horizontal_distance` implementieren
  - [ ] Nutzung von `geographiclib.geodesic.Geodesic.WGS84.Inverse`
  - [ ] Umgang mit identischen Koordinaten (Return 0.0)
  - [ ] Dokstring mit Formel und Beispiel (Google-Style)
  - **Akzeptanz:** Manueller Test: Distanz (0°,0°) → (0°,1°) ≈ 111.32 km

- [ ] **Task 4:** `ElevationProvider` Kernlogik (`elevation.py`)
  - [ ] `__init__(self, data_source: DEMDataSourceProtocol)`
  - [ ] `get_elevation_profile(route, sampling_distance_m)` implementieren
  - [ ] `calculate_segment_gradients(elevation_points, route)` implementieren
  - [ ] Fehlerbehandlung: leerer Route, unvollständige elevation_points
  - **Akzeptanz:** Unit-Tests mit FakeDataSource, keine externen Calls

- [ ] **Task 5:** Test-Fixtures vorbereiten
  - [ ] Synthetisches 5×5 GeoTIFF erstellen (`tests/fixtures/elevation/copernicus_dem_test_tile.tif`)
  - [ ] `tile_index.json` mit Metadaten erzeugen
  - [ ] `route_segment_example.json` mit 4 Koordinaten
  - **Akzeptanz:** `rasterio.open("copernicus_dem_test_tile.tif").crs == EPSG:4326`

- [ ] **Task 6:** Integrationstest mit echtem GeoTIFF
  - [ ] `conftest.py` mit fixtures: `real_dem_provider()` (läd Test-Tile einmalig)
  - [ ] `test_segment_gradient_with_real_dem` implementieren
  - [ ] `test_tile_boundary_crossing` implementieren
  - [ ] `@pytest.mark.integration` decorator anwenden
  - **Akzeptanz:** `pytest -m integration` → 2 Tests passen

- [ ] **Task 7:** Dokumentation schreiben (Google-Style, ruff D-Regeln)
  - [ ] Alle Klassen-, Methoden-, Funktionsdocstrings hinzufügen
  - [ ] Type hints in allen Signaturen (auch `*args`/`**kwargs`)
  - [ ] Beispiele in docstrings (wenn sinnvoll)
  - **Akzeptanz:** `ruff check src/tripplanner/elevation/` → keine D1xx/ D2xx Errors

- [ ] **Task 8:** `rasterio`-Installation prüfen + optionalen DEM-Fetcher schreiben
  - [ ] `uv add rasterio` ausführen, `rasterio.__version__` ≥ 1.3.0
  - [ ] Prüfen ob `rasterio.env.Env.default_credentials` gesetzt ist (für S3 access)
  - [ ] Optional: `scripts/fetch_dem_tiles.py` erstellen (Download für DE/DK/SE)
  - **Akzeptanz:** `python -c "import rasterio; print(rasterio.__version__)"` → 1.3.x+

- [ ] **Task 9:** Test Coverage erzwingen (pytest-cov)
  - [ ] `pytest --cov=src/tripplanner/elevation --cov-report=term-missing --cov-fail-under=85`
  - [ ] Alle öffentlichen Funktionen/mindestens 90% der internen Logik abgedeckt
  - [ ] FakeDataSource und Integrationstests kombiniert für 85%+ Coverage
  - **Akzeptanz:** CLI-Output zeigt `85%` oder höher

- [ ] **Task 10:** Linting und Typisierung prüfen
  - [ ] `ruff check src/tripplanner/elevation/ tests/elevation/` (select=E,F,I,UP,B,SIM,PL,RUF,D)
  - [ ] `mypy src/tripplanner/elevation/` mit `--strict` → keine Errors
  - [ ] `pre-commit run --all-files` falls Hook-System eingerichtet
  - **Akzeptanz:** Keine roten Zeilen in Lint-Output

- [ ] **Task 11:** Integration mit `routing`-Modul validieren
  - [ ] Kleines End-to-End-Szenario: `Route` von `routing` → `elevation.get_elevation_profile` → `calculate_segment_gradients`
  - [ ] Test-Route mit 3 Segmenten (Start→Ziel inkl. Zwischenstopp)
  - [ ] Validierung: `len(segments) == len(gradients)`
  - **Akzeptanz:** `pytest tests/elevation/test_elevation.py::test_integration_with_routing` passiert

- [ ] **Task 12:** Caching und Performance optimieren (optional, für Phase 1 nicht zwingend)
  - [ ] `functools.lru_cache` für `rasterio.open()` (maxsize=4 für Europa-West)
  - [ ] Batch-Lookup optimieren (numpy-Vectorized operations falls möglich)
  - [ ] Profiling mit `pytest-benchmark` für 100+ Koordinaten
  - **Akzeptanz:** Zeit für 100 Elevation-Lookups ≤ 100ms (on SSD)

---

## 8. Risiken & offene technische Fragen

**Bereits durch „Verbindlich entschiedene offene Punkte" abgedeckt (aus docs/06):**
- Keine Abhängigkeit von Live-Netzwerk in Unit-Tests (FakeDataSource deckt ab)
- Lokale DEM-Datenquelle ist akzeptiert (kein Crawler nötig)

**Offene technische Fragen (nicht durch Projektvorgaben geregelt):**

1. **UTM-Zonentransformation:** Soll das Modul WGS84→UTM automatisch durchführen, oder vorausgesetzt werden?
   - **Entscheidung:** WGS84 (EPSG:4326) direkt mit `rasterio.index()` ist ausreichend für 30m-Auflösung (error < 1cm für Europa). UTM-Projektion erst bei Bedarf mit `pyproj.Transformer`.

2. **DEM-Caching-Strategie:** Soll das Modul eine eigene Cache-Layer (z. B. SQLite mit GeoTIFF-Blobs) haben?
   - **Entscheidung:** Kein eigener Cache in Phase 1 — `rasterio` nutzt bereits internes VRT-Caching. Falls Performance-Probleme auftreten: `rasterio.vrt.WarpedVRT` für lazy reprojection.

3. **Mehrfach-Verarbeitung von Koordinaten:** Soll `get_elevation_profile`_duplicates-Debouncing implementieren (wenn Route oft die gleiche Koordinate traversiert)?
   - **Entscheidung:** Nein — `sampling_distance_m=100` garantiert hinreichende Distanz, um Duplikate zu vermeiden. Falls nötig: `set`-Debouncing im `routing`-Modul (nicht hier).

4. **Nodata-Handling bei Meeresgebieten:** Soll `-9999` (nodata) durch Interpolation ersetzt werden?
   - **Entscheidung:** Nein —保留 `-9999` als Marker. Das `energy`-Modul kann dann entscheiden, ob es Meeressegmente ablehnt oder interpoliert.

5. **Große europäische Routen (mehr als 4 Tiles):** Wie effizient ist das dynamische Tile-Lookup?
   - **Lösung:** Erstes MVP mit sequenziellem Tile-Open. Falls Performance-Engpass: Tile-Index mit Quadtree (nicht in Phase 1).

6. **rasterio-Architektur-Bindung:** Wird `rasterio` als feste Dependency akzeptiert?
   - **Entscheidung:** Ja — `rasterio` ist der Standard für GeoTIFF in Python und in `docs/02-architektur.md` explizit genannt. Alternative (`rioxarray`) wäre Overkill.

**Zusätzliches Risiko:**
- **Dateigröße:** Copernicus DEM GLO-30 für ganz Europa ist ca. 500 GB. **Lösung:** Nur DE/DK/SE-Tiles herunterladen (ca. 20–30 GB), `.gitignore` für `data/elevation/` erzwingen.