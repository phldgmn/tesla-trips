# Implementierungsplan: `energy`-Modul (Phase 3, physikalisches Verbrauchsmodell)

## 1. Zweck & Scope

Das `energy`-Modul berechnet den Energieverbrauch je Segment der Route physikalisch fundiert unter Berücksichtigung von:

- **Rollwiderstand** (Reibung zwischen Reifen und Straße)
- **Luftwiderstand** (einschließlich Windkomponenten aus `wind.models.WindComponents`)
- **Höhenenergie/Steigung** (Arbeit gegen die Schwerkraft bei Steigungen/Gefällen)
- **Rekuperation** (regeneratives Bremsen mit physikalisch plausiblen Grenzen)
- **Nebenverbraucher** (temperaturabhängige HVAC-Leistung für Heizung/Klimaanlage)

Das Modul **übersetzt** die physikalischen Kräfte entlang jedes Route-Segments in energiemäßig messbare Größen (kWh). Die Eingabe ist ein `RouteSegment` (aus `routing.models`), `WeatherSample` (aus `weather.models`), `WindComponents` (aus `wind.models`) sowie ein `VehicleEnergyParameters`-Objekt mit Fahrzeugparametern.

**Nicht im Scope:**
- Konfiguration oder Simulation der Batterie (dieser Bereich gehört zum `battery`-Modul).
- Ladeplanung oder -optimierung (dieser Bereich gehört zum `optimization`-Modul).
- Wettervorhersage oder Windmodellierung (dieser Bereich gehört zum `weather`- und `wind`-Modul).
- Fahrtzeitberechnung (dieser Bereich fließt in `optimization`, nicht in das Verbrauchsmodul).
- Kalibrierung aus Fahrdaten (dieser Bereich ist ausdrücklich als *nicht* im Umfang enthalten — siehe `docs/06-offene-punkte-widersprueche.md`, Punkt 5).

## 2. Abhängigkeiten & Phasenzuordnung

**Phase:** 3 (physikalisches Verbrauchsmodell)

**Direkte Abhängigkeiten von anderen Modulen:**
- `tripplanner.routing.models.RouteSegment` (liest: geometrie, laenge_m, tempolimit_kmh, steigung_rohdaten, oberflaeche)
- `tripplanner.elevation.models.SegmentGradient` (liest: steigung_prozent, hoehendifferenz_m)
- `tripplanner.weather.models.WeatherSample` (liest: temperatur_c, windgeschwindigkeit_ms, windrichtung_deg)
- `tripplanner.wind.models.WindComponents` (liest: gegenwind_ms, seitenwind_ms)
- `tripplanner.construction.models.ConstructionZone` (liest: tempolimit_kmh für Baustellenüberschreibung)

**Fremde Typen aus dem Register (nur Lesen):**
- `tripplanner.trip_input.models.VehicleProfile` (wird als Quelle für `VehicleEnergyParameters` genutzt)
- `tripplanner.elevation.models.ElevationPoint` (indirekt über `SegmentGradient`)

## 3. Datenmodelle

### `VehicleEnergyParameters` (eigener Typ im energy-Modul)

Pydantic-Modell mit fest verdrahteten Default-Werten für Tesla Model 3 (ohne Kalibrierungs-Feature im aktuellen Scope, aber strukturell vorbereitet):

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional


