# Implementation Plan: `energy` Module (Phase 3, Physics-Based Consumption Model)

## 1. Purpose & Scope

The `energy` module calculates energy consumption per segment of the route in a physics-based manner, taking into account:

- **Rolling resistance** (friction between tires and road)
- **Aerodynamic drag** (including wind components from `wind.models.WindComponents`)
- **Altitude energy/gradient** (work against gravity on inclines/descents)
- **Regenerative braking** (with physics-plausible limits)
- **Auxiliary loads** (temperature-dependent HVAC power for heating/air conditioning)

The module **translates** the physical forces along each route segment into energy-measurable quantities (kWh). The input is a `RouteSegment` (from `routing.models`), `WeatherSample` (from `weather.models`), `WindComponents` (from `wind.models`), and a `VehicleEnergyParameters` object with vehicle parameters.

**Out of scope:**

- Battery configuration or simulation (this belongs to the `battery` module).
- Charging planning or optimization (this belongs to the `optimization` module).
- Weather forecasting or wind modeling (this belongs to the `weather` and `wind` modules).
- Travel time calculation (this flows into `optimization`, not the consumption module).
- Calibration from driving data (this area is expressly *out of scope* — see `docs/06-open-points-contradictions.md`, item 5).

## 2. Dependencies & Phase Assignment

**Phase:** 3 (physics-based consumption model)

**Direct dependencies on other modules:**

- `tripplanner.routing.models.RouteSegment` (reads: geometrie, laenge_m, tempolimit_kmh, steigung_rohdaten, oberflaeche)
- `tripplanner.elevation.models.SegmentGradient` (reads: steigung_prozent, hoehendifferenz_m)
- `tripplanner.weather.models.WeatherSample` (reads: temperatur_c, windgeschwindigkeit_ms, windrichtung_deg)
- `tripplanner.wind.models.WindComponents` (reads: gegenwind_ms, seitenwind_ms)
- `tripplanner.construction.models.ConstructionZone` (reads: tempolimit_kmh for construction zone override)

**External types from the registry (read-only):**

- `tripplanner.trip_input.models.VehicleProfile` (used as source for `VehicleEnergyParameters`)
- `tripplanner.elevation.models.ElevationPoint` (indirectly via `SegmentGradient`)

## 3. Data Models

### `VehicleEnergyParameters` (own type in the energy module)

Pydantic model with hard-coded default values for Tesla Model 3 (no calibration feature in current scope, but structurally prepared):

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

