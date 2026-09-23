import {
  getStopRole,
  isRawCoordinateLabel,
  isUnresolvedAddress,
  swapStops,
  validateForm,
  toggleFerryExclusion,
  setFerryTimeWindowFor,
  setChargingDurationPresetFor,
  differsAsTime,
  formatChargingStationName,
  formatDrivingSegmentDistance,
  formatDrivingSegmentDuration,
} from "@/components/TripPlannerForm";
import type { Stop } from "@/types/trip-request";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** Hilfsfunktion: erzeugt einen Stop mit default-Werten. */
function makeStop(overrides: Partial<Stop> = {}): Stop {
  return {
    id: crypto.randomUUID(),
    address: "",
    position: null,
    ...overrides,
  };
}

/** Zwei Stopps mit aufgelösten Positionen (z. B. Berlin → Hamburg). */
const berlinToHamburg: Stop[] = [
  makeStop({ address: "Berlin", position: [52.52, 13.405] }),
  makeStop({ address: "Hamburg", position: [53.551, 9.993] }),
];

/** Drei Stopps: Berlin → Hannover → Hamburg. */
const berlinToHamburgVia: Stop[] = [
  makeStop({ address: "Berlin", position: [52.52, 13.405] }),
  makeStop({ address: "Hannover", position: [52.3759, 9.732] }),
  makeStop({ address: "Hamburg", position: [53.551, 9.993] }),
];

// ===========================================================================
// getStopRole
// ===========================================================================

