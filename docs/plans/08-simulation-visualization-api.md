# Plan: Simulation, Visualization/API-Schicht (Phase 7)

**Zweck dieses Dokuments:** Gemeinsamer Implementierungsplan für drei eng verwandte Komponenten:

1. **simulation** (Backend, Phase 6→7) – Erzeugung der Zeitreihe aus Route+ChargingPlan+Energie-/Wetterdaten  
2. **visualization** (Frontend, Phase 7) – Grafische Darstellung der Simulationsergebnisse  
3. **trip_input / API-Schicht** (Phase 7) – FastAPI-Endpunkt + CLI-Entry-Point, Orchestrierung der 11 Datenfluss-Schritte  

Die drei Module werden eng gekoppelt implementiert, da sie stark voneinander abhängen und denselben Contract (JSON-Schema-basiert) teilen.

---

## 1. Zweck & Scope

### 1.1 `simulation`-Modul

**Was es leistet:**
- Aus `Route`, `ChargingPlan`, `SegmentEnergyResult` und `WeatherSample` eine diskrete Zeitreihe (`TripSimulationResult`) erzeugen  
- Jeder Zeitschritt enthält: Zeitpunkt, Position (Interpolation zwischen Segmentgrenzen), aktueller SoC, Geschwindigkeit (km/h), Zustand (FAHREN/LADEN/PAUSE)  
- Zeitauflösung: Konfigurierbar (Default: alle 60 Sekunden), alternativ Segmentgrenzen + Ladestopps als explizite Eventpunkte  
- Rekonstruktion der Position via Linear Interpolation entlang der Route-Geometrie  
- SoC-Verlauf: Entladung (nach `energy`-Modul) und Ladevorgänge (nach `battery`-Modul, Ladekurve)

**Nicht-Scope:**
- Keine Echtzeit-Simulation (nur reine Rekonstruktion nach fixierter Route + Ladeplan)  
- Keine Neuberechnung der Route oder des Ladeplans  
- Keine Wetter-ETA-Iterative-Auflösung (durch `optimization`-Modul vor Simulation abgeschlossen)

### 1.2 `visualization`-Modul (Frontend)

**Was es leistet:**
- Karte mit Route als GeoJSON-LineString mit SoC-Farbverlauf entlang der Strecke  
- Marker für Ladehalte (Tesla Supercharger) und Zwischenstopps  
- Zeit/Soc-Verlauf als separates Diagramm (Line-Chart über Zeit vs. SoC)  
- Interaktive Hover-Info-Fenster pro Marker/Segment  

**Nicht-Scope:**
- Keine direkte Kartendaten-Herunterladestrategie (nutzt OpenFreemap/standard-Style)  
- Keine Animationen (nur statische Darstellung)  
- Keine Navigation (keine Steuerung des Simulators, nur Lesemodus)  

### 1.3 `trip_input`/API-Schicht

**Was es leistet:**
- `POST /trips`-Endpunkt (FastAPI) nimmt `TripRequest`, liefert `TripSimulationResult`  
- CLI-Entry-Point (`python -m tripplanner.cli trips …`) dieselbe Pipeline aufrufend  
- Orchestrierung aller 11 Schritte aus Datenfluss-Abschnitt (inkl. iterative ETA/Wetter-Schleife aus `optimization`)  
- Keine Geschäftslogik, nur Verkettung der bereits implementierten Module  

**Nicht-Scope:**
- Keine Authentifizierung (lokaler, nichtöffentlicher API-Endpunkt)  
- Keine Persistenz (nur Transient)  
- Keine Caching-Schicht (wird erst später eingebaut, wenn nötig)  

---

## 2. Abhängigkeiten & Phasenzuordnung

| Modul | Phase | Importierte Modelle (exakt nach Register) |
|-------|-------|-------------------------------------------|
| **simulation** | Phase 6→7 | `tripplanner.routing.models.Route`, `tripplanner.elevation.models.SegmentGradient`, `tripplanner.weather.models.WeatherSample`, `tripplanner.energy.models.SegmentEnergyResult`, `tripplanner.battery.models.SoCState`, `tripplanner.battery.models.ChargingCurve`, `tripplanner.charging_infrastructure.models.ChargingStation`, `tripplanner.optimization.models.ChargingPlan`, `tripplanner.optimization.models.ChargingStop` |
| **visualization** | Phase 7 | `tripplanner.simulation.models.TripSimulationResult`, `tripplanner.simulation.models.SimulationFrame`, `tripplanner.optimization.models.ChargingStop`, `tripplanner.trip_input.models.Waypoint` (nur zur Anzeige) |
| **trip_input/API** | Phase 7 | `tripplanner.trip_input.models.TripRequest`, `tripplanner.trip_input.models.VehicleProfile`, `tripplanner.trip_input.models.Waypoint`, `tripplanner.routing.models.Route`, `tripplanner.elevation.models.SegmentGradient`, `tripplanner.weather.models.WeatherSample`, `tripplanner.energy.models.SegmentEnergyResult`, `tripplanner.charging_infrastructure.models.ChargingStation`, `tripplanner.optimization.models.ChargingPlan`, `tripplanner.simulation.models.TripSimulationResult` |

**Anmerkung:** `trip_input.models` ist noch zu implementieren; das Register ist für dieses Modul bindend — es existiert nur die Schema-Definition, keine Implementierung.

---

## 3. Datenmodelle (Python, Pydantic v2)

### 3.1 `simulation.models`

```python
from __future__ import annotations
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated
from pydantic import BaseModel, Field, model_validator


class TripState(str, Enum):
    FAHREN = "FAHREN"
    LADEN = "LADEN"
    PAUSE = "PAUSE"


class SimulationFrame(BaseModel):
    """Ein einzelner Zeitpunkt in der Reisesimulation"""

    zeitpunkt: datetime
    position: tuple[
        float, float
    ]  # (lat, lon) – WGS84, konsistent mit dem Domänenmodell (siehe Konvention unten)
    soc_pct: Annotated[float, Field(ge=0.0, le=100.0)]
    zustand: TripState
    geschwindigkeit_kmh: Annotated[float, Field(ge=0.0)]

    @model_validator(mode="after")
    def validate_speed_state_consistency(self) -> SimulationFrame:
        if self.zustand == TripState.LADEN and self.geschwindigkeit_kmh > 0.5:
            raise ValueError("Beim Laden muss Geschwindigkeit ≈ 0 sein")
        if self.zustand == TripState.PAUSE and self.geschwindigkeit_kmh > 5.0:
            raise ValueError("Bei Pause sollte Geschwindigkeit sehr gering sein")
        return self


class TripSimulationResult(BaseModel):
    """Vollständige Zeitreihe einer Reise"""

    frames: list[SimulationFrame]
    gesamt_distanz_km: float
    gesamt_fahrzeit_min: float
    gesamt_ladezeit_min: float
    start_soc_pct: float
    ziel_soc_pct: float
```