class VehicleEnergyParameters(BaseModel):
    """Physical vehicle parameters for energy calculation.

    All default values are based on publicly verified specifications
    of the Tesla Model 3 (2024/2025).
    """

    # Aerodynamics
    cw_wert: float = Field(
        default=0.23,
        ge=0.0,
        description="Drag coefficient (cW) for Tesla Model 3 (standard version). "
        "Newer generation (Highland facelift) achieves 0.219, "
        "but 0.23 remains as standard for broad compatibility.",
    )
    stirnflaeche_m2: float = Field(
        default=2.22, ge=0.0, description="Frontal area in m² (Tesla Model 3)."
    )

    # Rolling resistance
    rollwiderstandsbeiwert: float = Field(
        default=0.011,
        ge=0.0,
        le=0.02,
        description="Rolling resistance coefficient c_r for Model 3 with standard tires "
        "at 2.9 bar (42 psi). Range 0.010–0.011 typical.",
    )

    # Mass
    masse_kg: float = Field(
        default=1706.0,
        ge=1500.0,
        le=1900.0,
        description="Vehicle mass in kg. Base: "
        "Rear-Wheel Drive 3,759 lbs ≈ 1,706 kg. "
        "Long Range AWD ≈ 1,828 kg, Performance ≈ 1,845 kg.",
    )

    # Battery & drivetrain
    batteriekapazitaet_kwh: float = Field(
        default=62.5,
        ge=50.0,
        le=85.0,
        description="Usable battery capacity in kWh. "
        "Standard Range (2025 LFP): 62.5 kWh (total approx. 65 kWh). "
        "Long Range/Performance: ~75–82 kWh usable.",
    )
    wirkungsgrad_antrieb: float = Field(
        default=0.94,
        ge=0.85,
        le=0.99,
        description="Electric motor efficiency (±2% tolerance). Typical values: 92–96%.",
    )
    wirkungsgrad_rekuperation: float = Field(
        default=0.75,
        ge=0.65,
        le=0.85,
        description="Overall efficiency for regenerative braking "
        "(chain efficiency: wheel → motor → battery ≈ 75%).",
    )

    # Auxiliary loads
    nebenverbraucher_baseline_kw: float = Field(
        default=0.34,
        ge=0.0,
        le=1.0,
        description="Baseline consumption in kW at park and standby conditions "
        "(without air conditioning/heating).",
    )
    klimaanlage_max_kw: float = Field(
        default=5.5,
        ge=3.0,
        le=8.0,
        description="Maximum power consumption of the air conditioner (A/C). "
        "Full blast ≈ 5–7 kW, typical operation ≈ 1–4 kW.",
    )
    heizung_max_kw: float = Field(
        default=6.0,
        ge=3.0,
        le=10.0,
        description="Maximum heating power (PTC heater or heat pump). "
        "PTC heater (older models): up to 7 kW. "
        "Heat pump (Highland): more efficient, but 6 kW as safe upper bound.",
    )
    komforttemperatur_min_c: float = Field(
        default=18.0,
        ge=10.0,
        le=22.0,
        description="Lower comfort temperature threshold (°C). "
        "Below this value, heating power increases linearly.",
    )
    komforttemperatur_max_c: float = Field(
        default=24.0,
        ge=20.0,
        le=28.0,
        description="Upper comfort temperature threshold (°C). "
        "Above this value, air conditioning power increases linearly.",
    )

    # Tire type & roof box
    reifentyp: Literal["standard", "winter", "low_rolling_resistance", "performance"] = Field(
        default="standard", description="Tire type that modulates rolling resistance."
    )
    dachbox: bool = Field(
        default=False, description="Presence of a roof box (increases cw_wert by ~0.03–0.05)."
    )

    @field_validator("cw_wert")
    @classmethod
    def adjust_cw_for_dachbox(cls, v: float, info) -> float:
        """Passive adjustment of cW value for roof box (no validator, handled in calculation instead)."""
        return v  # Considered in calculation method

    @field_validator("rollwiderstandsbeiwert")
    @classmethod
    def adjust_cr_for_reifentyp(cls, v: float, info) -> float:
        """Passive adjustment of rolling resistance for different tire types."""
        typ_factors = {
            "standard": 1.0,
            "winter": 1.25,
            "low_rolling_resistance": 0.9,
            "performance": 1.1,
        }
        return v * typ_factors.get(info.context.get("reifentyp", "standard"), 1.0)
```

### `SegmentEnergyResult` (own type in the energy module)

```python
class SegmentEnergyResult(BaseModel):
    """Result of energy calculation for a route segment."""

    segment_index: int
    energiebedarf_kwh: float  # Positive: consumption, Negative: regeneration (excess energy)
    rekuperation_kwh: float  # Amount of regeneratively recovered energy (always ≥ 0)
    energiebedarf_brutto_kwh: float  # Sum of all consumers (without regeneration)
    geschwindigkeit_m_s: float  # Average speed in segment (m/s)
    fahrzeit_s: float  # Travel time of segment (s)
    streckenlaenge_m: float  # Length of segment (m)
```

## 4. Public Interface

### `energy.py` – Core API

```python
"""
Energy consumption module (Phase 3).

Plain-text documentation in Google style (Pydantic/typing see models.py).
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
    Calculates energy consumption for a single segment.

    Args:
        segment: Routing segment (geometry, length, speed limit, road surface).
        gradient: Elevation profile gradient for this segment.
        wetter: Weather data (temperature, wind) at the expected traversal time.
        wind: Projection of wind onto the direction of travel (headwind/crosswind).
        fahrzeug_params: Vehicle parameters (mass, cW, rolling resistance, etc.).
        baustellen: optional list of construction zones (overrides speed limit).
        tempolimit_override_kmh: optional speed limit override (for construction logic).

    Returns:
        SegmentEnergyResult with energy consumption, regeneration, travel time, and speed.

    Algorithm:
        1. Determine effective speed (speed limit or override, max 150 km/h).
        2. Calculate forces (rolling resistance incl. road surface factor, aerodynamic drag, gradient, regeneration).
        3. Convert forces → power → energy over travel time.
        4. Add auxiliary loads (temperature-dependent).
        5. Subtract regeneration (physically limited).
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
    Calculates energy consumption for an entire route (segment-by-segment).

    Wrapper function for parallel or sequential processing of multiple segments.
    Weather and wind data must match the order of the route segments.
    """
    ...
