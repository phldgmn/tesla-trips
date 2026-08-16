# Implementierungsplan: `optimization`-Modul (Phase 5)

## 1. Zweck & Scope

Das `optimization`-Modul berechnet den optimalen Ladeplan für eine Tesla-Reise entlang einer bereits von GraphHopper festgelegten Route. Es löst ein diskretisiertes Zustandsraum-Suchproblem, das die folgenden Dimensionen berücksichtigt:

- **Position**: Segment-Index der Route (diskret, keine Neuberechnung der Straßenroute)
- **Batteriezustand (SoC)**: Diskretisiert in Buckets (z. B. 1%-Schritte, 0–100%)
- **Zeit**: Diskretisiert in Buckets (z. B. 15-min-Schritte, gerundet auf volle Viertelstunde)

Die Kostenfunktion minimiert Gesamtreisezeit (Fahrzeit + Ladezeit) unter Beachtung fester Nebenbedingungen und optionaler Strafkosten für Constraint-Verletzungen.

### Abgrenzung

- **KEIN energieoptimales Rerouting**: Die Route ist von Phase 1 (GraphHopper) fixiert; das Modul plant nur Ladehalte und Geschwindigkeitsprofile entlang der vorgegebenen Strecke.
- **Zwischenstopps sind Pflicht-Knoten**: Wie in `06-offene-punkte-widersprueche.md` entschieden (Variante a), sind Zwischenstopps eigenständige Waypoints mit optionaler Mindestaufenthaltsdauer — sie müssen im Zustandsgraph als explizite Knoten mit Zeitsperren modelliert werden.
- **Keine Iteration in der Optimierung**: Die Iterative ETA/Wetter-Konvergenz erfolgt in der übergeordneten Orchestrierungsschicht (`trip_input`/API); das `optimization`-Modul erhält als Eingabe eine konsistente Wetter-/ETA-Situation.

### Nicht-Scope

- Keine Erstellung/aktualisierte Pflege der Tesla-Supercharger-Datenbank (Provider-Interface vorgesehen, Datenquelle lokal/fix)
- Keine personalisierte Kalibrierung des Verbrauchsmodells (feste Defaults gemäß `vehicle_energy_parameters`-Pydantic-Modell)
- Keine Echtzeit-Neuplanung bei Fahrtfehlern (Simulation ist zukünftig vorgesehen, aber keine Live-Anpassung)

---

## 2. Abhängigkeiten & Phasenzuordnung

- **Phase**: 5 (Zentrale Optimierung)
- **Phasen-Voraussetzung**: Phase 4 (battery) muss abgeschlossen sein (Ladekurven-Interface), Phase 3 (energy) muss Segmente mit Energiebedarf liefern.
- **Konsumierte Typen** (exakt aus kanonischem Register):
  - `tripplanner.routing.models.Route`, `tripplanner.routing.models.RouteSegment`
  - `tripplanner.elevation.models.SegmentGradient`
  - `tripplanner.weather.models.WeatherSample` (via `tripplanner.wind.models.WindComponents`)
  - `tripplanner.construction.models.ConstructionZone`
  - `tripplanner.energy.models.SegmentEnergyResult`
  - `tripplanner.battery.models.SoCState`, `tripplanner.battery.models.ChargingCurve`
  - `tripplanner.charging_infrastructure.models.ChargingStation`
  - `tripplanner.trip_input.models.VehicleProfile`, `tripplanner.trip_input.models.TripRequest`, `tripplanner.trip_input.models.Waypoint`
  - `tripplanner.optimization.models.OptimizationConstraints`, `tripplanner.optimization.models.ChargingPlan`, `tripplanner.optimization.models.ChargingStop`

---

## 3. Datenmodelle

### Pydantic-Modelle (in `src/tripplanner/optimization/models.py`)