**Ergänzende Typen (nicht im Register aufgeführt, aber nötig):**
- `tripplanner.trip_input.models`: `TripRequest` (s. Register), `Waypoint`, `VehicleProfile` (s. Register)  

**Wichtig — Koordinaten-Konvention:** `SimulationFrame.position` nutzt wie alle Domänenmodelle `(lat, lon)` (siehe `docs/plans/01-routing.md`, Abschnitt 3). Die GeoJSON-Reihenfolge `(lon, lat)` wird ausschließlich im Frontend an der Rendering-Grenze erzeugt (siehe Abschnitt 5.2), nie in Backend-Modellen.

---

## 4. Öffentliche Schnittstelle (Python)

### 4.1 `simulation.__init__.py` (öffentliche API)

```python
from tripplanner.simulation.models import SimulationFrame, TripSimulationResult
from tripplanner.simulation.simulate import simulate_trip

__all__ = ["SimulationFrame", "TripSimulationResult", "simulate_trip"]
```

### 4.2 `simulation/simulate.py` (Kernfunktion)

```python
from datetime import datetime, timedelta
from typing import Optional
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.battery.models import SoCState, ChargingCurve, ChargingCurvePoint
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.optimization.models import ChargingPlan, ChargingStop
from tripplanner.simulation.models import SimulationFrame, TripState


def simulate_trip(
    route: Route,
    charging_plan: ChargingPlan,
    segment_energy: list[SegmentEnergyResult],
    weather_samples: list[WeatherSample],
    start_soc_pct: float,
    max_iterations: int = 3,
    convergence_threshold_minutes: float = 30.0,
    output_resolution_seconds: int = 60,
) -> TripSimulationResult:
    """
    Simuliert die komplette Reise entlang der Route unter Berücksichtigung des Ladeplans.

    Args:
        route: Route mit Segmente (feste Geometrie nach GraphHopper)
        charging_plan: Optimierter Ladeplan aus optimization.Modul
        segment_energy: Energiebedarf je Segment
        weather_samples: Wetter pro Abfragepunkt
        start_soc_pct: Start-SoC in %
        max_iterations: Max. Anzahl Iterationen für ETA-Wetter-Konvergenz
        convergence_threshold_minutes: Schwelle in Minuten für Iterationserneuerung
        output_resolution_seconds: Zeitauflösung der Ausgabe (default: 60s)

    Returns:
        TripSimulationResult: Zeitreihe aus Frames (Zeit, Position, SoC, Zustand, Geschwindigkeit)
    """
    pass  # Implementierung siehe Abschnitt 5
```

### 4.3 `tripplanner.cli` (CLI-Entry-Point)

**Datei:** `src/tripplanner/cli/main.py`

```python
import typer
from pathlib import Path
from typing import Optional
from tripplanner.trip_input.api import create_trip_simulation
from tripplanner.simulation.models import TripSimulationResult
import json

app = typer.Typer(help="Tesla Trip Planner – CLI für Reiseplanung und Simulation")


@app.command()
def trips(
    start: str = typer.Option(..., help="Start-Koordinate als 'lat,lon'"),
    ziel: str = typer.Option(..., help="Ziel-Koordinate als 'lat,lon'"),
    zwischenstopps: Optional[list[str]] = typer.Option(
        None, help="Zwischenstopps als 'lat,lon:duration_min'"
    ),
    abfahrtszeit: str = typer.Option(
        ..., help="Abfahrtszeit im ISO-Format (z. B. '2026-08-15T08:30:00')"
    ),
    start_soc_pct: float = typer.Option(80.0, ge=0.0, le=100.0, help="Start-SoC in Prozent"),
    ziel_soc_pct: float = typer.Option(20.0, ge=0.0, le=100.0, help="Ziel-SoC in Prozent"),
    vehicle_profile: str = typer.Option(
        "model3_standard", help="Name des Fahrzeugprofils aus config"
    ),
    output_json: Optional[Path] = typer.Option(
        None, help="Pfad zur JSON-Ausgabe (default: stdout)"
    ),
) -> None:
    """
    Berechnet eine Reise und simuliert sie vollständig (inkl. Ladeplanung und ETA-Wetter-Iterative).
    """
    # Konvertierung Eingaben
    from datetime import datetime
    import math

    def parse_coord(s: str) -> tuple[float, float]:
        parts = s.split(",")
        if len(parts) != 2:
            raise ValueError(f"Ungültige Koordinate: {s}")
        return (
            float(parts[0]),
            float(parts[1]),
        )  # lat, lon — Eingabeformat "lat,lon" bleibt in der Reihenfolge erhalten

    def parse_waypoint(s: str) -> tuple[tuple[float, float], float | None]:
        if ":" in s:
            coord, dur = s.split(":")
            return parse_coord(coord), int(dur) * 60
        return parse_coord(s), None

    start_coord = parse_coord(start)
    ziel_coord = parse_coord(ziel)

    zwischen = []
    if zwischenstopps:
        for wp in zwischenstopps:
            coord, dur = parse_waypoint(wp)
            zwischen.append((coord, timedelta(seconds=dur) if dur else None))

    request = {
        "start": start_coord,
        "ziel": ziel_coord,
        "zwischenstopps": [
            {"koordinate": w[0], "aufenthaltsdauer_s": int(w[1].total_seconds()) if w[1] else None}
            for w in zwischen
        ],
        "abfahrtszeit": abfahrtszeit,
        "fahrzeugprofil": vehicle_profile,
        "praeferenzen": {},
    }

    result: TripSimulationResult = create_trip_simulation(request)

    output = {
        "gesamt_distanz_km": result.gesamt_distanz_km,
        "gesamt_fahrzeit_min": result.gesamt_fahrzeit_min,
        "gesamt_ladezeit_min": result.gesamt_ladezeit_min,
        "frames": [
            {
                "zeitpunkt": f.zeitpunkt.isoformat(),
                "position": list(f.position),
                "soc_pct": f.soc_pct,
                "zustand": f.zustand.value,
                "geschwindigkeit_kmh": f.geschwindigkeit_kmh,
            }
            for f in result.frames
        ],
    }

    if output_json:
        output_json.write_text(json.dumps(output, indent=2))
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    app()
```