```

## 5. External Integration / Algorithm Details

### 5.1 Physics Derivation

All formulas refer to a segment of length `s` (m), travel time `t` (s), average speed `v` (m/s), and gradient angle `α` (rad).

#### 5.1.1 Rolling Resistance (F_roll)

```
F_roll = c_r * f_oberflaeche(segment.oberflaeche) * m * g * cos(α)
```

- `c_r`: rolling resistance coefficient (from vehicle, default: 0.011 for Model 3)
- `m`: vehicle mass (default: 1706 kg)
- `g`: gravitational acceleration ≈ 9.81 m/s²
- `α`: gradient angle (calculated from `gradient.steigung_prozent`: `α = arctan(steigung_prozent / 100)`)

**Note:** The cos(α) component at gradients < 15° ≈ 2–3% is very close to 1.0, but can be calculated exactly for steep sections.

**Road surface factor (`f_oberflaeche`):** `docs/01-project-specifications.md` explicitly names "road surface" as a factor influencing consumption; GraphHopper provides the path detail `surface` for this (see `docs/plans/01-routing.md`, section 5, mapped to `RouteSegment.oberflaeche`). Modeled as a lookup table in `energy.py`, multiplicatively applied to `c_r` (a rough approximation derived from automotive engineering literature — no calibration feature, see item 5 of open questions):

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
    1.0  # Fallback if `oberflaeche` is None or unknown (e.g., highway without surface tag)
)

def f_oberflaeche(oberflaeche: str | None) -> float:
    """Rolling resistance multiplier for the given road surface (rough approximation)."""
    if oberflaeche is None:
        return DEFAULT_OBERFLAECHEN_FAKTOR
    return OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR.get(oberflaeche.lower(), DEFAULT_OBERFLAECHEN_FAKTOR)
```

#### 5.1.2 Aerodynamic Drag (F_luft)

```
F_luft = 0.5 * ρ_luft * cW_eff * A * v_relativ²
```

- `ρ_luft`: air density ≈ 1.225 kg/m³ (ISA standard at 15°C, 1013 hPa)
- `cW_eff`: effective cW value, incl. roof box correction
  - Without roof box: `cW_eff = fahrzeug_params.cw_wert`
  - With roof box: `cW_eff = fahrzeug_params.cw_wert + 0.04` (conservative: +0.03 to +0.05)
- `A`: frontal area = `fahrzeug_params.stirnflaeche_m2` (2.22 m²)
- `v_relativ`: relative speed to the air mass flow
  - `v_relativ = v - gegenwind_ms` (headwind: positive → higher resistance)
  - `gegenwind_ms` is signed from `wind.models.WindComponents`

**Note:** With strong tailwind (`gegenwind_ms < -v`), `v_relativ` would become negative → `v_relativ²` stays positive, but the physical interpretation (wind drives the vehicle) is not in the model. For this case, `v_relativ = max(v_relativ, 0.5 * v)` is applied (minimum 50% of speed without wind).

#### 5.1.3 Gradient (Altitude Energy / F_gradient)

```
F_steigung = m * g * sin(α)
```

- `α = arctan(steigung_prozent / 100)`
- `sin(α)` can be calculated directly via `steigung_prozent / sqrt(10000 + steigung_prozent²)` (without trigonometric function)

**Sign:** For descent (`steigung_prozent < 0`), `F_steigung < 0` (assisting force).

#### 5.1.4 Regenerative Braking (F_rekuperation)

Regeneration is modeled **not** as a force, but as an energy balance:

```
E_rekuperation = min(
    0.5 * m * (v_anfang² - v_ende²) * η_rekup,
    P_rekup_max * t
)
```