```python
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Literal
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
from tripplanner.trip_input.models import Waypoint, VehicleProfile
from tripplanner.routing.models import RouteSegment
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.elevation.models import SegmentGradient


class ChargingStop(BaseModel):
    """
    Ein Ladehalt mit Station, Ankunfts- und Ziel-SoC sowie Zeitangaben.
    """

    station: ChargingStation
    segment_index: Annotated[int, Field(ge=0)]
    ankunfts_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)]
    ziel_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)]
    geschaetzte_ladedauer_s: Annotated[int, Field(ge=0)]
    ankunftszeit: datetime
    abfahrtszeit: datetime

    @field_validator("ankunfts_soc_pct", "ziel_soc_pct")
    @classmethod
    def validate_soc_range(cls, v: float) -> float:
        if v < 0.0 or v > 100.0:
            raise PydanticCustomError(
                "soc_range_error",
                "SoC muss zwischen 0.0 und 100.0 liegen, ist aber {value}",
                {"value": v},
            )
        return v


class OptimizationConstraints(BaseModel):
    """
    Harte Constraints und Sicherheitsparameter für die Optimierung.
    """

    min_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)] = Field(
        default=15.0,
        description="Minimal zulässiger SoC (Sicherheitsreserve)",
    )
    ziel_soc_pct: Annotated[float, Field(ge=0.0, le=100.0)] = Field(
        default=80.0,
        description="gewünschter SoC am Ziel",
    )
    max_etappenlaenge_km: Annotated[float, Field(gt=0.0)] = Field(
        default=500.0,
        description="maximale Distanz zwischen Ladestopps (optional)",
    )
    sicherheitsreserve_pct: Annotated[float, Field(ge=0.0, le=20.0)] = Field(
        default=5.0,
        description="Reserve auf dem Ziel-SoC (z. B. Ziel-SoC = 80%, Reserve = 5% → faktischer Ziel-SoC = 75%)",
    )
    max_ladezeit_s: Annotated[int, Field(ge=600, le=7200)] = Field(
        default=3600,
        description="maximale Dauer eines einzelnen Ladevorgangs (optional)",
    )


class ChargingPlan(BaseModel):
    """
    Ergebnis der Optimierung: geordnete Liste von Ladehalten + Gesamtreisezeit.
    """

    ladehalte: list[ChargingStop]
    gesamtreisezeit_s: Annotated[int, Field(ge=0)]
    min_zwischenstopp_ankunftszeit: dict[int, datetime] = Field(
        default_factory=dict,
        description="Mindestankunftszeit für Zwischenstopps (wenn nicht geladen wird)",
    )


class StateNode(BaseModel):
    """
    Interner Knoten im Zustandsgraphen: (segment_index, soc_bucket, time_bucket).
    Wird nicht als Pydantic-Exportmodell verwendet, dient nur interner Darstellung.
    """

    segment_index: int
    soc_pct: float  # diskretisiert
    zeitpunkt: datetime


class OptimizerInterface:
    """
    Protocol/Interface für Austauschbarkeit zwischen NetworkX (Prototyp)
    und OR-Tools (Produktion, spätere Ausbaustufe).
    """

    def optimize(
        self,
        route: "Route",
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list["SegmentEnergyResult"],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """
        Optimierungsmethode, die von beiden Backend-Implementierungen
        (NetworkXOptimizer, ORToolsOptimizer) bereitgestellt wird.
        """
        raise NotImplementedError
```

### Ergänzende Typen (nicht im Register enthalten, aber intern benötigt)

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class LadekurvenLookup:
    """
    Hilfsstruktur für Ladekurven-Interpolation: SoC-Grad → Ladeleistung (kW).
    """

    soc_pct: float
    ladeleistung_kw: float