class VehicleEnergyParameters(BaseModel):
    """Physikalische Fahrzeugparameter für Energieberechnung.

    Alle Default-Werte basieren auf öffentlich verifizierten Spezifikationen
    des Tesla Model 3 (2024/2025).
    """

    # Aerodynamik
    cw_wert: float = Field(
        default=0.23,
        ge=0.0,
        description="Drag coefficient (cW) für Tesla Model 3 (Standardfassung). "
        "Neuere Generation (Highland facelift) erreicht 0.219, "
        "aber 0.23 bleibt als Standard für breite Kompatibilität.",
    )
    stirnflaeche_m2: float = Field(
        default=2.22, ge=0.0, description="Frontalfläche in m² (Tesla Model 3)."
    )

    # Rollwiderstand
    rollwiderstandsbeiwert: float = Field(
        default=0.011,
        ge=0.0,
        le=0.02,
        description="Rollwiderstandsbeiwert c_r für Model 3 mit Standardreifen "
        "bei 2.9 bar (42 psi). Bereich 0.010–0.011 typisch.",
    )

    # Masse
    masse_kg: float = Field(
        default=1706.0,
        ge=1500.0,
        le=1900.0,
        description="Fahrzeuggestützte Masse in kg. Basis: "
        "Rear-Wheel Drive 3,759 lbs ≈ 1,706 kg. "
        "Long Range AWD ≈ 1,828 kg, Performance ≈ 1,845 kg.",
    )

    # Batterie & Antrieb
    batteriekapazitaet_kwh: float = Field(
        default=62.5,
        ge=50.0,
        le=85.0,
        description="Nutzbare Batteriekapazität in kWh. "
        "Standard Range (2025 LFP): 62.5 kWh (Gesamt ca. 65 kWh). "
        "Long Range/Performance: ~75–82 kWh nutzbar.",
    )
    wirkungsgrad_antrieb: float = Field(
        default=0.94,
        ge=0.85,
        le=0.99,
        description="Wirkungsgrad des Elektromotors (±2% Toleranz). Typische Werte: 92–96 %.",
    )
    wirkungsgrad_rekuperation: float = Field(
        default=0.75,
        ge=0.65,
        le=0.85,
        description="Gesamtwirkungsgrad für regenerative Bremsung "
        "(Kettenwirkungsgrad: Rad → Motor → Batterie ≈ 75 %).",
    )

    # Nebenverbraucher
    nebenverbraucher_baseline_kw: float = Field(
        default=0.34,
        ge=0.0,
        le=1.0,
        description="Baseline-Verbrauch in kW bei Parke- und Standby-Bedingungen "
        "(ohne Klimaanlage/Heizung).",
    )
    klimaanlage_max_kw: float = Field(
        default=5.5,
        ge=3.0,
        le=8.0,
        description="Maximale Leistungsaufnahme der Klimaanlage (A/C). "
        "Full blast ≈ 5–7 kW, typischer Betrieb ≈ 1–4 kW.",
    )
    heizung_max_kw: float = Field(
        default=6.0,
        ge=3.0,
        le=10.0,
        description="Maximale Heizleistung (PTC-Heizer oder Wärmepumpe). "
        "PTC-Heizung (ältere Modelle): bis 7 kW. "
        "Wärmepumpe (Highland): effizienter, aber 6 kW als obere Grenze sicher.",
    )
    komforttemperatur_min_c: float = Field(
        default=18.0,
        ge=10.0,
        le=22.0,
        description="Untere Komforttemperaturgrenze (°C). "
        "Unterhalb dieses Wertes steigt Heizungsleistung linear an.",
    )
    komforttemperatur_max_c: float = Field(
        default=24.0,
        ge=20.0,
        le=28.0,
        description="Obere Komforttemperaturgrenze (°C). "
        "Darüber steigt Klimaanlagenleistung linear an.",
    )

    # Reifentyp & Dachbox
    reifentyp: Literal["standard", "winter", "low_rolling_resistance", "performance"] = Field(
        default="standard", description="Reifentyp, der den Rollwiderstand moduliert."
    )
    dachbox: bool = Field(
        default=False, description="Vorhandensein einer Dachbox (erhöht cw_wert um ~0.03–0.05)."
    )

    @field_validator("cw_wert")
    @classmethod
    def adjust_cw_for_dachbox(cls, v: float, info) -> float:
        """Passive Anpassung des cw-Werts bei Dachbox (kein Validator, stattdessen in Berechnung)."""
        return v  # Wird in Berechnungsmethode berücksichtigt

    @field_validator("rollwiderstandsbeiwert")
    @classmethod
    def adjust_cr_for_reifentyp(cls, v: float, info) -> float:
        """Passive Anpassung des Rollwiderstands für verschiedene Reifentypen."""
        typ_factors = {
            "standard": 1.0,
            "winter": 1.25,
            "low_rolling_resistance": 0.9,
            "performance": 1.1,
        }
        return v * typ_factors.get(info.context.get("reifentyp", "standard"), 1.0)
```

### `SegmentEnergyResult` (eigener Typ im energy-Modul)

```python
class SegmentEnergyResult(BaseModel):
    """Ergebnis der Energieberechnung für ein Route-Segment."""

    segment_index: int
    energiebedarf_kwh: float  # Positiv: Verbrauch, Negativ: Rekuperation (überschüssige Energie)
    rekuperation_kwh: float  # Betrag der regenerativ gewonnenen Energie (immer ≥ 0)
    energiebedarf_brutto_kwh: float  # Summe aller Verbraucher (ohne Rekuperation)
    geschwindigkeit_m_s: float  # Mittlere Geschwindigkeit im Segment (m/s)
    fahrzeit_s: float  # Fahrzeit des Segments (s)
    streckenlaenge_m: float  # Länge des Segments (m)
```

## 4. Öffentliche Schnittstelle

### `energy.py` – Kern-API

```python
"""
Energieverbrauchs-Modul (Phase 3).

Klartext-Dokumentation im Google-Style (Pydantic/typing siehe models.py).
"""

from datetime import timedelta
from typing import Sequence, Optional

from tripplanner.routing.models import RouteSegment
from tripplanner.elevation.models import SegmentGradient
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents
from tripplanner.construction.models import ConstructionZone
from tripplanner.energy.models import VehicleEnergyParameters, SegmentEnergyResult


def berechne_segment_verbrauch(
    segment: RouteSegment,
    gradient: SegmentGradient,
    wetter: WeatherSample,
    wind: WindComponents,
    fahrzeug_params: VehicleEnergyParameters,
    baustellen: Optional[Sequence[ConstructionZone]] = None,
    tempolimit_override_kmh: Optional[float] = None,
) -> SegmentEnergyResult:
    """
    Berechnet den Energieverbrauch für ein einzelnes Segment.

    Args:
        segment: Routing-Segment (Geometrie, Länge, Tempolimit, Straßenbelag).
        gradient: Höhenprofil-Gradient für dieses Segment.
        wetter: Wetterdaten (Temperatur, Wind) zum erwarteten Durchfahrtszeitpunkt.
        wind: Projektion des Windes auf die Fahrtrichtung (Gegen-/Seitenwind).
        fahrzeug_params: Fahrzeugparameter (Masse, cW, Rollwiderstand, etc.).
        baustellen: optionale Liste von Baustellen (überschreibt Tempolimit).
        tempolimit_override_kmh: optionales Tempolimit-Override (für Baustellen-Logik).

    Returns:
        SegmentEnergyResult mit Energiebedarf, Rekuperation, Fahrzeit und Geschwindigkeit.

    Algorithmus:
        1. Bestimme Effective Speed (Tempolimit oder Überschreitung, max. 150 km/h).
        2. Berechne Kräfte (Rollwiderstand inkl. Straßenbelag-Faktor, Luftwiderstand, Steigung, Rekuperation).
        3. Konvertiere Kräfte → Leistung → Energie über Fahrzeit.
        4. Addiere Nebenverbraucher (temperaturabhängig).
        5. Subtrahiere Rekuperation (physikalisch begrenzt).
    """
    ...


