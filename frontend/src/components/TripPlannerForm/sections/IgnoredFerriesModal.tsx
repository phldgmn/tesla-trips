import { Modal } from "../../Modal";
import type { FerrySegment } from "../../../types";
import {
  sameFerryExclusion,
  toggleFerryExclusion,
  ferryKey,
} from "../ferry-helpers";
import type { TripPlannerState } from "../useTripPlannerState";

interface IgnoredFerriesModalProps {
  state: TripPlannerState;
  open: boolean;
  onClose: () => void;
  detectedFerries: FerrySegment[] | undefined;
  isSubmitting: boolean;
}

/** Permanently excluded ferry connections (independent of the currently
 *  computed route), opened from the counter part of the ferry pill. */
export function IgnoredFerriesModal({
  state,
  open,
  onClose,
  detectedFerries,
  isSubmitting,
}: IgnoredFerriesModalProps) {
  const { avoidedFerries, setAvoidedFerries, submit } = state;
  return (
    <Modal open={open} onClose={onClose} title="Ignorierte Fähren">
      {avoidedFerries.length === 0 ? (
        <p style={{ margin: 0, color: "#6b7280" }}>Keine ignorierten Fähren.</p>
      ) : (
        <div style={{ display: "grid", gap: "0.5rem" }}>
          {avoidedFerries.map((entry) => {
            const lengthM =
              (detectedFerries ?? []).find((f) =>
                sameFerryExclusion(
                  { name: f.name, bboxSw: f.bboxSw, bboxNe: f.bboxNe },
                  entry,
                ),
              )?.lengthM ?? null;
            return (
              <div
                key={ferryKey(entry)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "0.5rem",
                  border: "1px solid #e5e7eb",
                  borderRadius: "6px",
                  padding: "0.5rem 0.75rem",
                }}
              >
                <span style={{ fontSize: "0.85rem" }}>
                  {entry.name}
                  {lengthM !== null && ` (${(lengthM / 1000).toFixed(1)} km)`}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    const next = toggleFerryExclusion(
                      avoidedFerries,
                      entry,
                      false,
                    );
                    setAvoidedFerries(next);
                    submit({ avoidedFerries: next });
                  }}
                  disabled={isSubmitting}
                  style={{
                    padding: "0.2rem 0.5rem",
                    fontSize: "0.75rem",
                    background: "#dcfce7",
                    border: "1px solid #86efac",
                    borderRadius: "4px",
                    color: "#166534",
                    cursor: isSubmitting ? "not-allowed" : "pointer",
                  }}
                >
                  Wieder zulassen
                </button>
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