```

---

## 4. Öffentliche Schnittstelle

### Dateistruktur

```
src/tripplanner/optimization/
├── __init__.py        # re-exportiert OptimizerInterface + public models
├── models.py           # siehe oben
├── optimizer.py        # Kern-Logik: NetworkX- und OR-Tools-Implementierungen
├── discretizer.py      # Diskretisierung von SoC und Zeit
└── validation.py       # Validierung von Eingabedaten (Routenlücke, SoC-Bereich etc.)
```

### Öffentliche API (`src/tripplanner/optimization/__init__.py`)

```python
from tripplanner.optimization.models import (
    ChargingStop,
    OptimizationConstraints,
    ChargingPlan,
    OptimizerInterface,
)
from tripplanner.optimization.optimizer import (
    create_networkx_optimizer,
    create_ortools_optimizer,
)

__all__ = [
    "ChargingStop",
    "OptimizationConstraints",
    "ChargingPlan",
    "OptimizerInterface",
    "create_networkx_optimizer",
    "create_ortools_optimizer",
]
```

### Öffentliche Funktionen (`optimizer.py`)

```python
from tripplanner.routing.models import Route
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult
from tripplanner.trip_input.models import Waypoint, VehicleProfile
from tripplanner.charging_infrastructure.models import ChargingStation
from tripplanner.optimization.models import (
    OptimizationConstraints,
    ChargingPlan,
    OptimizerInterface,
    LadekurvenLookup,
)


def create_networkx_optimizer(
    soc_step_pct: float = 1.0,
    time_step_min: int = 15,
) -> OptimizerInterface:
    """
    Factory-Funktion für den NetworkX-basierten Prototyp-Optimizer.
    """
    from tripplanner.optimization.optimizer import NetworkXOptimizer

    return NetworkXOptimizer(
        soc_step_pct=soc_step_pct,
        time_step_min=time_step_min,
    )


def create_ortools_optimizer(
    soc_step_pct: float = 1.0,
    time_step_min: int = 15,
    use_cp_sat: bool = True,
) -> OptimizerInterface:
    """
    Factory-Funktion für den OR-Tools-basierten Optimizer (spätere Version).
    use_cp_sat=True → CP-SAT Solver, False → Routing Solver.
    """
    from tripplanner.optimization.optimizer import ORToolsOptimizer

    return ORToolsOptimizer(
        soc_step_pct=soc_step_pct,
        time_step_min=time_step_min,
        use_cp_sat=use_cp_sat,
    )