def berechne_gesamtverbrauch(
    route_segments: Sequence[RouteSegment],
    gradients: Sequence[SegmentGradient],
    wetter_samples: Sequence[WeatherSample],
    wind_components: Sequence[WindComponents],
    fahrzeug_params: VehicleEnergyParameters,
    baustellen: Optional[Sequence[ConstructionZone]] = None,
) -> list[SegmentEnergyResult]:
    """
    Berechnet den Energieverbrauch für eine gesamte Route (Segment-für-Segment).

    Wrapper-Funktion für parallele oder sequenzielle Verarbeitung mehrerer Segmente.
    Wetter- und Winddaten müssen der Reihenfolge der Route Segmente entsprechen.
    """
    ...
```

## 5. Externe Integration / Algorithmus-Details

### 5.1 Physikalische Formelherleitung

Alle Formeln beziehen sich auf ein Segment der Länge `s` (m), Fahrzeit `t` (s), mittlere Geschwindigkeit `v` (m/s) und Steigungswinkel `α` (Rad).

#### 5.1.1 Rollwiderstand (F_roll)

```
F_roll = c_r * f_oberflaeche(segment.oberflaeche) * m * g * cos(α)
```

- `c_r`: Rollwiderstandsbeiwert (vom Fahrzeug übergeben, Default: 0.011 für Model 3)
- `m`: Fahrzeuggewicht (Default: 1706 kg)
- `g`: Erdbeschleunigung ≈ 9.81 m/s²
- `α`: Steigungswinkel (berechnet aus `gradient.steigung_prozent`: `α = arctan(steigung_prozent / 100)`)

**Hinweis:** Die cos(α)-Komponente ist bei Steigungen < 15° ≈ 2–3 % sehr nahe an 1.0, kann aber für steile Abschnitte exakt berechnet werden.

**Straßenbelag-Faktor (`f_oberflaeche`):** `docs/01-projektspezifikation.md` nennt „Straßenbelag" explizit als Einflussgröße auf den Verbrauch; GraphHopper liefert dazu das Path-Detail `surface` (siehe `docs/plans/01-routing.md`, Abschnitt 5, gemappt auf `RouteSegment.oberflaeche`). Modelliert als Lookup-Tabelle in `energy.py`, multiplikativ auf `c_r` angewendet (grobe, aus Fahrzeugtechnik-Literatur abgeleitete Näherung — kein Kalibrierungs-Feature, siehe Punkt 5 der offenen Fragen):

```python
OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR: dict[str, float] = {
    "asphalt": 1.0,
    "paved": 1.0,
    "concrete": 1.0,
    "compacted": 1.15,
    "gravel": 1.5,
    "unpaved": 1.5,
    "dirt": 1.8,
    "ground": 1.8,
    "grass": 2.2,
    "sand": 2.5,
}
DEFAULT_OBERFLAECHEN_FAKTOR = (
    1.0  # Fallback, falls `oberflaeche` None oder unbekannt (z. B. Autobahn ohne surface-Tag)
)


def f_oberflaeche(oberflaeche: str | None) -> float:
    """Rollwiderstands-Multiplikator für den gegebenen Straßenbelag (grobe Näherung)."""
    if oberflaeche is None:
        return DEFAULT_OBERFLAECHEN_FAKTOR
    return OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR.get(oberflaeche.lower(), DEFAULT_OBERFLAECHEN_FAKTOR)
