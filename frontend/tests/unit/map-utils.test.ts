import { describe, it, expect } from "vitest";
import {
  socToColor,
  segmentAvgSoc,
  stopRole,
  roleToMarkerColor,
  roleToLabel,
  roleToMarkerGlyph,
  buildMarkerElement,
  buildPopupText,
  buildChargingStopMarkerElement,
  buildChargingStopPopupHtml,
  formatChargingDuration,
} from "@/components/Map";
import type { Stop, StopRole } from "@/components/Map";
import type { ChargingStop } from "@/types";

describe("MapVisualization utilities", () => {
  describe("socToColor", () => {
    it("should return red for SoC <= 20%", () => {
      expect(socToColor(0)).toBe("#ef4444");
      expect(socToColor(20)).toBe("#ef4444");
    });

    it("should return orange for 20% < SoC <= 40%", () => {
      expect(socToColor(21)).toBe("#f97316");
      expect(socToColor(40)).toBe("#f97316");
    });

    it("should return yellow for 40% < SoC <= 60%", () => {
      expect(socToColor(41)).toBe("#eab308");
      expect(socToColor(60)).toBe("#eab308");
    });

    it("should return light green for 60% < SoC <= 80%", () => {
      expect(socToColor(61)).toBe("#84cc16");
      expect(socToColor(80)).toBe("#84cc16");
    });

    it("should return dark green for SoC > 80%", () => {
      expect(socToColor(81)).toBe("#22c55e");
      expect(socToColor(100)).toBe("#22c55e");
    });
  });

  describe("segmentAvgSoc", () => {
    it("should compute average of start and end SoC", () => {
      expect(segmentAvgSoc(100, 80)).toBe(90);
      expect(segmentAvgSoc(80, 60)).toBe(70);
      expect(segmentAvgSoc(50, 50)).toBe(50);
    });

    it("should handle extremes correctly", () => {
      expect(segmentAvgSoc(0, 100)).toBe(50);
      expect(segmentAvgSoc(100, 0)).toBe(50);
    });
  });

  describe("stopRole", () => {
    it("should return 'start' for index 0", () => {
      expect(stopRole(0, 3)).toBe("start");
    });

    it("should return 'end' for last index", () => {
      expect(stopRole(2, 3)).toBe("end");
    });

    it("should return 'middle' for indices zwischen start und end", () => {
      expect(stopRole(1, 3)).toBe("middle");
      expect(stopRole(2, 5)).toBe("middle");
    });

    it("should return 'start' and 'end' für single stop", () => {
      expect(stopRole(0, 1)).toBe("start");
      expect(stopRole(0, 1)).not.toBe("middle");
    });
  });

  describe("roleToMarkerColor", () => {
    it("should return green für start", () => {
      expect(roleToMarkerColor("start")).toBe("#22c55e");
    });

    it("should return red für end", () => {
      expect(roleToMarkerColor("end")).toBe("#ef4444");
    });

    it("should return blue für middle", () => {
      expect(roleToMarkerColor("middle")).toBe("#3b82f6");
    });
  });

  describe("roleToLabel", () => {
    it("should return 'Start' für start", () => {
      expect(roleToLabel("start")).toBe("Start");
    });

    it("should return 'Ziel' für end", () => {
      expect(roleToLabel("end")).toBe("Ziel");
    });

    it("should return 'Zwischenstopp' für middle", () => {
      expect(roleToLabel("middle")).toBe("Zwischenstopp");
    });
  });

  describe("roleToMarkerGlyph", () => {
    it("should return 'A' für start", () => {
      expect(roleToMarkerGlyph("start")).toBe("A");
    });

    it("should return 'B' für end", () => {
      expect(roleToMarkerGlyph("end")).toBe("B");
    });

    it("should return filled circle für middle", () => {
      expect(roleToMarkerGlyph("middle")).toBe("●");
    });
  });

  describe("buildMarkerElement", () => {
    it("should build a div for start role", () => {
      const el = buildMarkerElement("start");
      expect(el instanceof HTMLElement).toBe(true);
      expect(el.textContent).toBe("A");
      expect(el.style.backgroundColor).toBe("rgb(34, 197, 94)");
      expect(el.style.cursor).toBe("grab");
    });

    it("should build a div for end role", () => {
      const el = buildMarkerElement("end");
      expect(el instanceof HTMLElement).toBe(true);
      expect(el.textContent).toBe("B");
      expect(el.style.backgroundColor).toBe("rgb(239, 68, 68)");
    });

    it("should build a div for middle role", () => {
      const el = buildMarkerElement("middle");
      expect(el instanceof HTMLElement).toBe(true);
      expect(el.textContent).toBe("●");
      expect(el.style.backgroundColor).toBe("rgb(59, 130, 246)");
    });
  });

  describe("buildPopupText", () => {
    const startRole: StopRole = "start";
    const middleRole: StopRole = "middle";

    it("should return address wenn vorhanden", () => {
      const stop: Stop = {
        id: "s1",
        address: "Tesla Supercharger Paris",
        position: [48.858844, 2.294351],
      };
      expect(buildPopupText(stop, middleRole)).toBe("Tesla Supercharger Paris");
    });

    it("should return role label + rounded coordinates wenn kein address und position gesetzt", () => {
      const stop: Stop = {
        id: "s2",
        address: "",
        position: [48.858844, 2.294351],
      };
      expect(buildPopupText(stop, startRole)).toBe(
        "Start (48.858844, 2.294351)",
      );
    });

    it("should return role label only wenn kein address und kein position", () => {
      const stop: Stop = {
        id: "s3",
        address: "",
        position: null,
      };
      expect(buildPopupText(stop, startRole)).toBe("Start");
    });
  });

  describe("buildChargingStopMarkerElement", () => {
    it("should build a lightning-bolt marker with charger orange background", () => {
      const el = buildChargingStopMarkerElement();
      expect(el instanceof HTMLElement).toBe(true);
      expect(el.style.backgroundColor).toBe("rgb(245, 158, 11)");
      expect(el.innerHTML).toContain("<svg");
    });
  });

  describe("formatChargingDuration", () => {
    it("should format sub-hour durations as minutes only", () => {
      expect(formatChargingDuration(1800)).toBe("30min");
    });

    it("should format durations over an hour as hours + minutes", () => {
      expect(formatChargingDuration(5400)).toBe("1h 30min");
    });

    it("should round to the nearest minute", () => {
      expect(formatChargingDuration(89)).toBe("1min");
    });
  });

  describe("buildChargingStopPopupHtml", () => {
    const stop: ChargingStop = {
      name: "Tesla Supercharger Hamm",
      position: [51.6806, 7.8206],
      ankunfts_soc_pct: 22,
      ziel_soc_pct: 80,
      ladedauer_s: 1800,
      energie_geladen_kwh: 33.5,
    };

    it("should include the station name", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain(
        "Tesla Supercharger Hamm",
      );
    });

    it("should include arrival and departure SoC", () => {
      const html = buildChargingStopPopupHtml(stop);
      expect(html).toContain("22% SoC");
      expect(html).toContain("80% SoC");
    });

    it("should include formatted charging duration", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain("30min");
    });

    it("should include charged energy in kWh", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain("33.5 kWh");
    });
  });
});