### 4.4 `tripplanner.trip_input.api` (FastAPI-Endpunkt)

**Datei:** `src/tripplanner/trip_input/api.py`

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timedelta
from tripplanner.trip_input.models import TripRequest, Waypoint, VehicleProfile
from tripplanner.simulation.models import TripSimulationResult
import httpx
import asyncio

app = FastAPI(title="Tesla Trip Planner API", version="0.1.0")


# --- Request/Response-Modelle für API (identisch zu Modellen, aber explizit für API) ---
class TripRequestAPI(BaseModel):
    """API-Request für /trips-Endpunkt"""

    start: tuple[float, float] = Field(..., description="Startkoordinate (lat, lon)")
    ziel: tuple[float, float] = Field(..., description="Zielkoordinate (lat, lon)")
    zwischenstopps: list[WaypointAPI] = Field(default=[], description="Liste von Zwischenstopps")
    abfahrtszeit: datetime = Field(..., description="ISO-8601 Abfahrtszeit")
    fahrzeugprofil: str = Field(..., description="Name des Fahrzeugprofils aus Konfiguration")
    praeferenzen: dict = Field(default_factory=dict, description="Nutzerpräferenzen")


class WaypointAPI(BaseModel):
    koordinate: tuple[float, float] = Field(..., description="(lat, lon)")
    aufenthaltsdauer_s: Optional[int] = Field(
        None, ge=0, description="Mindestaufenthaltsdauer in Sekunden"
    )


class TripSimulationResultAPI(BaseModel):
    """API-Response für /trips-Endpunkt"""

    gesamt_distanz_km: float = Field(..., description="Gesamtdistanz in km")
    gesamt_fahrzeit_min: float = Field(..., description="Gesamtfahrzeit in Minuten")
    gesamt_ladezeit_min: float = Field(..., description="Gesamtladezeit in Minuten")
    start_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Start-SoC in %")
    ziel_soc_pct: float = Field(..., ge=0.0, le=100.0, description="Ziel-SoC in %")
    frames: list[FrameAPI]


class FrameAPI(BaseModel):
    zeitpunkt: datetime = Field(..., description="ISO-8601 Zeitpunkt")
    position: tuple[float, float] = Field(
        ...,
        description="(lat, lon), konsistent mit dem internen Domänenmodell — Konvertierung nach GeoJSON (lon, lat) erfolgt erst im Frontend",
    )
    soc_pct: float = Field(..., ge=0.0, le=100.0)
    zustand: str = Field(..., description="'FAHREN', 'LADEN' oder 'PAUSE'")
    geschwindigkeit_kmh: float = Field(..., ge=0.0)


# --- Kernfunktion (wird von API und CLI gemeinsam genutzt) ---
async def create_trip_simulation(request_dict: dict) -> TripSimulationResult:
    """
    Orchestriert die 11 Datenfluss-Schritte:
    1. OSM-Routing (inkl. Zwischenstopps als Pflicht-Waypoints)
    2. Höhenprofil extrahieren
    3. Route in Segmente unterteilen
    4. Initiale ETA je Segment (grobe Schätzung)
    5. Wetterdaten abrufen zu den groben ETAs
    6. Baustellen einbeziehen
    7. Energieverbrauch je Segment berechnen
    8. Optimalem Ladeplan bestimmen
    9. ETA mit tatsächlicher Fahr-/Ladezeit aktualisieren + Iteration
    10. Gesamtreise simulieren
    11. Ergebnisse vorbereiten

    Implementierung siehe Abschnitt 5.
    """
    raise NotImplementedError("Orchestrierung muss implementiert werden")