```

#### 5.1.2 Luftwiderstand (F_luft)

```
F_luft = 0.5 * ρ_luft * cW_eff * A * v_relativ²
```

- `ρ_luft`: Luftdichte ≈ 1.225 kg/m³ (ISA-Standard bei 15°C, 1013 hPa)
- `cW_eff`: effektiver cW-Wert, inkl. Dachbox-Korrektur
  - Ohne Dachbox: `cW_eff = fahrzeug_params.cw_wert`
  - Mit Dachbox: `cW_eff = fahrzeug_params.cw_wert + 0.04` (Konservativ: +0.03 bis +0.05)
- `A`: Stirnfläche = `fahrzeug_params.stirnflaeche_m2` (2.22 m²)
- `v_relativ`: relative Geschwindigkeit zum Luftmassenstrom
  - `v_relativ = v - gegenwind_ms` (Gegenwind: positiv → höherer Widerstand)
  - `gegenwind_ms` ist vorzeichenbehaftet aus `wind.models.WindComponents`

**Hinweis:** Bei starkem Rückenwind (`gegenwind_ms < -v`) würde `v_relativ` negativ → `v_relativ²` bleibt positiv, aber die physikalische Interpretation (Wind treibt das Fahrzeug) ist nicht im Modell enthalten. Für diesen Fall wird `v_relativ = max(v_relativ, 0.5 * v)` gesetzt (minimal 50 % der Geschwindigkeit ohne Wind).

#### 5.1.3 Steigung (Höhenenergie/F_steigung)

```
F_steigung = m * g * sin(α)
```

- `α = arctan(steigung_prozent / 100)`
- `sin(α)` kann direkt über `steigung_prozent / sqrt(10000 + steigung_prozent²)` berechnet werden (ohne trigonometrische Funktion)

**Vorzeichen:** Bei Gefälle (`steigung_prozent < 0`) ist `F_steigung < 0` (unterstützende Kraft).

#### 5.1.4 Rekuperation (F_rekuperation)

Rekuperation wird **nicht** als Kraft, sondern als Energiebilanz modelliert:

```
E_rekuperation = min(
    0.5 * m * (v_anfang² - v_ende²) * η_rekup,
    P_rekup_max * t
)
```

- `v_anfang`, `v_ende`: Geschwindigkeit zu Segmentanfang/ende (m/s)
- `η_rekup`: Wirkungsgrad regenerative Bremsung ≈ 0.75 (Default)
- `P_rekup_max`: maximale Rekuperationsleistung (Tesla Model 3 AWD: 85 kW, Default: 80 kW für Sicherheitspuffer)

**Physikalische Beschränkungen:**
- Rekuperation funktioniert **nur bei Verzögerung** (`v_ende < v_anfang`).
- Rekuperation **darf nicht negative Geschwindigkeit erzeugen** (Segmentende-Geschwindigkeit ≥ 0).
- Rekuperation **darf nicht über die maximale Ladeleistung der Batterie hinausgehen** (`P_rekup_max`).

#### 5.1.5 Nebenverbraucher (HVAC / Heizung/Klima)

```
P_neben = P_baseline + P_HVAC(T_ausser)
```

**Baseline:**
- `P_baseline = fahrzeug_params.nebenverbraucher_baseline_kw` (0.34 kW)

**HVAC-Korrektur (linear, temparaturabhängig):**

```python
if T_ausser_c < fahrzeug_params.komforttemperatur_min_c:
    # Heizung nötig
    delta_T = fahrzeug_params.komforttemperatur_min_c - T_ausser_c
    P_heizung = fahrzeug_params.heizung_max_kw * (delta_T / 10.0)  # linear bis 10°C Differenz
    P_neben += min(P_heizung, fahrzeug_params.heizung_max_kw)
elif T_ausser_c > fahrzeug_params.komforttemperatur_max_c:
    # Klima nötig
    delta_T = T_ausser_c - fahrzeug_params.komforttemperatur_max_c
    P_klima = fahrzeug_params.klimaanlage_max_kw * (delta_T / 10.0)
    P_neben += min(P_klima, fahrzeug_params.klimaanlage_max_kw)
else:
    # Komfortbereich, nur Baseline
    pass
```

**Beispielrechnung:**
- `T_ausser = -5°C`, `komforttemperatur_min = 18°C` → `delta_T = 23°C` → Heizung auf 100 % (max. 6 kW).
- `T_ausser = 32°C`, `komforttemperatur_max = 24°C` → `delta_T = 8°C` → Klima bei ~80 % (ca. 4.4 kW).

#### 5.1.6 Kraft → Leistung → Energie

1. **Resultierende Kraft (F_res)** entgegen der Fahrtrichtung:
   ```
   F_res = F_roll + F_luft + max(0, F_steigung)
   ```
   (Steigung positiv nur bei Steigungen, bei Gefällen `F_steigung < 0` wird ignoriert, da Motor dann nicht mehr arbeitet)

2. **Leistung (P)** über die Segmentdauer:
   ```
   E_brutto = (F_res * s + P_neben * t) / η_antrieb
   ```
   - `s`: Strecke (m)
   - `t`: Fahrzeit (s)
   - `η_antrieb`: Wirkungsgrad Antrieb ≈ 0.94 (Default)

3. **Rekuperation abziehen:**
   ```
   E_gesamt = E_brutto - E_rekuperation
   ```
   - Wenn `E_gesamt < 0`, wird `E_gesamt = 0` gesetzt (Energie aus dem Netz ist nicht negativ).

### 5.2 Algorithmus-Schritte in Pseudocode

```python
def berechne_segment_verbrauch(segment, gradient, wetter, wind, params, baustellen=None):
    # 1. Bestimme Geschwindigkeit und Fahrzeit
    tempolimit = segment.tempolimit_kmh
    if baustellen:
        tempolimit = min(tempolimit, *[bz.tempolimit_kmh for bz in baustellen])

    v_mittel_kmh = min(tempolimit, 150.0)  # max. 150 km/h physikalisch sinnvoll
    v_mittel_ms = v_mittel_kmh / 3.6
    s_m = segment.laenge_m
    t_s = s_m / v_mittel_ms

    # 2. Winkel berechnen
    alpha_rad = arctan(gradient.steigung_prozent / 100.0)

    # 3. Kräfte
    F_roll = params.rollwiderstandsbeiwert * params.masse_kg * 9.81 * cos(alpha_rad)
    v_relativ = max(
        v_mittel_ms - wind.gegenwind_ms, 0.5 * v_mittel_ms
    )  # Mindestvorgabe bei Rückenwind
    F_luft = 0.5 * 1.225 * params.cw_wert * params.stirnflaeche_m2 * v_relativ**2
    F_steigung = params.masse_kg * 9.81 * sin(alpha_rad)

    # 4. Energie für Bewegung
    E_bewegung_brutto = (F_roll + F_luft + max(0, F_steigung)) * s_m / params.wirkungsgrad_antrieb

    # 5. HVAC-Verbrauch
    T_c = wetter.temperatur_c
    P_neben = params.nebenverbraucher_baseline_kw
    if T_c < params.komforttemperatur_min_c:
        delta_T = params.komforttemperatur_min_c - T_c
        P_heiz = params.heizung_max_kw * min(delta_T / 10.0, 1.0)
        P_neben += P_heiz
    elif T_c > params.komforttemperatur_max_c:
        delta_T = T_c - params.komforttemperatur_max_c
        P_klima = params.klimaanlage_max_kw * min(delta_T / 10.0, 1.0)
        P_neben += P_klima
    E_neben = P_neben * t_s

    # 6. Rekuperation (nur bei Verzögerung)
    # Vereinfachung: feste Anfangsgeschwindigkeit v_anfang, Ende v_ende = 0 bei Steigung / v_anfang bei Gefälle
    v_anfang_ms = v_mittel_ms
    v_ende_ms = max(v_mittel_ms - 0.1 * gradient.steigung_prozent, 0)  # vereinfachte Verzögerung
    if v_ende_ms < v_anfang_ms:
        E_kin = 0.5 * params.masse_kg * (v_anfang_ms**2 - v_ende_ms**2)
        E_rekup = min(E_kin * params.wirkungsgrad_rekuperation, 80_000 * t_s)  # 80 kW max.
    else:
        E_rekup = 0.0

    # 7. Gesamtergebnis
    E_brutto = E_bewegung_brutto + E_neben
    E_gesamt = max(0.0, E_brutto - E_rekup)

    return SegmentEnergyResult(
        segment_index=segment.segment_index,
        energiebedarf_kwh=E_gesamt / 3_600_000,  # J → kWh
        rekuperation_kwh=E_rekup / 3_600_000,
        energiebedarf_brutto_kwh=E_brutto / 3_600_000,
        geschwindigkeit_m_s=v_mittel_ms,
        fahrzeit_s=t_s,
        streckenlaenge_m=s_m,
    )