- `v_anfang`, `v_ende`: speed at segment start/end (m/s)
- `η_rekup`: efficiency of regenerative braking ≈ 0.75 (default)
- `P_rekup_max`: maximum regeneration power (Tesla Model 3 AWD: 85 kW, default: 80 kW for safety margin)

**Physical constraints:**

- Regeneration works **only during deceleration** (`v_ende < v_anfang`).
- Regeneration **must not produce negative speed** (segment-end speed ≥ 0).
- Regeneration **must not exceed maximum battery charging power** (`P_rekup_max`).

#### 5.1.5 Auxiliary Loads (HVAC / Heating/AC)

```
P_neben = P_baseline + P_HVAC(T_ausser)
```

**Baseline:**

- `P_baseline = fahrzeug_params.nebenverbraucher_baseline_kw` (0.34 kW)

**HVAC correction (linear, temperature-dependent):**

```python
if T_ausser_c < fahrzeug_params.komforttemperatur_min_c:
    # Heating required
    delta_T = fahrzeug_params.komforttemperatur_min_c - T_ausser_c
    P_heizung = fahrzeug_params.heizung_max_kw * (delta_T / 10.0)  # linear up to 10°C difference
    P_neben += min(P_heizung, fahrzeug_params.heizung_max_kw)
elif T_ausser_c > fahrzeug_params.komforttemperatur_max_c:
    # AC required
    delta_T = T_ausser_c - fahrzeug_params.komforttemperatur_max_c
    P_klima = fahrzeug_params.klimaanlage_max_kw * (delta_T / 10.0)
    P_neben += min(P_klima, fahrzeug_params.klimaanlage_max_kw)
else:
    # Comfort range, baseline only
    pass
```

**Example calculation:**

- `T_ausser = -5°C`, `komforttemperatur_min = 18°C` → `delta_T = 23°C` → heating at 100% (max 6 kW).
- `T_ausser = 32°C`, `komforttemperatur_max = 24°C` → `delta_T = 8°C` → AC at ~80% (approx. 4.4 kW).

#### 5.1.6 Force → Power → Energy

1. **Resulting force (F_res)** against the direction of travel:

   ```
   F_res = F_roll + F_luft + max(0, F_steigung)
   ```

   (Gradient positive only for inclines; for descents `F_steigung < 0` is ignored, as the motor no longer works)

2. **Power (P)** over the segment duration:

   ```
   E_brutto = (F_res * s + P_neben * t) / η_antrieb
   ```

   - `s`: distance (m)
   - `t`: travel time (s)
   - `η_antrieb`: drivetrain efficiency ≈ 0.94 (default)

3. **Subtract regeneration:**

   ```
   E_gesamt = E_brutto - E_rekuperation
   ```

   - If `E_gesamt < 0`, then `E_gesamt = 0` is set (energy from the grid is not negative).

### 5.2 Algorithm Steps in Pseudocode

