import { describe, it, expect } from "vitest";
import {
  getStopRole,
  isRawCoordinateLabel,
  isUnresolvedAddress,
  swapStops,
  validateForm,
  toggleFaehrAusschluss,
  setFaehrZeitfensterFuer,
  setLadedauerVorgabeFuer,
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

    it("returns 'Zwischenstopp N' for middle stops (1-based label equals index)", () => {
      expect(getStopRole(berlinToHamburgVia, 1)).toBe("Zwischenstopp 1");
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
      expect(getStopRole(four, 1)).toBe("Zwischenstopp 1");
      expect(getStopRole(four, 2)).toBe("Zwischenstopp 2");
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
        zielSoc: 30,
      });
      expect(errors.length).toBeGreaterThan(0);
      expect(errors.some((e) => e.toLowerCase().includes("stop"))).toBe(true);
    });

    it("returns no errors for a valid start/ziel with resolved positions and SoC in range", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        zielSoc: 30,
      });
      expect(errors).toEqual([]);
    });

    it("reports an error when startSoc is below 0", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: -1,
        zielSoc: 30,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("reports an error when startSoc is above 100", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 101,
        zielSoc: 30,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("reports an error when zielSoc is below 0", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        zielSoc: -5,
      });
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("reports an error when zielSoc is above 100", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        zielSoc: 120,
      });
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("treats NaN startSoc as invalid", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: Number.NaN,
        zielSoc: 30,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("treats NaN zielSoc as invalid", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 80,
        zielSoc: Number.NaN,
      });
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });

    it("accepts boundary SoC values 0 and 100", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: 0,
        zielSoc: 100,
      });
      // No SoC errors should appear (stops are valid).
      expect(errors).toEqual([]);
    });

    it("can report both SoC errors simultaneously when both are out of range", () => {
      const errors = validateForm({
        stops: berlinToHamburg,
        startSoc: -1,
        zielSoc: 200,
      });
      expect(errors).toContain("Start-SoC muss zwischen 0 und 100 % liegen.");
      expect(errors).toContain("Ziel-SoC muss zwischen 0 und 100 % liegen.");
    });
  });

  // =========================================================================
  // sameFaehrAusschluss / toggleFaehrAusschluss
  // =========================================================================

  describe("toggleFaehrAusschluss", () => {
    const faehre = {
      name: "Rødby (DK) - Puttgarden (D)",
      bbox_sw: [54.5, 11.22] as [number, number],
      bbox_no: [54.66, 11.36] as [number, number],
    };

    it("adds the ferry when toggled on and not already present", () => {
      const result = toggleFaehrAusschluss([], faehre, true);
      expect(result).toHaveLength(1);
      expect(result[0].name).toBe(faehre.name);
    });

    it("does not duplicate the ferry when toggled on twice", () => {
      const once = toggleFaehrAusschluss([], faehre, true);
      const twice = toggleFaehrAusschluss(once, faehre, true);
      expect(twice).toHaveLength(1);
    });

    it("removes the ferry when toggled off", () => {
      const withFaehre = toggleFaehrAusschluss([], faehre, true);
      const result = toggleFaehrAusschluss(withFaehre, faehre, false);
      expect(result).toHaveLength(0);
    });

    it("toggling off an absent ferry is a no-op", () => {
      const result = toggleFaehrAusschluss([], faehre, false);
      expect(result).toHaveLength(0);
    });

    it("distinguishes ferries by all fields, not just name (same name, different bbox)", () => {
      // Two ferries with the same name but different bounding boxes should be distinct
      const faehre1 = {
        name: "Fährverbindung A",
        bbox_sw: [50.0, 10.0] as [number, number],
        bbox_no: [50.5, 10.5] as [number, number],
      };
      const faehre2 = {
        name: "Fährverbindung A",
        bbox_sw: [51.0, 11.0] as [number, number],
        bbox_no: [51.5, 11.5] as [number, number],
      };
      const withFaehre1 = toggleFaehrAusschluss([], faehre1, true);
      expect(withFaehre1).toHaveLength(1);
      // Toggling on faehre2 adds it as a separate entry (not a duplicate)
      const withBoth = toggleFaehrAusschluss(withFaehre1, faehre2, true);
      expect(withBoth).toHaveLength(2);
      // Toggling off faehre1 removes only faehre1, leaving faehre2
      const afterRemoveFaehre1 = toggleFaehrAusschluss(
        withBoth,
        faehre1,
        false,
      );
      expect(afterRemoveFaehre1).toHaveLength(1);
      expect(afterRemoveFaehre1[0].bbox_sw[0]).toBe(51.0);
    });
  });

  // =========================================================================
  // setFaehrZeitfensterFuer
  // =========================================================================

  describe("setFaehrZeitfensterFuer", () => {
    const faehre = {
      name: "Rødby (DK) - Puttgarden (D)",
      bbox_sw: [54.5, 11.22] as [number, number],
      bbox_no: [54.66, 11.36] as [number, number],
    };

    it("adds a time window when both abfahrt and ankunft are given", () => {
      const result = setFaehrZeitfensterFuer(
        [],
        faehre,
        "2026-08-15T10:00:00",
        "2026-08-15T11:09:00",
      );
      expect(result).toHaveLength(1);
      expect(result[0].abfahrt).toBe("2026-08-15T10:00:00");
      expect(result[0].ankunft).toBe("2026-08-15T11:09:00");
    });

    it("replaces an existing time window for the same ferry instead of duplicating", () => {
      const once = setFaehrZeitfensterFuer(
        [],
        faehre,
        "2026-08-15T10:00:00",
        "2026-08-15T11:09:00",
      );
      const updated = setFaehrZeitfensterFuer(
        once,
        faehre,
        "2026-08-15T12:00:00",
        "2026-08-15T13:09:00",
      );
      expect(updated).toHaveLength(1);
      expect(updated[0].abfahrt).toBe("2026-08-15T12:00:00");
    });

    it("removes the time window when either abfahrt or ankunft is empty", () => {
      const once = setFaehrZeitfensterFuer(
        [],
        faehre,
        "2026-08-15T10:00:00",
        "2026-08-15T11:09:00",
      );
      const cleared = setFaehrZeitfensterFuer(once, faehre, "", "");
      expect(cleared).toHaveLength(0);
    });

    it("does not add a time window when abfahrt/ankunft are both empty", () => {
      const result = setFaehrZeitfensterFuer([], faehre, "", "");
      expect(result).toHaveLength(0);
    });
  });

  // =========================================================================
  // setLadedauerVorgabeFuer
  // =========================================================================

  describe("setLadedauerVorgabeFuer", () => {
    it("adds a duration override converted from minutes to seconds", () => {
      const result = setLadedauerVorgabeFuer([], "station-1", 30);
      expect(result).toEqual([{ station_id: "station-1", ladedauer_s: 1800 }]);
    });

    it("replaces an existing override for the same station instead of duplicating", () => {
      const once = setLadedauerVorgabeFuer([], "station-1", 30);
      const updated = setLadedauerVorgabeFuer(once, "station-1", 45);
      expect(updated).toEqual([{ station_id: "station-1", ladedauer_s: 2700 }]);
    });

    it("removes the override when the given minutes are zero or negative", () => {
      const once = setLadedauerVorgabeFuer([], "station-1", 30);
      const cleared = setLadedauerVorgabeFuer(once, "station-1", 0);
      expect(cleared).toHaveLength(0);
    });

    it("keeps overrides for other stations untouched", () => {
      const withTwo = setLadedauerVorgabeFuer(
        setLadedauerVorgabeFuer([], "station-1", 30),
        "station-2",
        15,
      );
      const updated = setLadedauerVorgabeFuer(withTwo, "station-1", 20);
      expect(updated).toHaveLength(2);
      expect(
        updated.find((v) => v.station_id === "station-2")?.ladedauer_s,
      ).toBe(900);
    });
  });
});