```

### 5.3 Externe Quellen / Bibliotheken

- **rasterio**: Wird **nicht** im `energy`-Modul direkt genutzt — die Höhendaten kommen bereits als `SegmentGradient` aus dem `elevation`-Modul.
- **httpx**: Wird **nicht** im `energy`-Modul genutzt — externe API-Zugriffe erfolgen in den Provider-Modulen `routing`, `weather`, `construction`, `charging_infrastructure`.
- **Pydantic v2**: Verwendet für `VehicleEnergyParameters`, `SegmentEnergyResult`.

### 5.4 Weitere Recherche-Entscheidungen

| Parameter | Wert | Quelle | Begründung |
|-----------|------|--------|------------|
| Luftdichte `ρ` | 1.225 kg/m³ | ISA-Standard (15°C, 1013 hPa) | Industry standard; bei extremen Temperaturen < ±20°C können Korrekturen (~±3 %) erfolgen, aber dies ist im ersten Prototype nicht nötig. |
| `cW` Tesla Model 3 | 0.23 | [Chegg, 2022] | Konservativ für alle Model-3-Versionen. Neuere Generation 0.219, aber 0.23 erhältliche Datenbasis. |
| Stirnfläche | 2.22 m² | [Chegg, 2022] | Standardangabe für Model 3. |
| Rollwiderstand `c_r` | 0.011 | [Engineering D&B, ORNL studies] | Typisch für Model 3 mit Standardreifen bei 2.9 bar. |
| Masse (RWD) | 1706 kg | [Tesla Owners Online, 2025] | Basierend auf 3,759 lbs (1,706 kg) für Base RWD. Long Range ~1,828 kg. Defaultwert Mittelwert für typische Konfiguration. |
| Batteriekapazität | 62.5 kWh | [EV Database, TMC 2025] | Aktuelle 2025-LFP-Version; Long Range/Performance ~75–82 kWh nutzbar. Defaultwert für Standard Range. |
| Wirkungsgrad Motor | 0.94 | [Tesla Owners Online forum] | Typischer Bereich 0.92–0.96; 0.94 als konservativer Mittelwert. |
| Wirkungsgrad Rekuperation | 0.75 | [arXiv:2106.14686, TMC] | Kombinierter Kettenwirkungsgrad (Rad→Motor→Batterie). |
| Rekuperationsleistung | 80 kW | [TMC, 2023] | AWD Model 3: 85 kW max., aber Sicherheitspuffer 80 kW für Default. |
| Baseline-Verbrauch | 0.34 kW | [Cleantechnica, 2020] | Parke- und Standby-Verbrauch (ohne HVAC). |
| Klimaanlage max. | 5.5 kW | [EV Speedy, 2024] | Full blast ≈ 5–7 kW; 5.5 kW als konservativer Mittelwert. |
| Heizung max. (PTC) | 6.0 kW | [Enhauto, 2025] | PTC-Heizung bis 7 kW, Wärmepumpe effizienter; 6.0 kW als sichere Obergrenze. |

## 6. Test-Strategie

### 6.1 Fixtures

**`tests/fixtures/energy/`**
- `default_model3_params.json`: `VehicleEnergyParameters` als JSON (für Testdaten-Serialisierung)
- `model3_segments.json`: 3 typische Segmente (eben, Steigung +3 %, Gefälle -4 %)
- `wetter_sample_20c_windstill.json`: 20°C, Windgeschwindigkeit 0 m/s, Windrichtung 0°
- `wetter_sample_5c_15kmh_nord.json`: 5°C, Wind 15 km/h aus Norden (gegen Fahrtrichtung)
- `wetter_sample_neg10c_heizung.json`: -10°C, Klima auf Heizung (max. Leistung)
- `segment_gradients.csv`: segment_index, steigung_prozent, hoehendifferenz_m
- `wind_components.json`: wind_gegenwind_ms, wind_seitenwind_ms

**`tests/conftest.py`**
- `default_model3_params()`: `VehicleEnergyParameters()` mit Defaults.
- `segment_eben()`: `RouteSegment(segment_index=0, laenge_m=1000, tempolimit_kmh=120, geocode=[...], steigung_rohdaten=[...])`
- `segment_steigung_3pct()`: `RouteSegment(segment_index=1, laenge_m=800, tempolimit_kmh=100, ...)` mit `steigung_rohdaten=[0, 1.5, 3.0, 2.5, 0]`
- `segment_gefaelle_4pct()`: `RouteSegment(segment_index=2, laenge_m=1200, tempolimit_kmh=110, ...)` mit `steigung_rohdaten=[0, -2.0, -4.0, -3.0, 0]`
- `wind_components_windstill()`: `WindComponents(gegenwind_ms=0.0, seitenwind_ms=0.0)`
- `wind_components_gegenwind()`: `WindComponents(gegenwind_ms=5.0, seitenwind_ms=1.0)`
- `wetter_sample_ref()`: Referenzwetter (20°C, windstill) als `WeatherSample`

### 6.2 Konkrete Beispiel-Testfälle

#### Testfall 1: Ebene Strecke, windstill, 100 km/h (Referenzfall)

**Given:**
- `segment_eben()`: 1000 m, Tempolimit 120 km/h, Steigung 0 %
- `wetter_sample_ref()`: 20°C, Wind 0 m/s
- `wind_components_windstill()`: 0 m/s Gegenwind
- `default_model3_params()`: Tesla Model 3 Defaults

**When:**
- `berechne_segment_verbrauch()` aufgerufen mit oben genannten Daten.

**Then:**
- `ergebnis.energiebedarf_kwh ≈ 1.5 kWh` (±0.1 kWh Toleranz)
- `ergebnis.rekuperation_kwh ≈ 0.0 kWh`
- `ergebnis.fahrzeit_s ≈ 30.0 s`
- `ergebnis.geschwindigkeit_m_s ≈ 27.78 m/s (100 km/h)`

**Quellenvergleich:** Typische Verbrauchsdaten für Model 3 Long Range auf ebener Strecke bei 100 km/h liegen bei ~13–15 kWh/100km (EPA/WLTP). Dieser Testfall simuliert 1 km bei 100 km/h → ~1.4–1.6 kWh.

#### Testfall 2: Steigung +3 %, 80 km/h, Heizung (Maximalverbrauch)

**Given:**
- `segment_steigung_3pct()`: 800 m, Tempolimit 100 km/h, Steigung +3 %
- `wetter_sample_neg10c_heizung()`: -10°C
- `wind_components_windstill()`: 0 m/s
- `default_model3_params()`

**When:**
- `berechne_segment_verbrauch()` aufgerufen.

**Then:**
- `ergebnis.energiebedarf_kwh ≥ 2.5 kWh` (maximaler Verbrauch bei Steigung + Klima)
- `ergebnis.rekuperation_kwh = 0.0 kWh` (Steigung → keine Verzögerung)
- `ergebnis.fahrzeit_s ≈ 36.0 s (80 km/h für 800 m)`

**Berechnungshinweis:** Bei +3 % Steigung (α ≈ 1.72°), `F_steigung ≈ m * g * sin(α) ≈ 1706 * 9.81 * 0.0299 ≈ 501 N`. Über 800 m → 401 kJ ≈ 0.11 kWh (ohne Wirkungsgradverlust). Combined mit Luft- und Rollwiderstand und Heizung (6 kW * 0.01 h ≈ 0.06 kWh) ergibt sich insgesamt ~2.5 kWh.

#### Testfall 3: Gefälle -4 %, 110 km/h, Rekuperation aktiv (Minimalverbrauch / Energiegewinn)

**Given:**
- `segment_gefaelle_4pct()`: 1200 m, Tempolimit 110 km/h, Steigung -4 %
- `wetter_sample_ref()`: 20°C, Wind 0 m/s
- `wind_components_windstill()`: 0 m/s
- `default_model3_params()`

**When:**
- `berechne_segment_verbrauch()` aufgerufen.

**Then:**
- `ergebnis.energiebedarf_kwh ≤ 0.5 kWh` (sehr gering, möglicherweise接近 0)
- `ergebnis.rekuperation_kwh ≥ 0.1 kWh` (Rekuperation bei Verzögerung)
- `ergebnis.fahrzeit_s ≈ 39.3 s (110 km/h für 1200 m)`

**Hinweis:** Bei Gefälle wird Rekuperation aktiviert (`v_ende < v_anfang`), was zu negativem `energiebedarf_kwh` (Energiegewinn) führen kann. Im Modell wird dies aber als `0.0` begrenzt, da negative Energie aus dem Netz nicht existiert — Rekuperation wird als `rekuperation_kwh` separat ausgewiesen.

### 6.3 Unit- vs. Integrationstest

- **Unit-Tests (pytest)**: Alle o. g. Testfälle sind Unit-Tests (`tests/test_energy.py`). Keine Live-Netzwerkzugriffe, keine Datei-E/A.
- **Integrationstests (pytest.mark.integration)**: Keine Integrationstests für das `energy`-Modul, da es eine rein deterministische Berechnung ist (keine Netzwerkabhängigkeit). Integrationstests finden im `routing`, `weather`, `elevation`-Modul statt, wo externe API- oder Dateizugriffe stattfinden.

## 7. Aufgaben-Checkliste

- [ ] **Task 1: `src/tripplanner/energy/models.py` erstellen**  
  Erstelle `VehicleEnergyParameters` und `SegmentEnergyResult` als Pydantic-Modelle mit den im Plan definierten Feldern und Default-Werten (Tesla Model 3).  
  *Akzeptanzkriterium:* Beide Modelle können mit `VehicleEnergyParameters()` und `SegmentEnergyResult(segment_index=0, energiebedarf_kwh=1.5, rekuperation_kwh=0.0, energiebedarf_brutto_kwh=1.5, geschwindigkeit_m_s=27.78, fahrzeit_s=30.0, streckenlaenge_m=1000.0)` instantiiert werden; mypy --strict akzeptiert keine Typfehler.

- [ ] **Task 2: `src/tripplanner/energy/__init__.py` erstellen**  
  Erstelle ein minimal `__init__.py`, das `berechne_segment_verbrauch` und `berechne_gesamtverbrauch` exportiert.  
  *Akzeptanzkriterium:* `from tripplanner.energy import berechne_segment_verbrauch, berechne_gesamtverbrauch` funktioniert ohne Fehler.

- [ ] **Task 3: `src/tripplanner/energy/energy.py` erstellen (Kernfunktion)**  
  Implementiere `berechne_segment_verbrauch()` gemäß dem im Plan definierten Algorithmus (Formeln, Schritte 1–7).  
  *Akzeptanzkriterium:* Alle 3 Beispiel-Testfälle (Task 6) liefern die erwarteten Werte innerhalb der Toleranz (±0.1 kWh für Referenzfall).

- [ ] **Task 4: `src/tripplanner/energy/providers.py` erstellen (Fake)**  
  Erstelle `EnergyProvider`-Protocol (wenn später Erweiterungen möglich sein sollen) und `FakeEnergyProvider` für Tests (simuliert `berechne_segment_verbrauch` und gibt `SegmentEnergyResult` zurück).  
  *Akzeptanzkriterium:* `FakeEnergyProvider().berechne_segment_verbrauch(...)` gibt das gleiche Ergebnis wie die Referenz-Implementierung zurück.

- [ ] **Task 5: `tests/energy/conftest.py` erstellen**  
  Erstelle Fixtures für `default_model3_params`, `segment_eben`, `segment_steigung_3pct`, `segment_gefaelle_4pct`, `wind_components_windstill`, `wind_components_gegenwind`, `wetter_sample_ref`.  
  *Akzeptanzkriterium:* `pytest --fixtures` zeigt alle Fixtures an; jede Fixture kann in einem Test verwendet werden.

- [ ] **Task 6: `tests/energy/test_energy.py` erstellen**  
  Schreibe 3 Unit-Tests für die oben definierten Testfälle (eben, Steigung +3 %, Gefälle -4 %). Jeder Test vergleicht Ergebnis mit erwartetem Wert (`pytest.approx(1.5, rel=0.05)`).  
  *Akzeptanzkriterium:* `pytest tests/energy/test_energy.py -v` gibt 3 bestandene Tests aus.

- [ ] **Task 7: `tests/energy/test_providers.py` erstellen (optional)**  
  Schreibe Test für `FakeEnergyProvider`, der `berechne_segment_verbrauch()` mit Referenzdaten aufruft und Ergebnis mit direktem Aufruf der Kernfunktion vergleicht.  
  *Akzeptanzkriterium:* Test bestanden; `FakeEnergyProvider` gibt identische Werte wie die direkte Funktion.

- [ ] **Task 8: `docs/plans/06-energy.md` erstellen**  
  Diese Plan-Datei (die Sie gerade erstellen) ist die Dokumentation des Moduls.  
  *Akzeptanzkriterium:* Die Datei existiert unter `docs/plans/06-energy.md` und enthält alle 8 Abschnitte (Zweck & Scope, Abhängigkeiten & Phasenzuordnung, Datenmodelle, Öffentliche Schnittstelle, Algorithmus-Details, Test-Strategie, Aufgaben-Checkliste, Risiken & offene Fragen).

- [ ] **Task 9: Dokumentation `docs/plans/06-energy.md` finalisieren**  
  Füge alle Details aus dem Plan hinzu, die noch fehlen (z. B. Link zu Quellen für Default-Parameter).  
  *Akzeptanzkriterium:* Leser kann alle Default-Parameter nachvollziehen (Wert + Quelle) und die Implementierung ohne Rückfragen starten.

- [ ] **Task 10: `ruff`-Formatierung und Linting**  
  Führe `ruff check src/tripplanner/energy tests/energy` aus und korrigiere alle gefundenen Regeln (E, F, I, UP, B, SIM, PL, RUF, plus D-Regeln mit `pydocstyle convention = "google"`).  
  *Akzeptanzkriterium:* `ruff check` meldet keine Fehler mehr.

- [ ] **Task 11: `mypy --strict`-Typannotationen prüfen**  
  Führe `mypy --strict src/tripplanner/energy tests/energy` aus.  
  *Akzeptanzkriterium:* `mypy` meldet keine Typprobleme (vollständige Typannotationen auf allen öffentlichen Schnittstellen).

- [ ] **Task 12: `pytest --cov=src/tripplanner/energy --cov-report=term-missing --cov-fail-under=85`**  
  Führe Testausführung mit Coverage-Gate 85 % aus.  
  *Akzeptanzkriterium:* Coverage ≥ 85 %, `pytest` meldet keine Fehler.

- [ ] **Task 13: Code-Review durchführen**  
  Lass die Implementierung von einem Kollegen reviewen (oder nutze `task code-documentation-code-reviewer`).  
  *Akzeptanzkriterium:* review-Feedback ist eingearbeitet; keine offenen review-comments mehr.

- [ ] **Task 14: Beispiel-Szenario mit 5-Segment-Route simulieren**  
  Erstelle ein kleines Python-Skript (`scripts/example_energy_calculation.py`), das eine 5-Segment-Route mit verschiedenen Steigungen/Wetterbedingungen berechnet und Ergebnis ausgibt.  
  *Akzeptanzkriterium:* Skript läuft ohne Fehler, gibt für jedes Segment `SegmentEnergyResult` aus (konsistente Werte).

- [ ] **Task 15: `f_oberflaeche()` und Rollwiderstands-Integration implementieren**
  Implementiere `OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR`, `f_oberflaeche()` und die Anwendung in der `F_roll`-Berechnung gemäß Abschnitt 5.1.1. Ergänze `tests/energy/test_energy.py::test_oberflaechenfaktor_gravel_erhoeht_verbrauch` (gleiches Segment mit `oberflaeche="asphalt"` vs. `oberflaeche="gravel"` → Gravel liefert strikt höheren `energiebedarf_kwh`) und `test_oberflaechenfaktor_none_fallback` (`oberflaeche=None` → Faktor 1.0).
  *Akzeptanzkriterium:* Beide neuen Tests grün; `f_oberflaeche` ist unit-testbar ohne weitere Abhängigkeiten (reine Funktion).

## 8. Risiken & offene technische Fragen

1. **Windprojektion bei komplexen Windfeldern**: Der aktuelle Algorithmus nutzt einen konstanten Gegenwind für das gesamte Segment. Bei kurzen, stark wechselnden Windböen (z. B. Täler, Windkanäle) könnte dies ungenau sein. *Lösung:* Für Phase 1 akzeptabel; spätere Ausbaustufe: Wind-Interpolation entlang der Segment-Geometrie.

2. **Temperaturabhängige Batterieeffizienz**: Die Rekuperationseffizienz und maximale Ladeleistung hängen von der Batterietemperatur ab. Dies wird im `energy`-Modul nicht berücksichtigt (nur im `battery`-Modul). *Lösung:* Ein `battery`-Modul könnte later eine `ChargingEfficiencyCurve` an `energy` liefern, die dann in die Rekuperationsberechnung eingeht.

3. **Rekuperation bei starkem Gefälle**: Wenn das Fahrzeug im Gefälle mit Rekuperation „abgebremst“ wird, kann die Geschwindigkeit unter das Tempolimit fallen. Der aktuelle Algorithmus nimmt eine lineare Verzögerung an (`v_ende = v_anfang - 0.1 * steigung_prozent`). *Lösung:* Für Phase 1 ausreichend präzise; spätere Ausbaustufe: Integration in die Optimierungsschleife mit variabler Geschwindigkeit.

4. **HVAC-Modell ist sehr vereinfacht**: Es wird nur linear zwischen Komfortgrenzen interpoliert, keine Berücksichtigung von Wärmepumpe vs. PTC-Heizung, keine Inertial-Effekte (Heizung läuft weiter, auch wenn Zieltemperatur erreicht). *Lösung:* Dies ist ein bewusster Kompromiss für Phase 1. Kalibrierung aus realen Fahrdaten (spätere Phase) wird dieses Modell automatisch verbessern.

5. **Luftdichte `ρ = 1.225 kg/m³` ist konstant**: Es wird keine Temperatur-/Druckkorrektur für die Luftdichte vorgenommen. *Lösung:* Bei extremen Temperaturen (< -20°C oder > +40°C) könnte dies zu ~±5 % Abweichung führen. Für Europa und mittlere Klimazonen ist dies vernachlässigbar.

6. **Reifentyp und Dachbox sind binär (ja/nein)**: Es gibt keine stufenlose Modellierung (z. B. Winterreifen „schlecht“ vs. „gut“). *Lösung:* Reifentyp-Enumeration reicht für Phase 1. Dachbox-Effekt (~+4 % cw) ist dominant genug, um als binärer Schalter behandelt zu werden.

7. **Default-Batteriekapazität 62.5 kWh ist für Standard Range (2025)**: Wenn ein Nutzer ein Long Range Modell fährt, muss er manuell `batteriekapazitaet_kwh=75` oder `82` setzen. *Lösung:* Kein Problem; das Fahrzeugprofil (`VehicleProfile`) im `trip_input`-Modul kann dies über die Modellbezeichnung ableiten (spätere Erweiterung im `trip_input`-Modul).

8. **Keine Berücksichtigung von Fahrbahnzustand (nass, schnee, eis)**: Der Rollwiderstandsbeiwert `c_r` ist für trockene Fahrbahn angegeben. *Lösung:* Für Europa und mittlere Klimazonen wird das Modell primär auf trockenen Straßenkalibriert. Schnee/Eis ist ein extremer Sonderfall, der in einer späteren Ausbaustufe über ein `RoadConditionProvider` abgedeckt werden kann.