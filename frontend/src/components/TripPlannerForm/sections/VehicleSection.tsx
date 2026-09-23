import { useState } from "react";
import { Modal } from "../../Modal";
import type { VehicleProfileInput } from "../../../types/trip-request";
import { VEHICLE_PROFILE_PRESETS } from "../../../data/vehicleProfiles";
import type { TripPlannerState } from "../useTripPlannerState";

interface VehicleSectionProps {
  state: TripPlannerState;
}

/** "Fahrzeug & Ladestand" button with its modal: vehicle preset, advanced
 *  physical parameters and SoC/charging limits. */
export function VehicleSection({ state }: VehicleSectionProps) {
  const [isVehicleModalOpen, setIsVehicleModalOpen] = useState(false);
  const {
    selectedPresetId,
    selectPreset: handlePresetChange,
    minArrivalSocPct,
    setMinArrivalSocPct,
    minChargeTimeMin,
    setMinChargeTimeMin,
    maxChargeSocPct,
    setMaxChargeSocPct,
  } = state;

  return (
    <>
      <button
        type="button"
        onClick={() => setIsVehicleModalOpen(true)}
        style={{
          width: "100%",
          padding: "0.75rem",
          marginBottom: "0.75rem",
          background: "white",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <div style={{ fontWeight: 600 }}>Fahrzeug & Ladestand</div>
        <div
          style={{ fontSize: "0.8rem", color: "#6b7280", marginTop: "0.2rem" }}
        >
          {VEHICLE_PROFILE_PRESETS.find((p) => p.id === selectedPresetId)
            ?.label ?? "Benutzerdefiniert"}
        </div>
      </button>

      <Modal
        open={isVehicleModalOpen}
        onClose={() => setIsVehicleModalOpen(false)}
        title="Fahrzeug & Ladestand"
      >
        <select
          value={selectedPresetId ?? ""}
          onChange={(e) => handlePresetChange(e.target.value)}
          style={{ width: "100%", padding: "0.5rem", marginBottom: "0.5rem" }}
        >
          {VEHICLE_PROFILE_PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.label}
            </option>
          ))}
          <option value="">— Benutzerdefiniert —</option>
        </select>

        <VehicleAdvanced state={state} />

        <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Min. SoC an Ladestationen (%)
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={minArrivalSocPct}
              onChange={(e) =>
                setMinArrivalSocPct(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Ermöglicht, den SoC an Ladestationen niedriger sinken zu lassen
              als die allgemeine Sicherheitsreserve, um die schnellere
              Ladeleistung im unteren SoC-Bereich zu nutzen.
            </small>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Min. Ladedauer (min)
            </label>
            <input
              type="number"
              min="0"
              max="30"
              step="1"
              value={minChargeTimeMin}
              onChange={(e) =>
                setMinChargeTimeMin(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Verhindert unnötig kurze Ladehalte - ein Halt dauert entweder gar
              nicht oder mindestens so lange.
            </small>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Max. Lade-SoC (%)
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={maxChargeSocPct}
              onChange={(e) =>
                setMaxChargeSocPct(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Obergrenze für den Ladeziel-SoC an regulären Ladestopps - 100
              deaktiviert die Begrenzung. Laden an Zwischenstopps ist nicht
              betroffen.
            </small>
          </div>
        </div>
      </Modal>
    </>
  );
}

function VehicleAdvanced({ state }: VehicleSectionProps) {
  const {
    vehicleProfile: v,
    setVehicleField: handleVehicleFieldChange,
    showAdvancedVehicle,
    setShowAdvancedVehicle,
  } = state;
  return (
    <details
      open={showAdvancedVehicle}
      onToggle={() => setShowAdvancedVehicle(!showAdvancedVehicle)}
      style={{ marginTop: "1rem" }}
    >
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        Erweitert
      </summary>
      <div style={{ marginTop: "0.75rem", display: "grid", gap: "0.75rem" }}>
        <div>
          <label style={{ display: "block", marginBottom: "0.25rem" }}>
            Masse (kg)
          </label>
          <input
            type="number"
            step="1"
            value={v.massKg}
            onChange={(e) =>
              handleVehicleFieldChange("massKg", parseFloat(e.target.value))
            }
            style={{ width: "100%", padding: "0.4rem" }}
          />
        </div>
        <div>
          <label style={{ display: "block", marginBottom: "0.25rem" }}>
            cW-Wert
          </label>
          <input
            type="number"
            step="0.001"
            value={v.dragCoefficient}
            onChange={(e) =>
              handleVehicleFieldChange(
                "dragCoefficient",
                parseFloat(e.target.value),
              )
            }
            style={{ width: "100%", padding: "0.4rem" }}
          />
        </div>
        <div>
          <label style={{ display: "block", marginBottom: "0.25rem" }}>
            Stirnfläche (m²)
          </label>
          <input
            type="number"
            step="0.01"
            value={v.frontalAreaM2}
            onChange={(e) =>
              handleVehicleFieldChange(
                "frontalAreaM2",
                parseFloat(e.target.value),
              )
            }
            style={{ width: "100%", padding: "0.4rem" }}
          />
        </div>
        <div>
          <label style={{ display: "block", marginBottom: "0.25rem" }}>
            Rollwiderstandsbeiwert
          </label>
          <input
            type="number"
            step="0.0001"
            value={v.rollingResistanceCoefficient}
            onChange={(e) =>
              handleVehicleFieldChange(
                "rollingResistanceCoefficient",
                parseFloat(e.target.value),
              )
            }
            style={{ width: "100%", padding: "0.4rem" }}
          />
        </div>
        <div>
          <label style={{ display: "block", marginBottom: "0.25rem" }}>
            Batteriekapazität (kWh)
          </label>
          <input
            type="number"
            step="0.1"
            value={v.batteryCapacityKwh}
            onChange={(e) =>
              handleVehicleFieldChange(
                "batteryCapacityKwh",
                parseFloat(e.target.value),
              )
            }
            style={{ width: "100%", padding: "0.4rem" }}
          />
        </div>
        <div>
          <label style={{ display: "block", marginBottom: "0.25rem" }}>
            Nebenverbraucher Baseline (kW)
          </label>
          <input
            type="number"
            step="0.01"
            value={v.auxiliaryBaselineKw}
            onChange={(e) =>
              handleVehicleFieldChange(
                "auxiliaryBaselineKw",
                parseFloat(e.target.value),
              )
            }
            style={{ width: "100%", padding: "0.4rem" }}
          />
        </div>
        <div>
          <label style={{ display: "block", marginBottom: "0.25rem" }}>
            Reifentyp
          </label>
          <select
            value={v.tireType}
            onChange={(e) =>
              handleVehicleFieldChange(
                "tireType",
                e.target.value as VehicleProfileInput["tireType"],
              )
            }
            style={{ width: "100%", padding: "0.5rem" }}
          >
            <option value="standard">Standard</option>
            <option value="winter">Winter</option>
            <option value="low_rolling_resistance">
              Low Rolling Resistance
            </option>
            <option value="performance">Performance</option>
          </select>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <input
            type="checkbox"
            checked={v.roofBox}
            onChange={(e) =>
              handleVehicleFieldChange("roofBox", e.target.checked)
            }
            id="roofBox-checkbox"
          />
          <label htmlFor="roofBox-checkbox">Dachbox</label>
        </div>
      </div>
    </details>
  );
}