class NetworkXOptimizer(OptimizerInterface):
    """A*/Dijkstra-Optimierung mit NetworkX (Prototyp)."""

    def __init__(
        self,
        soc_step_pct: float = 1.0,
        time_step_min: int = 15,
    ):
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min

    def optimize(
        self,
        route: Route,
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """
        Optimiert Ladeplan unter Verwendung eines diskretisierten Zustandsgraphen.
        A*-Suche mit Heuristik = verbleibende Distanz / geschätzte Reisegeschwindigkeit.
        """
        # Implementierung siehe Abschnitt 5
        pass


class ORToolsOptimizer(OptimizerInterface):
    """OR-Tools-basierter Optimizer (CP-SAT oder Routing Solver)."""

    def __init__(
        self,
        soc_step_pct: float = 1.0,
        time_step_min: int = 15,
        use_cp_sat: bool = True,
    ):
        self.soc_step_pct = soc_step_pct
        self.time_step_min = time_step_min
        self.use_cp_sat = use_cp_sat

    def optimize(
        self,
        route: Route,
        segments: list[RouteSegment],
        gradients: list[SegmentGradient],
        energy_results: list[SegmentEnergyResult],
        charging_stations: list[ChargingStation],
        waypoints: list[Waypoint],
        vehicle_profile: VehicleProfile,
        constraints: OptimizationConstraints,
        start_soc_pct: float,
        abfahrtszeit: datetime,
        iteration: int = 1,
    ) -> ChargingPlan:
        """
        Optimiert Ladeplan mittels Constraint-Programmierung (CP-SAT) oder
        Routing-Solver (bei größeren Instanzen).
        """
        # Implementierung siehe Abschnitt 5
        pass
```

---

## 5. Externe Integration / Algorithmus-Details

### 5.1 Zustandsraum-Diskretisierung (`discretizer.py`)

**SoC-Diskretisierung**: `soc_bucket = round(soc_pct / soc_step_pct)` → 0–100 in Schritten von z. B. 1% (101 Buckets)

**Zeit-Diskretisierung**: `zeit_bucket = round(zeitpunkt / timedelta(minutes=time_step_min)) * timedelta(minutes=time_step_min)` → gerundet auf volle Viertelstunde (15 Min)

**Knoten-Hash**: `(segment_index, soc_bucket, zeit_bucket)` als Dictionary-Key

### 5.2 Kostenfunktion

```
Kosten(Kante) = fahrzeit_segment + ladezeit + umweg_kosten + constraint_strafe

mit:
- fahrzeit_segment = segment_laenge_m / (geschwindigkeit_kmh * 1000/3600)
- ladezeit = (ziel_soc_pct - ankunfts_soc_pct) / (ladeleistung_kw * 0.01) * batteriekapazitaet_kwh * 3600
- umweg_kosten = 0 (da keine Rerouting-Alternativen, nur Ladehalt-Planung)
- constraint_strafe = 0 (falls Constraint erfüllt), else M (große Konstante, z. B. 10^6)
```

**Heuristik (A*)**: `h(n) = verbleibende_distanz_m / (reisegeschwindigkeit_kmh * 1000/3600)`

**Reisegeschwindigkeit**: vorerst als Default 110 km/h (Autobahn) festgelegt; spätere Anpassung über Tempolimits der Segmente.

### 5.3 A*/Dijkstra-Algorithmus (NetworkX-Implementierung)

**Graphaufbau** (in `optimizer.py`):

```python
import networkx as nx

class NetworkXOptimizer(OptimizerInterface):
    def optimize(...):
        # 1. Erstelle gerichteten Graph G = nx.DiGraph()
        G = nx.DiGraph()

        # 2. Erstelle Startknoten (segment_index=0, soc=start_soc, zeit=abfahrtszeit)
        start_node = (0, self._soc_to_bucket(start_soc_pct), self._zeit_to_bucket(abfahrtszeit))
        G.add_node(start_node, type="start", soc_pct=start_soc_pct, zeitpunkt=abfahrtszeit)

        # 3. Füge Knoten für alle Segmente, SoC-Buckets und Zeit-Buckets hinzu
        #    (lazy: nur erreichbare Knoten erzeugen)

        # 4. Füge Kanten hinzu:
        #    - Fahrtkante: (seg, soc, zeit) → (seg+1, soc_drops, zeit+delta_t)
        #    - Ladekante: (seg, soc, zeit) → (seg, soc+charging, zeit+delta_tau)
        #    - Zwischenstopp-Zwang: (seg, soc, zeit) → (seg+1, soc, zeit+min_aufenthalt)

        # 5. Füge Ladekanten zu allen verfügbaren ChargingStationen hinzu
        #    (nur wenn Station in Reichweite oder im Segment-Intervall)

        # 6. Führe A*-Suche aus:
        path = nx.astar_path(
            G,
            source=start_node,
            target=self._ziel_knoten,
            heuristic=lambda u, v: self._heuristik(u, v),
            weight="cost",
        )

        # 7. Extrahiere ChargingStop-Objekte aus dem Pfad
        #    (Ladekanten erkennen und zu ChargingStop umwandeln)

        # 8. Berechne Gesamtreisezeit = letzter Knoten.zeitpunkt + restliche_fahrzeit
        #    (falls Ziel nicht im letzten Segment erreicht wird)

        return ChargingPlan(ladehalte=ladehalte, gesamtreisezeit_s=gesamtzeit)
```

**Zielknoten**: `(segment_index=len(segments)-1, soc_pct >= ziel_soc_pct, beliebige Zeit)`

**Heuristik-Funktion (in `NetworkXOptimizer`)**:

```python
def _heuristik(self, u: tuple, v: tuple) -> float:
    """
    Admissible Heuristik: Zeit bis zum Ziel unter idealen Bedingungen.
    """
    u_seg, u_soc, u_zeit = u
    v_seg, v_soc, v_zeit = v

    # Distanz von v_seg bis zum Ende
    rest_distanz = sum(seg.laenge_m for seg in self.segments[v_seg:])

    # Idealgeschwindigkeit (Autobahn, 110 km/h)
    v_ideal_mps = 110.0 * 1000 / 3600

    # Zeitdauer
    rest_zeit_s = rest_distanz / v_ideal_mps

    return rest_zeit_s
```

### 5.4 OR-Tools-Implementierung (CP-SAT vs. Routing Solver)

**CP-SAT (Constraint Programming SAT Solver)**: Für kleine bis mittlere Instanzen (<50 Ladestationen). Modelliert als ganzzahlige lineare Optimierung mit booleschen Variablen für "laden an Station i".

**Routing Solver**: Für große Instanzen (>50 Ladestationen). Modelliert als Vehicle Routing Problem mit Time Windows (VRPTW) und zusätzlichen Constraints für SoC.

**Recherche-Ergebnis** (s. Websuche): CP-SAT ist für allgemeine Integer-Probleme und kleineren Instanzen besser geeignet; Routing Solver ist spezialisiert auf Route-Probleme mit LNS-Heuristik. Für den Prototyp und mittlere Reisen (ca. 300–600 km, ≤20 Ladestationen entlang der Route) bietet sich CP-SAT an. Spätere Ausbaustufe mit großem Datensatz → Routing Solver.

**OR-Tools-Modell (CP-SAT-Pseudocode)**:

```python
from ortools.sat.python import cp_model

def optimize(...):
    model = cp_model.CpModel()

    # Variablen
    laden_an_station[i] = model.NewBoolVar(f"laden_{i}")
    ankunfts_soc[i] = model.NewIntVar(0, 100, f"ankunfts_soc_{i}")
    abfahrts_soc[i] = model.NewIntVar(0, 100, f"abfahrts_soc_{i}")
    ladezeit[i] = model.NewIntVar(0, 3600, f"ladezeit_{i}")
    ankunfts_zeit[i] = model.NewIntVar(int(abfahrtszeit.timestamp()), ..., f"ankunfts_{i}")

    # Constraints
    # 1. SoC-Konsistenz: ankunfts_soc[i+1] = abfahrts_soc[i] - energiebedarf[i→i+1]
    # 2. Ladezeit-Berechnung: ladezeit[i] = (abfahrts_soc[i] - ankunfts_soc[i]) * faktor
    # 3. Mindest-SoC: ankunfts_soc[i] >= min_soc_pct
    # 4. Zwischenstopp-Zeitfenster: ankunfts_zeit[i] ≥ ziel_ankunftszeit[i]
    # 5. Ziel-SoC: ankunfts_soc[last] >= ziel_soc_pct

    # Objective: minimize sum(ladezeit) + sum(fahrzeit)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
    result = solver.Solve(model)

    if result == cp_model.OPTIMAL or result == cp_model.FEASIBLE:
        # Extrahiere Lösung und baue ChargingPlan
        pass
```

### 5.5 Externe APIs / Bibliotheken

| Komponente | Bibliothek | Version | Verwendung |
| ----------- | ----------- | --------- | ------------ |
| NetworkX | `networkx` | ≥3.0 | A*/Dijkstra auf DiGraph |
| OR-Tools | `ortools` | ≥9.10 | CP-SAT Solver (oder Routing Solver) |
| Wetter/Ladekurven | `tripplanner.weather`, `tripplanner.battery` | eigene Module | Datenimport via models |
| DEM-Zugriff | `rasterio` | ≥1.3 | nicht direkt im optimization-Modul, nur über energy/battery |

**Abhängigkeiten in `pyproject.toml`**:

```toml
[project.dependencies]
networkx = "^3.0"
ortools = "^9.10"
rasterio = "^1.3"  # über elevation-Modul, aber hier aufgeführt für Vollständigkeit
```

---

## 6. Test-Strategie

### Fixtures (in `tests/fixtures/optimization/`)

- `route_sechs_segmente.json`: 6 Segmente, Länge insgesamt ca. 300 km, Tempolimits zwischen 80–130 km/h
- `charging_stations_3.json`: 3 Supercharger entlang der Route (Segment 1, 3, 5)
- `waypoints_1.json`: 1 Zwischenstopp (Segment 2, 15-min-Pause)
- `energy_segments_6.json`: Energiebedarf je Segment (berechnet mit Referenzfahrzeug)
- `ladekurve_v3.json`: V3-Ladekurve (10→80% in ca. 30 Min, Piezokurve modelliert)

### Beispiel-Testfälle (Unit-Tests in `tests/optimization/test_optimizer.py`)

#### Test 1: Einfache Route, keine Ladehalt nötig (Given/When/Then)

```
Given: Route mit 3 Segmenten (100 km), Start-SoC = 100%, Ziel-SoC = 60%, Minimal-SoC = 15%
When: Energiebedarf je Segment < 10% (insgesamt < 30% SoC-Verbrauch)
Then: Kein Ladehalt im Ergebnis, Gesamtreisezeit = fahrzeit, Ankunfts-SoC ≥ Ziel-SoC
```

```python
def test_kein_ladehalt_noetig():
    # Setup: kleine Route, geringer Verbrauch
    route = _create_route(3, [100_000, 100_000, 100_000])  # 3 × 100 km
    gradients = _create_gradients(3, [0.0, 0.0, 0.0])
    energy = _create_energy([5.0, 5.0, 5.0])  # 15 kWh insgesamt
    stations = []  # keine Ladestationen notwendig

    optimizer = create_networkx_optimizer()
    plan = optimizer.optimize(
        route=route,
        segments=route.segments,
        gradients=gradients,
        energy_results=energy,
        charging_stations=stations,
        waypoints=[],
        vehicle_profile=VehicleProfile(...),
        constraints=OptimizationConstraints(min_soc_pct=15.0, ziel_soc_pct=60.0),
        start_soc_pct=100.0,
        abfahrtszeit=datetime(2025, 6, 15, 8, 0, 0),
    )

    # Assertions
    assert plan.ladehalte == []
    assert plan.gesamtreisezeit_s > 0
    # ... weiteres
```

#### Test 2: Route mit Zwischenstopp, der auch Ladehalt ist (Given/When/Then)

```
Given: Route mit Zwischenstopp (Segment 2, 30-min-Pause), Supercharger am selben Segment
When: Zwischenstopp-Dauer > Zeit für Ladevorgang bis Ziel-SoC
Then: Eine ChargingStop mit station am Segment 2, Ankunftszeit ≤ Zwischenstopp-Zeitfenster
```

#### Test 3: Grenzfall — maximale Iteration, Konvergenz erreicht (Integrationstest)

```
Given: Iterative Wetterabfrage (Phase 6/Orchestrierung) mit 30-min-Schwellwert
When: Reise über mehrere Wetterpunkte, ETA-Abweichung < 30 Min nach 1. Iteration
Then: Optimization-Modul erhält konsistente Wetterdaten, Plan ist stabil (keine Änderung zwischen Iteration 1 und 2)
```

```python
@pytest.mark.integration
def test_konvergenz_kleiner_eta_abweichung():
    # Setup: große Route (600 km), Wetteränderung im Verlauf
    # → first iteration yields ETA differences < 30 min
    # → optimizer receives new weather, re-runs → plan unchanged
    pass
```

### Unit vs. Integrationstest

| Test | Art | Marker | Beschreibung |
| ------ | ----- | -------- | -------------- |
| `test_kein_ladehalt_noetig` | Unit | — | Einzelfall, alle Input-Daten synthetisch |
| `test_zwischenstopp_mit_ladehalt` | Unit | — | Kombination von Zwischenstopp + Ladehalt |
| `test_grenzfall_minimaler_soc` | Unit | — | Start-SoC = Min-SoC + kleines Delta → muss laden |
| `test_konvergenz_kleiner_eta_abweichung` | Integration | `@pytest.mark.integration` | Orchestrierungsschicht mit Wetter-Iter |

---

## 7. Aufgaben-Checkliste

### Phase 7a – Modelldefinition & Validation

- [ ] Task 1: `src/tripplanner/optimization/models.py` erstellen — alle Pydantic-Modelle (ChargingStop, OptimizationConstraints, ChargingPlan, OptimizerInterface) mit Pydantic v2 Syntax und Custom-Validatoren (`PydanticCustomError` für SoC-Range).
- [ ] Task 2: `src/tripplanner/optimization/validation.py` implementieren — Funktionen zur Validierung von Eingabedaten (`validate_route_has_no_gaps`, `validate_soc_range`, `validate_waypoints_ordered`).
- [ ] Task 3: `src/tripplanner/optimization/__init__.py` erstellen — re-exportiert öffentliche API.

### Phase 7b – NetworkX-Prototyp

- [ ] Task 4: `src/tripplanner/optimization/discretizer.py` erstellen — Hilfsfunktionen `_soc_to_bucket`, `_bucket_to_soc`, `_zeit_to_bucket`, `_bucket_to_zeit`.
- [ ] Task 5: `src/tripplanner/optimization/optimizer.py` implementieren — `NetworkXOptimizer.optimize()` mit Graph-Aufbau, A*-Suche, Ladekanten-Hinzufügen.
- [ ] Task 6: `src/tripplanner/optimization/optimizer.py` implementieren — `_heuristik()`-Methode (verbleibende Distanz / 110 km/h).

### Phase 7c – OR-Tools-Backend (Vorbereitung für spätere Ausbaustufe)

- [ ] Task 7: `src/tripplanner/optimization/optimizer.py` implementieren — `ORToolsOptimizer.optimize()` mit CP-SAT-Modell (Variablen, Constraints, Objective).
- [ ] Task 8: `src/tripplanner/optimization/optimizer.py` implementieren — Factory-Funktion `create_ortools_optimizer()` mit `use_cp_sat`-Parameter.

### Phase 7d – Tests & Fixtures

- [ ] Task 9: Fixtures generieren (`tests/fixtures/optimization/`): 6-Segment-Route, 3 Ladestationen, 1 Zwischenstopp, Energie- und Ladekurven-Daten.
- [ ] Task 10: `tests/optimization/test_optimizer.py` schreiben — 3 Unit-Tests (kein Ladehalt, Zwischenstopp=Ladehalt, Minimal-SoC-Grenzfall).
- [ ] Task 11: `tests/optimization/test_optimizer.py` schreiben — 1 Integrationstest (`@pytest.mark.integration`) für Konvergenz-Erwartung.

### Phase 7e – Integration & Validierung

- [ ] Task 12: `src/tripplanner/optimization/optimizer.py` integrieren — `tripplanner/trip_input/orchestration.py` erweitern, um `optimizer.optimize()` nach Energie-Berechnung aufzurufen.
- [ ] Task 13: End-to-End-Test via CLI/API — Reise von Berlin nach Hamburg (ca. 260 km) mit Zwischenstopp in Magdeburg (70 km), startet mit 100% SoC, Zielsoc 80% → prüfe, dass Plan 1–2 Ladehalte vorsieht.
- [ ] Task 14: Coverage-Check → `pytest --cov=tripplanner.optimization --cov-report=term-missing --cov-fail-under=85`.

---

## 8. Risiken & offene technische Fragen

### Risiken

- **Skalierbarkeit der NetworkX-Lösung (behoben)**: Der Zustandsgraph wurde ursprünglich pro Roh-Segment (Segment-Index als eigene Dimension) aufgebaut, wodurch der Zustandsraum bei sehr langen Routen (>1000 Segmente, z. B. ein Segment pro GraphHopper-Polyline-Punktpaar) mit der Anzahl an Ladehalt-Optionen kombinatorisch anwuchs (Reisen >800 km dauerten mehrere Minuten oder liefen in ein Zeit-Limit). Fahrtkanten überspringen jetzt in `_add_drive_edge`/`_generate_graph` alle Roh-Segmente zwischen zwei Entscheidungspunkten (Ladestation, Zwischenstopp, Fähr-Einstieg) in einem Sprung (Distanz/Energie per vorberechneter Präfixsumme, siehe `optimize()`), die A*-Heuristik nutzt dieselbe Präfixsumme statt einer O(n)-Neuberechnung pro Knoten. Reduziert die Zustandsknotenzahl von O(Roh-Segmente × SoC-Buckets × Zeit-Buckets) auf O(Entscheidungspunkte × SoC-Buckets × Zeit-Buckets) - ein 1450-km-Beispiel mit 12.000 Roh-Segmenten/16 Ladestationen läuft dadurch in <1 s statt >90 s. OR-Tools (Phase 7c) bleibt für sehr viele Ladestationen (>50) dennoch die langfristig vorgesehene Ausbaustufe.
- **Zeit-Diskretisierung**: 15-Min-Schritte können zu suboptimalen Lösungen führen (z. B. Ladebeginn bei 13:47 statt 13:45). Feinere Diskretisierung (5 Min) → bessere Lösungen, aber höherer Rechenaufwand. Default auf 15 Min festgelegt; spätere Anpassung über Konfigurationsparameter möglich.
- **Unvollständige Ladekurven**: Die `ChargingCurve`-Validierung im battery-Modul ist noch nicht implementiert → falsche Ladezeiten möglich. Fix: Ladekurve vor Optimierung validieren (steigend, keine negativen Ladeleistungen).

### Offene technische Fragen

- **Reale Geschwindigkeit vs. Idealgeschwindigkeit**: Heuristik nutzt aktuell 110 km/h (Autobahn) als feste Geschwindigkeit. Bessere Heuristik: gewichtete Durchschnittsgeschwindigkeit aus Tempolimits der verbleibenden Segmente. *(Spätere Verbesserung, nicht Pflicht für MVP)*
- **Wetter-Änderung während Fahrt**: Die aktuelle Lösung geht von konstantem Wetter je Wetterabfragepunkt aus; keine dynamische Anpassung bei plötzlichem Sturm unterwegs. *(Komplexitätserhöhung; aktuell nicht vorgesehen)*
- **Ladekurven-Temperaturabhängigkeit**: Die Ladeleistung hängt von Batterietemperatur ab, die wiederum von Außentemperatur abhängt. Momentan wird eine mittlere Ladekurve verwendet. *(Optional für Kalibrierung in Phase 6, nicht Teil MVP)*

### Später / nicht jetzt umsetzen (explizit als „nicht im Scope“ markiert)

- **Mehrere GraphHopper-Alternativrouten**: Aktuell wird nur eine Route berechnet; keine energieoptimale Auswahl mehrerer Routen. *(Spätere, nicht-invasive Ausbaustufe)*
- **Live-Neuplanung**: Keine dynamische Anpassung des Plans während der Fahrt. *(Wäre ein eigenes Modul mit Echtzeit-Feeds)*
- **Personalisierte Kalibrierung**: Feste Default-Parameter für `VehicleEnergyParameters`; keine Anpassung an eigene Fahrdaten. *(Spätere Ausbaustufe, nicht Teil MVP)*

---

**Ende des Implementierungsplans für das `optimization`-Modul.**