describe("TripPlannerForm pure helpers", () => {
  describe("getStopRole", () => {
    it("returns empty string for an empty stops array at any index", () => {
      expect(getStopRole([], 0)).toBe("");
      expect(getStopRole([], 3)).toBe("");
    });

    it("returns 'Start' for index 0", () => {
      expect(getStopRole(berlinToHamburg, 0)).toBe("Start");
      expect(getStopRole(berlinToHamburgVia, 0)).toBe("Start");
    });

    it("returns 'Ziel' for the last index", () => {
      expect(getStopRole(berlinToHamburg, 1)).toBe("Ziel");
      expect(getStopRole(berlinToHamburgVia, 2)).toBe("Ziel");
    });

    it("returns 'Stop N' for middle stops (1-based label equals index)", () => {
      expect(getStopRole(berlinToHamburgVia, 1)).toBe("Stop 1");
    });

    it("treats a single stop as both Start and Ziel (index 0 wins)", () => {
      const single = [
        makeStop({ address: "Berlin", position: [52.52, 13.405] }),
      ];
      expect(getStopRole(single, 0)).toBe("Start");
    });

    it("labels intermediate stops correctly in a 4-stop array", () => {
      const four: Stop[] = [
        makeStop({ address: "A" }),
        makeStop({ address: "B" }),
        makeStop({ address: "C" }),
        makeStop({ address: "D" }),
      ];
      expect(getStopRole(four, 0)).toBe("Start");
      expect(getStopRole(four, 1)).toBe("Stop 1");
      expect(getStopRole(four, 2)).toBe("Stop 2");
      expect(getStopRole(four, 3)).toBe("Ziel");
    });
  });

  // =========================================================================
  // isRawCoordinateLabel
  // =========================================================================

  describe("isRawCoordinateLabel", () => {
    it("returns true for the reverse-geocode fallback format 'lat, lon'", () => {
      expect(isRawCoordinateLabel("52.5200, 13.4050")).toBe(true);
    });

    it("returns true for negative coordinates", () => {
      expect(isRawCoordinateLabel("-33.8688, -151.2093")).toBe(true);
    });

    it("returns true for integer-valued coordinates", () => {
      expect(isRawCoordinateLabel("52, 13")).toBe(true);
    });

    it("returns true when coordinates are separated by a comma without space", () => {
      expect(isRawCoordinateLabel("52.52,13.405")).toBe(true);
    });

    it("returns true for leading/trailing whitespace (trims before matching)", () => {
      expect(isRawCoordinateLabel("  52.52, 13.405  ")).toBe(true);
    });

    it("returns false for a normal human-readable address", () => {
      expect(isRawCoordinateLabel("Brandenburger Tor, Berlin")).toBe(false);
    });

    it("returns false for an empty string", () => {
      expect(isRawCoordinateLabel("")).toBe(false);
    });

    it("returns false for a single number (missing comma)", () => {
      expect(isRawCoordinateLabel("52.5200")).toBe(false);
    });

    it("returns false for three comma-separated values", () => {
      expect(isRawCoordinateLabel("52.52, 13.405, 0")).toBe(false);
    });
  });

  // =========================================================================
  // isUnresolvedAddress
  // =========================================================================

  describe("isUnresolvedAddress", () => {
    it("returns true when position is null (not yet geocoded)", () => {
      expect(
        isUnresolvedAddress(makeStop({ position: null, address: "Berlin" })),
      ).toBe(true);
    });

    it("returns true when address is empty (even with a position)", () => {
      expect(
        isUnresolvedAddress(
          makeStop({ position: [52.52, 13.405], address: "" }),
        ),
      ).toBe(true);
    });

    it("returns true when address is a raw coordinate label (fallback), even with position", () => {
      expect(
        isUnresolvedAddress(
          makeStop({ position: [52.52, 13.405], address: "52.5200, 13.4050" }),
        ),
      ).toBe(true);
    });

    it("returns false when address is human-readable and position is set", () => {
      expect(
        isUnresolvedAddress(
          makeStop({ position: [52.52, 13.405], address: "Brandenburger Tor" }),
        ),
      ).toBe(false);
    });

    it("returns true for a brand-new empty stop (createEmptyStop-style)", () => {
      expect(isUnresolvedAddress(makeStop())).toBe(true);
    });
  });

  // =========================================================================
  // swapStops
  // =========================================================================

  describe("swapStops", () => {
    it("swaps two elements and returns a new array (does not mutate input)", () => {
      const result = swapStops(berlinToHamburgVia, 0, 2);
      const [first, , last] = result;
      expect(first).toBe(berlinToHamburgVia[2]);
      expect(last).toBe(berlinToHamburgVia[0]);
      // Middle element stays in place.
      expect(result[1]).toBe(berlinToHamburgVia[1]);
      // Input array is unchanged.
      expect(berlinToHamburgVia[0].address).toBe("Berlin");
      expect(berlinToHamburgVia[2].address).toBe("Hamburg");
      // Returned reference is a new array.
      expect(result).not.toBe(berlinToHamburgVia);
    });

    it("returns the same array reference when from === to", () => {
      const result = swapStops(berlinToHamburgVia, 1, 1);
      expect(result).toBe(berlinToHamburgVia);
    });

    it("returns the same array reference when 'from' is out of bounds (low)", () => {
      expect(swapStops(berlinToHamburg, -1, 1)).toBe(berlinToHamburg);
    });

    it("returns the same array reference when 'from' is out of bounds (high)", () => {
      expect(swapStops(berlinToHamburg, 5, 1)).toBe(berlinToHamburg);
    });

    it("returns the same array reference when 'to' is out of bounds (low)", () => {
      expect(swapStops(berlinToHamburg, 0, -1)).toBe(berlinToHamburg);
    });

    it("returns the same array reference when 'to' is out of bounds (high)", () => {
      expect(swapStops(berlinToHamburg, 0, 5)).toBe(berlinToHamburg);
    });

    it("returns the same array reference when stops is empty", () => {
      expect(swapStops([], 0, 1)).toEqual([]);
    });

    it("correctly swaps adjacent middle stops", () => {
      const result = swapStops(berlinToHamburgVia, 1, 2);
      expect(result[1].address).toBe("Hamburg");
      expect(result[2].address).toBe("Hannover");
      expect(result[0].address).toBe("Berlin");
    });
  });

  // =========================================================================
  // validateForm
  // =========================================================================

  describe("validateForm", () => {
    it("delegates stop validation to validateStops (e.g. fewer than two stops)", () => {
      const errors = validateForm({
        stops: [makeStop({ address: "Berlin", position: [52.52, 13.405] })],
        startSoc: 80,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors.length).toBeGreaterThan(0);
      expect(errors.some((e) => e.toLowerCase().includes("stop"))).toBe(true);
    });

    it("returns no errors for a valid start/destination with resolved positions and SoC in range", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toEqual([]);
    });

    it("reports an error when startSoc is below 0", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: -1,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("reports an error when startSoc is above 100", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 101,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("reports an error when targetSoc is below 0", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        targetSoc: -5,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("reports an error when targetSoc is above 100", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        targetSoc: 120,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("treats NaN startSoc as invalid", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: Number.NaN,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("treats NaN targetSoc as invalid", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        targetSoc: Number.NaN,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("accepts boundary SoC values 0 and 100", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 0,
        targetSoc: 100,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      // No SoC errors should appear (stops are valid).
      expect(errors).toEqual([]);
    });

    it("can report both SoC errors simultaneously when both are out of range", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: -1,
        targetSoc: 200,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("reports an error when maxChargeSocPct is below 0", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
        maxChargeSocPct: -1,
      });
      expect(errors).toContain(
        "Max. Lade-SoC muss zwischen 0 und 100 % liegen.",
      );
    });

    it("reports an error when maxChargeSocPct is above 100", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
        maxChargeSocPct: 150,
      });
      expect(errors).toContain(
        "Max. Lade-SoC muss zwischen 0 und 100 % liegen.",
      );
    });

    it("accepts maxChargeSocPct at the boundaries 0 and 100", () => {
      for (const value of [0, 100]) {
        const errors = validateForm({
          stops: berlinToHamburg,
          startSoc: 80,
          targetSoc: 30,
          minArrivalSocPct: 5,
          minChargingTimeMin: 10,
          maxChargeSocPct: value,
        });
        expect(errors).toEqual([]);
      }
    });

    it("reports an error when maxChargeSocPct is NaN", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        targetSoc: 30,
        minArrivalSocPct: 5,
        minChargingTimeMin: 10,
        maxChargeSocPct: Number.NaN,
      });
      expect(errors).toContain(
        "Max. Lade-SoC muss zwischen 0 und 100 % liegen.",
      );
    });
  });

  // =========================================================================
  // sameFerryExclusion / toggleFerryExclusion
  // =========================================================================

  describe("toggleFerryExclusion", () => {
    const ferry = {
      name: "Rødby (DK) - Puttgarden (D)",
      bboxSw: [54.5, 11.22] as [number, number],
      bboxNe: [54.66, 11.36] as [number, number],
    };

    it("adds the ferry when toggled on and not already present", () => {
      const result = toggleFerryExclusion([], ferry, true);
      expect(result).toHaveLength(1);
      expect(result[0].name).toBe(ferry.name);
    });

    it("does not duplicate the ferry when toggled on twice", () => {
      const once = toggleFerryExclusion([], ferry, true);
      const twice = toggleFerryExclusion(once, ferry, true);
      expect(twice).toHaveLength(1);
    });

    it("removes the ferry when toggled off", () => {
      const withFerry = toggleFerryExclusion([], ferry, true);
      const result = toggleFerryExclusion(withFerry, ferry, false);
      expect(result).toHaveLength(0);
    });

    it("toggling off an absent ferry is a no-op", () => {
      const result = toggleFerryExclusion([], ferry, false);
      expect(result).toHaveLength(0);
    });

    it("distinguishes ferries by all fields, not just name (same name, different bbox)", () => {
      // Two ferries with the same name but different bounding boxes should be distinct
      const faehre1 = {
        name: "Fährverbindung A",
        bboxSw: [50.0, 10.0] as [number, number],
        bboxNe: [50.5, 10.5] as [number, number],
      };
      const faehre2 = {
        name: "Fährverbindung A",
        bboxSw: [51.0, 11.0] as [number, number],
        bboxNe: [51.5, 11.5] as [number, number],
      };
      const withFaehre1 = toggleFerryExclusion([], faehre1, true);
      expect(withFaehre1).toHaveLength(1);
      // Toggling on faehre2 adds it as a separate entry (not a duplicate)
      const withBoth = toggleFerryExclusion(withFaehre1, faehre2, true);
      expect(withBoth).toHaveLength(2);
      // Toggling off faehre1 removes only faehre1, leaving faehre2
      const afterRemoveFaehre1 = toggleFerryExclusion(withBoth, faehre1, false);
      expect(afterRemoveFaehre1).toHaveLength(1);
      expect(afterRemoveFaehre1[0].bboxSw[0]).toBe(51.0);
    });
  });

  // =========================================================================
  // setFerryTimeWindowFor
  // =========================================================================

  describe("setFerryTimeWindowFor", () => {
    const ferry = {
      name: "Rødby (DK) - Puttgarden (D)",
      bboxSw: [54.5, 11.22] as [number, number],
      bboxNe: [54.66, 11.36] as [number, number],
    };

    it("adds a time window when both departure and arrival are given", () => {
      const result = setFerryTimeWindowFor(
        [],
        ferry,
        "2026-08-15T10:00:00",
        "2026-08-15T11:09:00",
      );
      expect(result).toHaveLength(1);
      expect(result[0].departure).toBe("2026-08-15T10:00:00");
      expect(result[0].arrival).toBe("2026-08-15T11:09:00");
    });

    it("replaces an existing time window for the same ferry instead of duplicating", () => {
      const once = setFerryTimeWindowFor(
        [],
        ferry,
        "2026-08-15T10:00:00",
        "2026-08-15T11:09:00",
      );
      const updated = setFerryTimeWindowFor(
        once,
        ferry,
        "2026-08-15T12:00:00",
        "2026-08-15T13:09:00",
      );
      expect(updated).toHaveLength(1);
      expect(updated[0].departure).toBe("2026-08-15T12:00:00");
    });

    it("removes the time window when either departure or arrival is empty", () => {
      const once = setFerryTimeWindowFor(
        [],
        ferry,
        "2026-08-15T10:00:00",
        "2026-08-15T11:09:00",
      );
      const cleared = setFerryTimeWindowFor(once, ferry, "", "");
      expect(cleared).toHaveLength(0);
    });

    it("does not add a time window when departure/arrival are both empty", () => {
      const result = setFerryTimeWindowFor([], ferry, "", "");
      expect(result).toHaveLength(0);
    });
  });

  // =========================================================================
  // setChargingDurationPresetFor
  // =========================================================================

  describe("setChargingDurationPresetFor", () => {
    it("adds a duration override converted from minutes to seconds", () => {
      const result = setChargingDurationPresetFor([], "station-1", 30);
      expect(result).toEqual([
        { stationId: "station-1", chargingDurationS: 1800 },
      ]);
    });

    it("replaces an existing override for the same station instead of duplicating", () => {
      const once = setChargingDurationPresetFor([], "station-1", 30);
      const updated = setChargingDurationPresetFor(once, "station-1", 45);
      expect(updated).toEqual([
        { stationId: "station-1", chargingDurationS: 2700 },
      ]);
    });

    it("removes the override when the given minutes are zero or negative", () => {
      const once = setChargingDurationPresetFor([], "station-1", 30);
      const cleared = setChargingDurationPresetFor(once, "station-1", 0);
      expect(cleared).toHaveLength(0);
    });

    it("keeps overrides for other stations untouched", () => {
      const withTwo = setChargingDurationPresetFor(
        setChargingDurationPresetFor([], "station-1", 30),
        "station-2",
        15,
      );
      const updated = setChargingDurationPresetFor(withTwo, "station-1", 20);
      expect(updated).toHaveLength(2);
      expect(
        updated.find((v) => v.stationId === "station-2")?.chargingDurationS,
      ).toBe(900);
    });
  });

  // =========================================================================
  // differsAsTime
  // =========================================================================

  describe("differsAsTime", () => {
    it("gibt false zurück, wenn beide Zeitpunkte auf dieselbe Minute fallen", () => {
      expect(differsAsTime("2026-08-17T01:14:00", "2026-08-17T01:14:00")).toBe(
        false,
      );
    });

    it("gibt true zurück, wenn sich die angezeigte Uhrzeit unterscheidet", () => {
      expect(differsAsTime("2026-08-17T00:40:00", "2026-08-17T00:53:00")).toBe(
        true,
      );
    });

    it("gibt true zurück, wenn einer der beiden Zeitpunkte unbekannt ist", () => {
      expect(differsAsTime(null, "2026-08-17T00:53:00")).toBe(true);
      expect(differsAsTime("2026-08-17T00:53:00", null)).toBe(true);
      expect(differsAsTime(null, null)).toBe(true);
    });
  });

  // =========================================================================
  // formatChargingStationName
  // =========================================================================

  describe("formatChargingStationName", () => {
    it("entfernt den 'Tesla Supercharger - '-Präfix", () => {
      expect(
        formatChargingStationName("Tesla Supercharger - Berlin Alexanderplatz"),
      ).toBe("Berlin Alexanderplatz");
    });

    it("lässt Namen ohne diesen Präfix unverändert", () => {
      expect(formatChargingStationName("Ionity Rasthof Rhön")).toBe(
        "Ionity Rasthof Rhön",
      );
    });

    it("entfernt den Präfix nicht, wenn er nicht am Anfang steht", () => {
      expect(
        formatChargingStationName("Nahe Tesla Supercharger - Berlin"),
      ).toBe("Nahe Tesla Supercharger - Berlin");
    });
  });

  // =========================================================================
  // formatDrivingSegmentDistance / formatDrivingSegmentDuration
  // =========================================================================

  describe("formatDrivingSegmentDistance", () => {
    it("formatiert km mit einer Nachkommastelle und deutschem Komma", () => {
      expect(formatDrivingSegmentDistance(42.05)).toBe("42,1 km");
      expect(formatDrivingSegmentDistance(0)).toBe("0,0 km");
    });
  });

  describe("formatDrivingSegmentDuration", () => {
    it("formatiert unter einer Stunde nur in Minuten", () => {
      expect(formatDrivingSegmentDuration(35)).toBe("35min");
    });

    it("formatiert ab einer Stunde als 'Xh Ymin'", () => {
      expect(formatDrivingSegmentDuration(90)).toBe("1h 30min");
    });

    it("rundet auf ganze Minuten", () => {
      expect(formatDrivingSegmentDuration(59.6)).toBe("1h 0min");
    });
  });
});