@app.post("/trips", response_model=TripSimulationResultAPI, status_code=201)
async def create_trip_endpoint(request: TripRequestAPI):
    """
    Erstellt eine neue Reise-Simulation.
    """
    try:
        result = await create_trip_simulation(request.model_dump())
        return TripSimulationResultAPI(
            gesamt_distanz_km=result.gesamt_distanz_km,
            gesamt_fahrzeit_min=result.gesamt_fahrzeit_min,
            gesamt_ladezeit_min=result.gesamt_ladezeit_min,
            start_soc_pct=result.start_soc_pct,
            ziel_soc_pct=result.ziel_soc_pct,
            frames=[
                FrameAPI(
                    zeitpunkt=f.zeitpunkt,
                    position=f.position,
                    soc_pct=f.soc_pct,
                    zustand=f.zustand.value,
                    geschwindigkeit_kmh=f.geschwindigkeit_kmh,
                )
                for f in result.frames
            ],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation fehlgeschlagen: {str(e)}")
```

---

## 5. Externe Integration / Algorithmus-Details

### 5.1 `simulation`-Modul: Algorithmen

#### 5.1.1 Zeitauflösung & Interpolation

**Zwei Ansätze, einheitlich über Flag steuerbar (`output_resolution_seconds` vs. `segment_boundaries_only`):**

1. **Segmentgrenzen + Ladestopps (Event-basiert):**
   - Jeder Segmentanfang/end + Ladehalt + Zwischenstopp wird zu einem Frame  
   - Dazwischen lineare Interpolation von Position und SoC  

2. **Regelmäßige Abtastrate (Default: 60s):**
   - Starte bei `abfahrtszeit`  
   - Für jedes Intervall `t .. t+60s`:
     - Bestimme aktuellen Segment (via `position` + `distances_cumsum`)
     - Wenn SoC ≥ 100% und kein Ladehalt → „PAUSE“ bis Ladehalt  
     - Wenn Ladehalt aktiv → „LADEN“, SoC↑ nach Ladekurve  
     - Sonst → „FAHREN“, SoC↓ nach Segmentenergie  

**Hilfsfunktion `interpolate_position(route: Route, distance: float) -> tuple[float, float]`:**
- `distances_cumsum = [0] + list(accumulate(r.gesamtlaenge_m for r in route.segments))`  
- Finde Segment `i` mit `distances_cumsum[i] ≤ distance < distances_cumsum[i+1]`  
- Interpoliere Anteil `α = (distance - distances_cumsum[i]) / route.segments[i].laenge_m`  
- `coords = route.segments[i].geometrie` (LineString)  
- `lat = coords[0][0] + α * (coords[-1][0] - coords[0][0])`  
- `lon = coords[0][1] + α * (coords[-1][1] - coords[0][1])`  
- Rückgabe `(lat, lon)` — konsistent mit `RouteSegment.geometrie` und allen Domänenmodellen; Konvertierung nach `(lon, lat)` erfolgt erst bei der GeoJSON-Erzeugung im Frontend (Abschnitt 5.2)

#### 5.1.2 Ladekurven-Interpolation

**`battery.ChargingCurve` + `ChargingCurvePoint`** (soc_pct, ladeleistung_kw):

```python
def interpolate_charging_power(curve: list[ChargingCurvePoint], soc: float) -> float:
    """Stückweise lineare Interpolation der Ladeleistung."""
    if soc <= curve[0].soc_pct:
        return curve[0].ladeleistung_kw
    if soc >= curve[-1].soc_pct:
        return curve[-1].ladeleistung_kw

    for i in range(len(curve) - 1):
        if curve[i].soc_pct <= soc <= curve[i + 1].soc_pct:
            alpha = (soc - curve[i].soc_pct) / (curve[i + 1].soc_pct - curve[i].soc_pct)
            return curve[i].ladeleistung_kw + alpha * (
                curve[i + 1].ladeleistung_kw - curve[i].ladeleistung_kw
            )
    raise RuntimeError("Unreachable")
```

#### 5.1.3 Ladedauer berechnen

```python
def compute_charge_duration(
    start_soc_pct: float,
    target_soc_pct: float,
    charging_curve: list[ChargingCurvePoint],
    nominal_power_kw: float,
    duration_seconds: int,
) -> float:
    """
    Berechnet, wie viel SoC in `duration_seconds` erreicht wird.
    Umgekehrt: Wie lange bis Ziel-SoC?
    """
    if target_soc_pct <= start_soc_pct:
        return 0.0

    # Energiebedarf ∫ power(SoC) dSoC
    energy_kwh = 0.0
    for i in range(len(charging_curve) - 1):
        soc1, p1 = charging_curve[i].soc_pct, charging_curve[i].ladeleistung_kw
        soc2, p2 = charging_curve[i + 1].soc_pct, charging_curve[i + 1].ladeleistung_kw

        if start_soc_pct >= soc2 or target_soc_pct <= soc1:
            continue  # Intervall nicht betroffen

        low = max(start_soc_pct, soc1)
        high = min(target_soc_pct, soc2)

        # Mittlere Ladeleistung im Segment
        p_avg = (p1 + p2) / 2.0
        energy_kwh += p_avg * (high - low) / 100.0  # (kW) * (SoC%-points) / 100

    # Dauer (Sekunden) = Energie / (power * Wirkungsgrad) – vereinfacht: power * 0.95
    efficiency = 0.95
    required_seconds = (energy_kwh * 3600) / (nominal_power_kw * efficiency)

    return min(required_seconds, duration_seconds)  # max. so lange wie verfügbar
```

### 5.2 `visualization`-Modul: MapLibre GL JS Setup

#### 5.2.1 Projektstruktur (empfohlen)

```
frontend/
├── src/
│   ├── main.tsx                # Entry-Point, MapLibre Worker Setup
│   ├── App.tsx
│   ├── components/
│   │   ├── Map.tsx             # MapLibre-Component (vgl. Beispiel)
│   │   ├── RouteLine.tsx       # Linie mit line-gradient SoC-Farbverlauf
│   │   ├── StopMarkers.tsx     # Marker für Ladehalte + Zwischenstopps
│   │   └── SocChart.tsx        # Line-Chart über Zeit vs. SoC (z. B. recharts)
│   └── types/
│       └── generated.ts        # Aus JSON-Schema generierte Typen (siehe unten)
└── package.json
```

#### 5.2.2 MapLibre `line-gradient` Syntax

**Konkrete Expression (aus Rechercheergebnis):**

```javascript
'line-gradient': [
  'interpolate',
  ['linear'],                   // Interpolationsart
  ['line-progress'],            // Input: 0 = Start, 1 = Ende der Linie
  0,   '#ef4444',               // Start: rot (SoC low)
  0.3, '#f59e0b',               // 30%: orange
  0.6, '#eab308',               // 60%: gelb
  1.0, '#22c55e'                // Ende: grün (SoC high)
]
```

**Wichtig:** Die `line-gradient` Expression **kann nicht direkt** auf GeoJSON-Properties verweisen (`['get', 'soc_start']`).  
**Workaround**: Die Farbstopps werden **vorab berechnet** und als LineString mit Properties ausgestattet.  
Aber: MapLibre GL JS erlaubt **nur line-progress** als Input für `line-gradient`.  
**Lösung**: Wir erzeugen eine **Farb- lookup-Funktion** auf Client-Seite.

**Empfohlene Umsetzung (durch Client-Logik, nicht MapLibre-Expression):**

1. Erzeuge `frames` aus `TripSimulationResult` → berechne `SoC` je km oder je `line-progress`  
2. Erstelle **eine Farb-Interpolationsfunktion** in JavaScript:

```typescript
function socToColor(soc: number): string {
  // Farbverlauf: rot → gelb → grün
  if (soc <= 10) return '#ef4444';
  if (soc <= 30) return '#f97316';
  if (soc <= 50) return '#eab308';
  if (soc <= 70) return '#84cc16';
  return '#22c55e';
}

// Erzeuge Farben je Frame
const colors = simulationResult.frames.map(f => socToColor(f.soc_pct));
```

3. Erstelle ein **GeoJSON LineString mit `line-stops` pseudo-Feature**  
   **Oder einfacher**: Erstelle **mehrere GeoJSON-Linien**, je eine mit einfarbigem Segment (vgl. „Workaround“ Suche).  
   **Praktikabel**: Erstelle **eine einzige LineString-GeoJSON**, berechne für jeden Frame die `line-progress` (0–1) und erstelle ein Array von Farben.  
   MapLibre unterstützt **keine data-driven line-gradient** → wir simulieren es mit **Segmentierung**:

```javascript
// Wir teilen die Route in 100 Segmente, jedes mit eigenem Layer
const segments = 100;
for (let i = 0; i < segments; i++) {
  const start = i / segments;
  const end = (i + 1) / segments;
  const soc = simulateSocAtProgress(start);
  const color = socToColor(soc);

  map.addLayer({
    id: `route-segment-${i}`,
    type: 'line',
    source: 'route-source',
    filter: ['all', ['==', '$type', 'LineString']],
    paint: {
      'line-color': color,
      'line-width': 4,
      'line-opacity': 0.9,
      'line-gradient': [
        'interpolate', ['linear'],
        ['line-progress'],
        start, color,
        end, color
      ]
    }
  });
}
```

**Aber das ist ineffizient! Besser: Nutze `line-dasharray` + `line-color` (kein Gradient)**  
→ **Endgültige Empfehlung (aus Recherche)**: Da `line-gradient` **keine data-driven styling** erlaubt, nutzen wir stattdessen:

- **Einfache Farbe je Linie** (kein Gradient), oder  
- **Mehrere Layers** mit `line-color`, je nach Segment  
- **Oder**: Nutze ein **rasterbasiertes Heatmap-Overlay** (nicht vorgesehen im Scope)  

**Final decision (basiert auf Recherche):**  
> Wir nutzen eine **LineString-GeoJSON mit `line-color` pro Segment**, wobei die Route in N Segmente (z. B. 100) unterteilt wird. Jedes Segment erhält eine Farbe basierend auf dem mittleren SoC in diesem Segment. MapLibre unterstützt kein data-driven `line-gradient`, daher ist dies der pragmatische Workaround.

#### 5.2.2b Koordinaten-Konvertierung an der Rendering-Grenze

Alle Backend-Modelle (API-Response, `SimulationFrame`, `ChargingStation`, `Waypoint`) liefern Koordinaten als `(lat, lon)` — identisch zur Konvention aller Python-Domänenmodelle. MapLibre GL JS (`setLngLat`, GeoJSON-`coordinates`) erwartet dagegen strikt `[lng, lat]`. Die Konvertierung erfolgt **ausschließlich** an dieser einen Stelle im Frontend, nirgends sonst:

```typescript
// frontend/src/utils/geo-utils.ts

/** Konvertiert eine Backend-Koordinate (lat, lon) in MapLibre-Reihenfolge [lng, lat]. */
export function toLngLat([lat, lon]: [number, number]): [number, number] {
  return [lon, lat];
}

/** Konvertiert eine Liste von Backend-Koordinaten für eine GeoJSON-LineString. */
export function routeToGeoJsonCoordinates(
  geometrie: [number, number][],
): [number, number][] {
  return geometrie.map(toLngLat);
}
```

Verwendung bei der Routen-GeoJSON-Erzeugung: `coordinates: routeToGeoJsonCoordinates(route.geometrie)`. Verwendung bei Markern: `.setLngLat(toLngLat(stop.standort.koordinate))`.

#### 5.2.3 Marker für Ladehalte + Zwischenstopps

```typescript
import { Marker } from 'maplibre-gl';
import { toLngLat } from './utils/geo-utils';

// Ladehalte (Tesla-Supercharger)
chargingStops.forEach(stop => {
  const el = document.createElement('div');
  el.className = 'marker-charger';
  el.innerHTML = '⚡'; // oder SVG-Icon

  new Marker(el)
    .setLngLat(toLngLat(stop.standort.koordinate))  // API liefert (lat, lon); toLngLat() konvertiert nach MapLibre-[lng, lat]
    .setPopup(new Popup().setHTML(`<h3>${stop.standort.name}</h3><p>SoC: ${stop.ankunfts_soc_pct.toFixed(0)}% → ${stop.ziel_soc_pct.toFixed(0)}%</p>`))
    .addTo(map);
});

// Zwischenstopps (optionaler Aufenthalt)
waypoints.forEach((wp, i) => {
  if (wp.aufenthaltsdauer_s) {
    const el = document.createElement('div');
    el.className = 'marker-waypoint';
    el.innerHTML = '📍';

    new Marker(el)
      .setLngLat(toLngLat(wp.koordinate))  // API liefert (lat, lon); toLngLat() konvertiert nach MapLibre-[lng, lat]
      .setPopup(new Popup().setHTML(`<p>Zwischenstopp ${i + 1}<br>Pause: ${wp.aufenthaltsdauer_s / 60} min</p>`))
      .addTo(map);
  }
});
```

### 5.3 JSON-Schema-Contract-Generierung (Backend → Frontend)

#### 5.3.1 Pydantic → JSON Schema

```python
# src/tripplanner/simulation/generate_schema.py
import json
from pathlib import Path
from tripplanner.simulation.models import TripSimulationResult, SimulationFrame
from tripplanner.optimization.models import ChargingStop


def generate_schema(output_dir: Path = Path("frontend/src/types")) -> None:
    """Generiert JSON Schema und convertiert zu TypeScript."""
    output_dir.mkdir(exist_ok=True)

    # Schema für TripSimulationResult (inkl. $defs)
    schema = TripSimulationResult.model_json_schema(by_alias=False, ref_template="#/$defs/{model}")

    # Speichern
    (output_dir / "simulation.schema.json").write_text(json.dumps(schema, indent=2))

    # Schema für ChargingStop (optional, für Anzeige)
    (output_dir / "charging.schema.json").write_text(
        json.dumps(ChargingStop.model_json_schema(by_alias=False), indent=2)
    )


if __name__ == "__main__":
    generate_schema()
```

#### 5.3.2 JSON Schema → TypeScript (cli-basiert)

**Installation:**

```bash
npm install -g json-schema-to-typescript
# oder lokal
npm install json-schema-to-typescript
```

**Generierung (nach Schema-Generierung):**

```bash
json2ts -i frontend/src/types/simulation.schema.json -o frontend/src/types/generated.ts
```

**Ergebnis-Datei `frontend/src/types/generated.ts` (Auszug):**

```typescript
export interface SimulationFrame {
  zeitpunkt: string;          // ISO 8601
  position: [number, number]; // [lat, lon] – vor MapLibre-Rendering mit toLngLat() aus geo-utils.ts konvertieren (siehe Abschnitt 5.2)
  soc_pct: number;
  zustand: 'FAHREN' | 'LADEN' | 'PAUSE';
  geschwindigkeit_kmh: number;
}

export interface TripSimulationResult {
  frames: SimulationFrame[];
  gesamt_distanz_km: number;
  gesamt_fahrzeit_min: number;
  gesamt_ladezeit_min: number;
  start_soc_pct: number;
  ziel_soc_pct: number;
}
```

**Integration in `package.json` Skript:**

```json
{
  "scripts": {
    "gen:types": "python -m tripplanner.simulation.generate_schema && json2ts -i frontend/src/types/simulation.schema.json -o frontend/src/types/generated.ts"
  }
}
```

---

## 6. Test-Strategie

### 6.1 `simulation`-Tests (Unit)

**Fixtures (in `tests/fixtures/simulation/`):**
- `route_segment_example.json`: 3 Segmente, Geometrie, Längen, Steigungen  
- `weather_samples_example.json`: 2 Wetterabfragepunkte, Temperatur/Wind  
- `energy_results_example.json`: Energiebedarf je Segment (kWh)  
- `charging_plan_example.json`: 2 Ladehalte, `ChargingStop`-Listen  
- `expected_frames_60s.json`: Erwartete Frames mit 60s-Auflösung (Referenz)  

**Testfälle (mindestens 3, incl. Grenzfälle):**

**Test 1: Einfache Simulation (keine Ladehalte)**

```python
def test_simulate_trip_no_charging():
    # Given: Route mit 3 Segmenten (10km, 5km, 8km), 0% Steigung, 120km/h Limit
    route = Route(segments=[...], gesamtlaenge_m=23_000, geometrie=[...])
    energy = [SegmentEnergyResult(segment_index=0, energiebedarf_kwh=1.5, rekuperation_kwh=0.0),
              SegmentEnergyResult(segment_index=1, energiebedarf_kwh=0.8, ...),
              SegmentEnergyResult(segment_index=2, energiebedarf_kwh=1.2, ...)]
    plan = ChargingPlan(ladehalte=[], gesamtreisezeit_s=820)  # 13.7 min Fahrzeit

    # When: Simulation mit start_soc_pct=80, output_resolution_seconds=60
    result = simulate_trip(route, plan, energy, [], start_soc_pct=80.0)

    # Then: 14 Frames (840s / 60s + 1), SoC endet bei ~65%, kein LADEN-Zustand
    assert len(result.frames) == 14
    assert result.frames[0].soc_pct == 80.0
    assert result.frames[-1].soc_pct < 70.0
    assert all(f.zustand == TripState.FAHREN for f in result.frames)
    assert result.gesamt_fahrzeit_min == pytest.approx(13.7, rel=0.01)
```

**Test 2: Simulation mit einem Ladehalt (Konvergenz)**

```python
def test_simulate_trip_with_charging():
    # Given: Ladehalt in Mitte der Route, SoC fällt auf 20%, Ziel-SoC=40%
    # ChargingCurve: [10% → 150kW], [30% → 120kW], [50% → 100kW], [80% → 60kW]
    plan = ChargingPlan(
        ladehalte=[
            ChargingStop(
                station=...,
                ankunfts_soc_pct=20.0,
                ziel_soc_pct=40.0,
                geschaetzte_ladedauer_s=1800,
                ankunftszeit=...,
                abfahrtszeit=...,
            )
        ],
        gesamtreisezeit_s=1200,
    )

    result = simulate_trip(route, plan, energy, [], start_soc_pct=80.0)

    # Then: mindestens ein LADEN-Frame, Gesamtreisezeit > Fahrzeit
    assert any(f.zustand == TripState.LADEN for f in result.frames)
    assert result.gesamt_ladezeit_min > 20.0
    assert result.frames[-1].soc_pct == pytest.approx(40.0, abs=0.5)
```

**Test 3: Grenzfall – SoC über 100% (Rekuperation nicht möglich)**

```python
def test_simulate_trip_soc_cap():
    # Given: Ladehalt mit Ziel-SoC=120% (falsch, aber Validierung testen)
    plan = ChargingPlan(
        ladehalte=[ChargingStop(..., ziel_soc_pct=120.0, ...)],
        gesamtreisezeit_s=1200
    )

    with pytest.raises(ValueError, match="SoC darf 100% nicht überschreiten"):
        simulate_trip(route, plan, energy, [], start_soc_pct=80.0)
```

**Test-Abgrenzung:**
- Unit-Tests: Alle oben (feste Eingaben, keine Mocks außer externer APIs)  
- Integrationstest (`@pytest.mark.integration`): Simuliere echte Route aus `routing`-Fixture, Wetter-Fixture, `optimization`-Output → komplette Pipeline

---

### 6.2 `visualization`-Tests (E2E, Playwright)

**Testfälle (Browser-basiert, `tests/e2e/visualization/`):**

**Test 1: Route mit Farbverlauf wird gerendert**

```typescript
test('route-line renders with gradient color segments', async ({ page }) => {
  await page.goto('/?demo=true'); // Demo-Mode mit vordefinierter Simulation

  // Warte auf Map-Initialisierung
  await page.waitForSelector('.maplibregl-map');

  // Prüfe, dass Route-Linie existiert (Layer 'route-segment-0' bis -N)
  const layers = await page.evaluate(() =>
    map.getStyle().layers.map((l: any) => l.id).filter((id: string) => id.startsWith('route-segment-'))
  );
  expect(layers.length).toBeGreaterThan(50); // mindestens 50 Segmente
});
```

**Test 2: Marker für Ladehalte werden angezeigt**

```typescript
test('charger markers appear on map', async ({ page }) => {
  await page.goto('/?demo=true');

  // Warte auf Marker-Container
  await page.waitForSelector('.marker-charger');

  const markers = await page.$$('.marker-charger');
  expect(markers.length).toBeGreaterThan(0);

  // Klicke auf einen Marker → Popup öffnet sich
  await markers[0].click();
  await page.waitForSelector('.maplibregl-popup');
});
```

**Test 3: SoC-Chart rendert Zeitreihe**

```typescript
test('soc chart displays time vs. SoC line', async ({ page }) => {
  await page.goto('/?demo=true');

  // Warte auf SVG-Chart (recharts)
  await page.waitForSelector('.recharts-line');

  const path = await page.getAttribute('.recharts-line path', 'd');
  expect(path).not.toBe(null);
  expect(path!.length).toBeGreaterThan(100); // mindestens eine Kurve
});
```

**Test-Abgrenzung:**
- Unit-Tests (Jest/Vitest): Nur `socToColor`, `interpolatePosition` → geringer Aufwand  
- E2E (Playwright): Ganze Applikation, Test-Server, demo-Mode, Snapshots vergleichen  

---

### 6.3 `trip_input`-Tests (Integration)

**Testfälle:**

**Test 1: API-Endpunkt gibt korrektes JSON zurück**

```python
def test_api_create_trip_endpoint(client: AsyncClient):
    # Given: Payload gemäß Test-Fixture
    payload = {
        "start": [8.6821, 50.1109],   // Frankfurt
        "ziel": [11.5820, 48.1351],   // München
        "zwischenstopps": [],
        "abfahrtszeit": "2026-08-15T08:00:00",
        "fahrzeugprofil": "model3_standard",
        "praeferenzen": {}
    }

    # When: POST /trips
    response = await client.post("/trips", json=payload)
    assert response.status_code == 201

    data = response.json()
    // Then: Response entspricht TripSimulationResultAPI
    assert "frames" in data
    assert len(data["frames"]) > 0
    assert data["gesamt_fahrzeit_min"] > 0
    assert data["gesamt_ladezeit_min"] >= 0
```

**Test 2: CLI gibt JSON auf stdout aus (Regression)**

```python
def test_cli_trips_output(capsys):
    // Given: Mocked API-Aufruf (via subprocess)
    result = subprocess.run(
        ["python", "-m", "tripplanner.cli", "trips",
         "--start", "8.6821,50.1109",
         "--ziel", "11.5820,48.1351",
         "--abfahrtszeit", "2026-08-15T08:00:00"],
        capture_output=True, text=True
    )

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "frames" in data
    assert isinstance(data["frames"], list)
```

**Test-Abgrenzung:**
- Unit: API-Schema-Validierung (`TripRequestAPI.model_validate()`)  
- Integration: E2E mit simulierten Backend-Modulen (Mocked, keine echten API-Calls)  

---

## 7. Aufgaben-Checkliste (TDD-Reihenfolge)

### 7.1 `simulation`-Modul

- [ ] **Task 1 (simulation/models.py):** Pydantic-Modelle `SimulationFrame`, `TripSimulationResult`, `TripState`-Enum implementieren, Validatoren hinzufügen (SoC-Bereich, Zustand-Geschwindigkeit-Konsistenz)  
  - Akzeptanz: `pytest tests/simulation/test_models.py` läuft, alle Validierungen testen

- [ ] **Task 2 (simulation/simulate.py – Funktionsskelett):** `simulate_trip()`-Funktionssignatur implementieren, docstring, doc-tests  
  - Akzentration: `pytest tests/simulation/test_simulate.py::test_function_signature` ✓

- [ ] **Task 3 (simulation/simulate.py – Position-Interpolation):** `interpolate_position()` implementieren, Tests mit 3 Segmenten (gerade, krumm, 0-Länge)  
  - Akzeptanzkriterium: `(lat, lon)`-Reihenfolge (konsistent mit Domänenmodell), Linear Interpolation, Segment-Index-Berechnung; GeoJSON-Konvertierung `(lon, lat)` erfolgt separat im Frontend

- [ ] **Task 4 (simulation/simulate.py – Energieverbrauch pro Zeitschritt):** `compute_energy_for_interval()` → Segment-Energie auf Teilintervall aufteilen  
  - Akzentration: Linear interpolation of distance, energy proportionally

- [ ] **Task 5 (simulation/simulate.py – Ladekurven-Interpolation):** `interpolate_charging_power()` implementieren, Tests für Grenzfälle (soC < min, > max)  
  - Akzentration: Piecewise linear, Input validation

- [ ] **Task 6 (simulation/simulate.py – Gesamtsimulation):** `simulate_trip()` vollständig implementieren, alle 11 Schritte aufrufen  
  - Akzentration: Schleife über Zeitintervalle, SoC-Update, Ladezustand-Auswahl, Frame-Erzeugung

- [ ] **Task 7 (simulation/fixtures):** Fixtures für 2 Szenarien erstellen (ohne Ladehalt, mit Ladehalt), Referenz-Frames erstellen  
  - Akzentration: JSON-Files, manuell validiert

- [ ] **Task 8 (simulation/test_simulate.py):** 3 Testfälle implementieren (ohne Ladehalt, mit Ladehalt, SoC-Cap)  
  - Akzentration: `pytest --cov=tripplanner.simulate --cov-report=term-missing` → Coverage ≥ 85%

---

### 7.2 `visualization`-Frontend (React + TypeScript)

- [ ] **Task 9 (frontend/setup):** Vite + React + TypeScript projekt initialisieren, MapLibre GL JS installieren (`?worker&url` Setup!)  
  - Akzentration: `vite.config.ts`, `tsconfig.json`, `main.tsx` mit Worker-URL

- [ ] **Task 10 (frontend/types/):** JSON-Schema generieren (`python -m tripplanner.simulation.generate_schema`), TypeScript-Typen generieren (`json2ts`)  
  - Akzentration: `npm run gen:types`, Commit `generated.ts`

- [ ] **Task 11 (frontend/components/Map.tsx):** Minimal Map-Component mit OpenFreemap-Style  
  - Akzentration: `map.on('load')`, Baseline Setup

- [ ] **Task 12 (frontend/components/RouteLine.tsx):** Route-Line-Component mit Segmentierung (keine echte Gradient, sondern Farbsegmente)  
  - Akzentration: `map.addSource('route', { type: 'geojson', data: geojson })`, Loop `route-segment-0..N` Layers erzeugen, Farbe je Segment basierend auf SoC-Zwischenwerten

- [ ] **Task 13 (frontend/components/StopMarkers.tsx):** Marker für Ladehalte + Zwischenstopps (Icon + Popup)  
  - Akzentration: `Marker().setLngLat().addTo(map)`, Popup-Inhalt dynamisch

- [ ] **Task 14 (frontend/components/SocChart.tsx):** Zeitreihe (X: Zeit, Y: SoC) als Line-Chart (recharts)  
  - Akzentration: `data={frames.map(f => ({ time: f.zeitpunkt, soc: f.soc_pct }))}`, recharts `Line` Component

- [ ] **Task 15 (frontend/App.tsx):** Komponenten zusammensetzen, Demo-Mode (feste Simulation), API-Mode (fetch `/trips`)  
  - Akzentration: State-Management (Context oder Zustand in `App`)

- [ ] **Task 16 (frontend/e2e/):** Playwright Tests (Route-Linie, Marker, SoC-Chart) implementieren  
  - Akzentration: `npx playwright codegen`, Snapshots vergleichen

---

### 7.3 `trip_input`/API-Schicht

- [ ] **Task 17 (trip_input/api.py):** FastAPI-App mit `POST /trips` Endpunkt (Request/Response-Modelle)  
  - Akzentration: `TripRequestAPI`, `TripSimulationResultAPI` Pydantic-Modelle, Status Code 201

- [ ] **Task 18 (trip_input/api.py):** `create_trip_simulation()`-Funktion implementieren (Orchestrierung der 11 Schritte)  
  - Akzentration: Aufruf `routing`, `elevation`, `weather`, `energy`, `optimization`, `simulation` Module in korrekter Reihenfolge

- [ ] **Task 19 (trip_input/api.py):** Fehlerbehandlung (HTTPException bei Fehlern)  
  - Akzentration: Try/Catch, Status 500, sinnvolle Fehlermeldung

- [ ] **Task 20 (cli/main.py):** Typer-CLI mit `trips` Command implementieren, Eingabeparameter parsen (Koordinaten, Zwischenstopps, Abfahrtszeit)  
  - Akzentration: `typer.Option`, `parse_coord`, `parse_waypoint`

- [ ] **Task 21 (cli/main.py):** Aufruf `create_trip_simulation(request_dict)`, JSON-Ausgabe (stdout oder Datei)  
  - Akzentration: `json.dumps`, `output_json.write_text`

- [ ] **Task 22 (tests/test_api.py):** API-Integrationstest (`test_api_create_trip_endpoint`)  
  - Akzentration: `client = AsyncClient(app)`, `await client.post("/trips", json=payload)`

- [ ] **Task 23 (tests/test_cli.py):** CLI-Integrationstest (`test_cli_trips_output`)  
  - Akzentration: `subprocess.run`, `json.loads(result.stdout)`

- [ ] **Task 24 (docs/plans/08-simulation-visualization-api.md):** Dieses Dokument vervollständigen (jetzt).  
  - Akzentration: Recherche, Struktur, Details, Tests.

---

## 8. Risiken & offene technische Fragen

### 8.1 Simulation

| Risiko | Auswirkung | Abwehrmaßnahme |
|--------|------------|----------------|
| **Position-Interpolation entlang kurviger Segmente** | Positionen zu ungenau bei stark gekrümmten Straßen | Nutze `shapely` für Geometrie-Interpolation (besser als Lineare) – **empfohlene Ergänzung**, falls Zeit | 
| **Ladekurve außerhalb der Tabellengrenzen (extrapolieren)** | Falsche Ladedauer, SoC-Über- oder -Unterschreitung | `ValueError` bei Extrapolation, Default-SoC=100% nach Ladehalt |

### 8.2 Visualization

| Risiko | Auswirkung | Abwehrmaßnahme |
|--------|------------|----------------|
| **MapLibre GL JS supportiert `line-gradient` nicht data-driven** | Kein SoC-Farbverlauf entlang Linie, nur Farbsegmente | Akzeptiere Segmentierung, dokumentiere Limitation, evtl. später Migration zu Vector Tiles mit data-driven styling |

### 8.3 trip_input / API

| Risiko | Auswirkung | Abwehrmaßnahme |
|--------|------------|----------------|
| **Iterative ETA/Wetter-Konvergenz in `create_trip_simulation()` fehlt** | Wetter und ETA inkonsistent, falsche Energie-Berechnung | Implementiere exakt die 6-Schritte-Schleife aus `02-architektur.md`, Schwellwert konfigurierbar (`convergence_threshold_minutes`) |

### 8.4 Offene technische Fragen (nicht durch offene Punkte abgedeckt)

1. **Should `simulation`-Modul `tripplanner.weather.models.WeatherSample` komplett übernehmen oder nur Temperature/Wind?**  
   → **Entscheidung:** Nur diejenigen Wetter-Parameter übernehmen, die das `energy`-Modul benötigt (vorerst nur Temperatur, Windgeschwindigkeit/-richtung), andere im `WeatherSample` ignorieren. Spätere Erweiterung ohne Breaking Changes möglich.

2. **Wie wird die Route aufgelöst, wenn Zwischenstopps enthalten sind?**  
   → **Entscheidung:** `Route.segments` enthält bereits alle Zwischenstopp-Koordinaten als Segmentgrenzen. Simulation iteriert einfach über alle Segmente. Kein extra Handling nötig.

3. **Soll `TripSimulationResult` auch die `ChargingPlan`-Informationen enthalten (z. B. zur Nachvollziehbarkeit)?**  
   → **Entscheidung:** Nein, `TripSimulationResult` ist reine Zeitreihe (`frames`). `ChargingPlan` bleibt im `optimization`-Modul. Bei Bedarf kann eine Erweiterung (`frames_with_plan: list[tuple[SimulationFrame, list[ChargingStop]]]`) später eingeführt werden.

4. **Soll die CLI eine Option `--output-format` unterstützen (JSON, CSV, GeoJSON)?**  
   → **Entscheidung:** Nein, scope limitieren auf JSON (einfachste Interoperabilität). CSV/GeoJSON können als spätere Erweiterung via Addon-Modul (`tripplanner.export`) erfolgen.

5. **Soll die API CORS erlauben (für externe Clients)?**  
   → **Entscheidung:** Nein, standardmäßig blockiert (`CORSMiddleware` nicht aktiviert). Lokale Entwicklung erlaubt `--cors` Flag für Dev-Mode, nicht für Produktion.

---

## 9. Zusammenfassung

Dieser Plan definiert die Implementierung von `simulation` (Phase 6→7), `visualization` (Frontend, Phase 7) und `trip_input`/API (Phase 7) als eng miteinander verknüpfte Komponenten:

- **`simulation`** erzeugt eine Zeitreihe aus fixierter Route und Ladeplan via diskreter Zeitschritte (60s-Default) mit linearer Position-Interpolation und SoC-Berechnung (Entladung nach `energy`, Ladekurven nach `battery`).  
- **`visualization`** nutzt Vite + TypeScript + MapLibre GL JS, nutzt JSON-Schema-Generierung (`pydantic.model_json_schema()` → `json-schema-to-typescript`) für TS-Typen, nutzt Segmentierung für Route-Farbverlauf (Workaround für fehlende data-driven `line-gradient`).  
- **`trip_input`/API** bietet FastAPI-Endpunkt (`POST /trips`) und Typer-CLI (`python -m tripplanner.cli trips …`), orchestriert alle 11 Schritte inkl. iterativer ETA/Wetter-Auflösung.  
- **Tests** folgen dem TDD-Muster (Test zuerst), Coverage-Gate 85% (`simulation`), Playwright E2E (`visualization`), Integrationstests (`trip_input`).  
- **Risiken** sind identifiziert und mit Abwehrmaßnahmen belegt (z. B. `line-gradient` Limitation akzeptiert, `convergence_threshold_minutes` konfigurierbar).

Der Plan ist vollständig, umsetzungsreif und deckt alle Anforderungen aus den Dokumenten `01-projektspezifikation.md`, `02-architektur.md`, `03-modulspezifikationen.md`, `06-offene-punkte-widersprueche.md` ab.

---

**Ende des Plans.**