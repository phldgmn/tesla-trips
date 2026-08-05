import { describe, it, expect } from "vitest";
import { socToColor, segmentAvgSoc } from "@/components/Map";

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
});