```python
def berechne_segment_verbrauch(segment, gradient, wetter, wind, params, baustellen=None):
    # 1. Determine speed and travel time
    tempolimit = segment.tempolimit_kmh
    if baustellen:
        tempolimit = min(tempolimit, *[bz.tempolimit_kmh for bz in baustellen])

    v_mittel_kmh = min(tempolimit, 150.0)  # max. 150 km/h physically sensible
    v_mittel_ms = v_mittel_kmh / 3.6
    s_m = segment.laenge_m
    t_s = s_m / v_mittel_ms

    # 2. Calculate angle
    alpha_rad = arctan(gradient.steigung_prozent / 100.0)

    # 3. Forces
    F_roll = params.rollwiderstandsbeiwert * params.masse_kg * 9.81 * cos(alpha_rad)
    v_relativ = max(
        v_mittel_ms - wind.gegenwind_ms, 0.5 * v_mittel_ms
    )  # Minimum setting for tailwind
    F_luft = 0.5 * 1.225 * params.cw_wert * params.stirnflaeche_m2 * v_relativ**2
    F_steigung = params.masse_kg * 9.81 * sin(alpha_rad)

    # 4. Energy for movement
    E_bewegung_brutto = (F_roll + F_luft + max(0, F_steigung)) * s_m / params.wirkungsgrad_antrieb

    # 5. HVAC consumption
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

    # 6. Regeneration (only during deceleration)
    # Simplification: fixed start speed v_anfang, end v_ende = 0 for incline / v_anfang for descent
    v_anfang_ms = v_mittel_ms
    v_ende_ms = max(v_mittel_ms - 0.1 * gradient.steigung_prozent, 0)  # simplified deceleration
    if v_ende_ms < v_anfang_ms:
        E_kin = 0.5 * params.masse_kg * (v_anfang_ms**2 - v_ende_ms**2)
        E_rekup = min(E_kin * params.wirkungsgrad_rekuperation, 80_000 * t_s)  # 80 kW max.
    else:
        E_rekup = 0.0

    # 7. Overall result
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

### 5.3 External Sources / Libraries

- **rasterio**: Not used directly in the `energy` module — elevation data already arrives as `SegmentGradient` from the `elevation` module.
- **httpx**: Not used in the `energy` module — external API access occurs in the provider modules `routing`, `weather`, `construction`, `charging_infrastructure`.
- **Pydantic v2**: Used for `VehicleEnergyParameters`, `SegmentEnergyResult`.

### 5.4 Further Research Decisions

| Parameter | Value | Source | Rationale |
| ----------- | ------ | -------- | ------------ |
| Air density `ρ` | 1.225 kg/m³ | ISA standard (15°C, 1013 hPa) | Industry standard; at extreme temperatures < ±20°C corrections (~±3%) may apply, but this is not needed in the first prototype. |
| `cW` Tesla Model 3 | 0.23 | [Chegg, 2022] | Conservative for all Model 3 variants. Newer generation 0.219, but 0.23 has more available data basis. |
| Frontal area | 2.22 m² | [Chegg, 2022] | Standard specification for Model 3. |
| Rolling resistance `c_r` | 0.011 | [Engineering D&B, ORNL studies] | Typical for Model 3 with standard tires at 2.9 bar. |
| Mass (RWD) | 1706 kg | [Tesla Owners Online, 2025] | Based on 3,759 lbs (1,706 kg) for Base RWD. Long Range ~1,828 kg. Default is average for typical configuration. |
| Battery capacity | 62.5 kWh | [EV Database, TMC 2025] | Current 2025 LFP version; Long Range/Performance ~75–82 kWh usable. Default for Standard Range. |
| Motor efficiency | 0.94 | [Tesla Owners Online forum] | Typical range 0.92–0.96; 0.94 as conservative average. |
| Regeneration efficiency | 0.75 | [arXiv:2106.14686, TMC] | Combined chain efficiency (wheel → motor → battery). |
| Regeneration power | 80 kW | [TMC, 2023] | AWD Model 3: 85 kW max., but safety margin 80 kW for default. |
| Baseline consumption | 0.34 kW | [Cleantechnica, 2020] | Park and standby consumption (without HVAC). |
| AC max | 5.5 kW | [EV Speedy, 2024] | Full blast ≈ 5–7 kW; 5.5 kW as conservative average. |
| Heating max (PTC) | 6.0 kW | [Enhauto, 2025] | PTC heater up to 7 kW, heat pump more efficient; 6.0 kW as safe upper limit. |

## 6. Test Strategy

### 6.1 Fixtures

**`tests/fixtures/energy/`**

- `default_model3_params.json`: `VehicleEnergyParameters` as JSON (for test data serialization)
- `model3_segments.json`: 3 typical segments (flat, gradient +3%, descent -4%)
- `wetter_sample_20c_windstill.json`: 20°C, wind speed 0 m/s, wind direction 0°
- `wetter_sample_5c_15kmh_nord.json`: 5°C, wind 15 km/h from north (against direction of travel)
- `wetter_sample_neg10c_heizung.json`: -10°C, AC on heating (max power)
- `segment_gradients.csv`: segment_index, steigung_prozent, hoehendifferenz_m
- `wind_components.json`: wind_gegenwind_ms, wind_seitenwind_ms

**`tests/conftest.py`**

- `default_model3_params()`: `VehicleEnergyParameters()` with defaults.
- `segment_eben()`: `RouteSegment(segment_index=0, laenge_m=1000, tempolimit_kmh=120, geocode=[...], steigung_rohdaten=[...])`
- `segment_steigung_3pct()`: `RouteSegment(segment_index=1, laenge_m=800, tempolimit_kmh=100, ...)` with `steigung_rohdaten=[0, 1.5, 3.0, 2.5, 0]`
- `segment_gefaelle_4pct()`: `RouteSegment(segment_index=2, laenge_m=1200, tempolimit_kmh=110, ...)` with `steigung_rohdaten=[0, -2.0, -4.0, -3.0, 0]`
- `wind_components_windstill()`: `WindComponents(gegenwind_ms=0.0, seitenwind_ms=0.0)`
- `wind_components_gegenwind()`: `WindComponents(gegenwind_ms=5.0, seitenwind_ms=1.0)`
- `wetter_sample_ref()`: reference weather (20°C, windless) as `WeatherSample`

### 6.2 Concrete Example Test Cases

#### Test Case 1: Flat road, windless, 100 km/h (reference case)

**Given:**

- `segment_eben()`: 1000 m, speed limit 120 km/h, gradient 0%
- `wetter_sample_ref()`: 20°C, wind 0 m/s
- `wind_components_windstill()`: 0 m/s headwind
- `default_model3_params()`: Tesla Model 3 defaults

**When:**

- `berechne_segment_verbrauch()` is called with the above data.

**Then:**

- `ergebnis.energiebedarf_kwh ≈ 1.5 kWh` (±0.1 kWh tolerance)
- `ergebnis.rekuperation_kwh ≈ 0.0 kWh`
- `ergebnis.fahrzeit_s ≈ 30.0 s`
- `ergebnis.geschwindigkeit_m_s ≈ 27.78 m/s (100 km/h)`

**Source comparison:** Typical consumption data for Model 3 Long Range on flat road at 100 km/h is ~13–15 kWh/100km (EPA/WLTP). This test case simulates 1 km at 100 km/h → ~1.4–1.6 kWh.

#### Test Case 2: Gradient +3%, 80 km/h, heating (max consumption)

**Given:**

- `segment_steigung_3pct()`: 800 m, speed limit 100 km/h, gradient +3%
- `wetter_sample_neg10c_heizung()`: -10°C
- `wind_components_windstill()`: 0 m/s
- `default_model3_params()`

**When:**

- `berechne_segment_verbrauch()` is called.

**Then:**

- `ergebnis.energiebedarf_kwh ≥ 2.5 kWh` (max consumption at gradient + AC)
- `ergebnis.rekuperation_kwh = 0.0 kWh` (gradient → no deceleration)
- `ergebnis.fahrzeit_s ≈ 36.0 s (80 km/h for 800 m)`

**Calculation note:** At +3% gradient (α ≈ 1.72°), `F_steigung ≈ m * g * sin(α) ≈ 1706 * 9.81 * 0.0299 ≈ 501 N`. Over 800 m → 401 kJ ≈ 0.11 kWh (without efficiency loss). Combined with aerodynamic and rolling resistance and heating (6 kW * 0.01 h ≈ 0.06 kWh) the total is ~2.5 kWh.

#### Test Case 3: Descent -4%, 110 km/h, regeneration active (min consumption / energy gain)

**Given:**

- `segment_gefaelle_4pct()`: 1200 m, speed limit 110 km/h, gradient -4%
- `wetter_sample_ref()`: 20°C, wind 0 m/s
- `wind_components_windstill()`: 0 m/s
- `default_model3_params()`

**When:**

- `berechne_segment_verbrauch()` is called.

**Then:**

- `ergebnis.energiebedarf_kwh ≤ 0.5 kWh` (very low, possibly close to 0)
- `ergebnis.rekuperation_kwh ≥ 0.1 kWh` (regeneration during deceleration)
- `ergebnis.fahrzeit_s ≈ 39.3 s (110 km/h for 1200 m)`

**Note:** During descent, regeneration is activated (`v_ende < v_anfang`), which can lead to negative `energiebedarf_kwh` (energy gain). In the model this is however limited to `0.0`, since negative energy from the grid does not exist — regeneration is reported separately as `rekuperation_kwh`.

### 6.3 Unit vs. Integration Tests

- **Unit tests (pytest)**: All test cases above are unit tests (`tests/test_energy.py`). No live network access, no file I/O.
- **Integration tests (pytest.mark.integration)**: No integration tests for the `energy` module, as it is a purely deterministic calculation (no network dependency). Integration tests occur in the `routing`, `weather`, `elevation` modules where external API or file access occurs.

## 7. Task Checklist

- [ ] **Task 1: Create `src/tripplanner/energy/models.py`**  
  Create `VehicleEnergyParameters` and `SegmentEnergyResult` as Pydantic models with the fields and default values defined in the plan (Tesla Model 3).  
  *Acceptance criterion:* Both models can be instantiated with `VehicleEnergyParameters()` and `SegmentEnergyResult(segment_index=0, energiebedarf_kwh=1.5, rekuperation_kwh=0.0, energiebedarf_brutto_kwh=1.5, geschwindigkeit_m_s=27.78, fahrzeit_s=30.0, streckenlaenge_m=1000.0)`; mypy --strict accepts no type errors.

- [ ] **Task 2: Create `src/tripplanner/energy/__init__.py`**  
  Create a minimal `__init__.py` that exports `berechne_segment_verbrauch` and `berechne_gesamtverbrauch`.  
  *Acceptance criterion:* `from tripplanner.energy import berechne_segment_verbrauch, berechne_gesamtverbrauch` works without error.

- [ ] **Task 3: Create `src/tripplanner/energy/energy.py` (core function)**  
  Implement `berechne_segment_verbrauch()` according to the algorithm defined in the plan (formulas, steps 1–7).  
  *Acceptance criterion:* All 3 example test cases (Task 6) return expected values within tolerance (±0.1 kWh for reference case).

- [ ] **Task 4: Create `src/tripplanner/energy/providers.py` (fake)**  
  Create `EnergyProvider` protocol (in case later extensions become possible) and `FakeEnergyProvider` for tests (simulates `berechne_segment_verbrauch` and returns `SegmentEnergyResult`).  
  *Acceptance criterion:* `FakeEnergyProvider().berechne_segment_verbrauch(...)` returns the same result as the reference implementation.

- [ ] **Task 5: Create `tests/energy/conftest.py`**  
  Create fixtures for `default_model3_params`, `segment_eben`, `segment_steigung_3pct`, `segment_gefaelle_4pct`, `wind_components_windstill`, `wind_components_gegenwind`, `wetter_sample_ref`.  
  *Acceptance criterion:* `pytest --fixtures` shows all fixtures; each fixture can be used in a test.

- [ ] **Task 6: Create `tests/energy/test_energy.py`**  
  Write 3 unit tests for the test cases defined above (flat, gradient +3%, descent -4%). Each test compares result with expected value (`pytest.approx(1.5, rel=0.05)`).  
  *Acceptance criterion:* `pytest tests/energy/test_energy.py -v` outputs 3 passed tests.

- [ ] **Task 7: Create `tests/energy/test_providers.py` (optional)**  
  Write test for `FakeEnergyProvider` that calls `berechne_segment_verbrauch()` with reference data and compares result with direct call to core function.  
  *Acceptance criterion:* Test passes; `FakeEnergyProvider` returns identical values as the direct function.

- [ ] **Task 8: Create `docs/plans/06-energy.md`**  
  This plan file (which you are creating now) is the module documentation.  
  *Acceptance criterion:* The file exists at `docs/plans/06-energy.md` and contains all 8 sections (Purpose & Scope, Dependencies & Phase Assignment, Data Models, Public Interface, Algorithm Details, Test Strategy, Task Checklist, Risks & Open Questions).

- [ ] **Task 9: Finalize documentation `docs/plans/06-energy.md`**  
  Add all details from the plan that are still missing (e.g., links to sources for default parameters).  
  *Acceptance criterion:* Reader can trace all default parameters (value + source) and start implementation without follow-up questions.

- [ ] **Task 10: `ruff` formatting and linting**  
  Run `ruff check src/tripplanner/energy tests/energy` and correct all found rules (E, F, I, UP, B, SIM, PL, RUF, plus D rules with `pydocstyle convention = "google"`).  
  *Acceptance criterion:* `ruff check` reports no more errors.

- [ ] **Task 11: `mypy --strict` type annotation check**  
  Run `mypy --strict src/tripplanner/energy tests/energy`.  
  *Acceptance criterion:* `mypy` reports no type issues (complete type annotations on all public interfaces).

- [ ] **Task 12: `pytest --cov=src/tripplanner/energy --cov-report=term-missing --cov-fail-under=85`**  
  Run tests with coverage gate at 85%.  
  *Acceptance criterion:* Coverage ≥ 85%, `pytest` reports no errors.

- [ ] **Task 13: Conduct code review**  
  Have a colleague review the implementation (or use `task code-documentation-code-reviewer`).  
  *Acceptance criterion:* review feedback is incorporated; no open review comments remain.

- [ ] **Task 14: Simulate example scenario with 5-segment route**  
  Create a small Python script (`scripts/example_energy_calculation.py`) that calculates a 5-segment route with various gradients/weather conditions and outputs results.  
  *Acceptance criterion:* Script runs without error, outputs `SegmentEnergyResult` for each segment (consistent values).

- [ ] **Task 15: Implement `f_oberflaeche()` and rolling resistance integration**
  Implement `OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR`, `f_oberflaeche()` and the application in `F_roll` calculation per section 5.1.1. Add `tests/energy/test_energy.py::test_oberflaechenfaktor_gravel_erhoeht_verbrauch` (same segment with `oberflaeche="asphalt"` vs. `oberflaeche="gravel"` → gravel yields strictly higher `energiebedarf_kwh`) and `test_oberflaechenfaktor_none_fallback` (`oberflaeche=None` → factor 1.0).
  *Acceptance criterion:* Both new tests green; `f_oberflaeche` is unit-testable without further dependencies (pure function).

## 8. Risks & Open Technical Questions

1. **Wind projection with complex wind fields**: The current algorithm uses a constant headwind for the entire segment. For short, highly fluctuating wind gusts (e.g., valleys, wind tunnels), this could be inaccurate. *Solution:* Acceptable for phase 1; later iteration: wind interpolation along segment geometry.

2. **Temperature-dependent battery efficiency**: Regeneration efficiency and maximum charging power depend on battery temperature. This is not considered in the `energy` module (only in the `battery` module). *Solution:* A `battery` module could later deliver a `ChargingEfficiencyCurve` to `energy`, which then feeds into the regeneration calculation.

3. **Regeneration on steep descents**: When the vehicle is "braked" on a descent with regeneration, speed can fall below the speed limit. The current algorithm assumes linear deceleration (`v_ende = v_anfang - 0.1 * steigung_prozent`). *Solution:* Sufficiently accurate for phase 1; later iteration: integration into the optimization loop with variable speed.

4. **HVAC model is very simplified**: Only linear interpolation between comfort thresholds, no consideration of heat pump vs. PTC heater, no inertial effects (heater continues running even after target temperature is reached). *Solution:* This is a conscious compromise for phase 1. Calibration from real driving data (later phase) will automatically improve this model.

5. **Air density `ρ = 1.225 kg/m³` is constant**: No temperature/pressure correction for air density is performed. *Solution:* At extreme temperatures (< -20°C or > +40°C), this could lead to ~±5% deviation. For Europe and medium climate zones, this is negligible.

6. **Tire type and roof box are binary (yes/no)**: There is no continuous modeling (e.g., winter tires "poor" vs. "good"). *Solution:* Tire type enumeration is sufficient for phase 1. Roof box effect (~+4% cW) is dominant enough to be treated as a binary switch.

7. **Default battery capacity 62.5 kWh is for Standard Range (2025)**: If a user drives a Long Range model, they must manually set `batteriekapazitaet_kwh=75` or `82`. *Solution:* No problem; the vehicle profile (`VehicleProfile`) in the `trip_input` module can derive this via model designation (later extension in the `trip_input` module).

8. **No consideration of road condition (wet, snow, ice)**: The rolling resistance coefficient `c_r` is specified for dry road. *Solution:* For Europe and medium climate zones, the model is primarily calibrated on dry roads. Snow/ice is an extreme special case that can be covered in a later iteration via a `RoadConditionProvider`.
