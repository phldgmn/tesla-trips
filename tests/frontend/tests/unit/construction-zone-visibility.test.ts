import { describe, it, expect } from "vitest";
import {
  isConstructionZoneVisibleAtZoom,
  minConstructionZoneLaengeForZoom,
} from "@/components/Map";

describe("minConstructionZoneLaengeForZoom", () => {
  it("requires no minimum length when zoomed in close (city level)", () => {
    expect(minConstructionZoneLaengeForZoom(14)).toBe(0);
    expect(minConstructionZoneLaengeForZoom(11)).toBe(0);
  });

  it("raises the minimum length as zoom decreases", () => {
    const atZoom10 = minConstructionZoneLaengeForZoom(10);
    const atZoom8 = minConstructionZoneLaengeForZoom(8);
    const atZoom6 = minConstructionZoneLaengeForZoom(6);
    const atZoom2 = minConstructionZoneLaengeForZoom(2);
    expect(atZoom10).toBeLessThan(atZoom8);
    expect(atZoom8).toBeLessThan(atZoom6);
    expect(atZoom6).toBeLessThan(atZoom2);
  });

  it("never returns a negative threshold for any realistic zoom", () => {
    for (const zoom of [0, 1, 3, 5, 7, 9, 11, 15, 20]) {
      expect(minConstructionZoneLaengeForZoom(zoom)).toBeGreaterThanOrEqual(0);
    }
  });
});

describe("isConstructionZoneVisibleAtZoom", () => {
  it("always shows zones with unknown length regardless of zoom", () => {
    expect(isConstructionZoneVisibleAtZoom(null, 0)).toBe(true);
    expect(isConstructionZoneVisibleAtZoom(null, 20)).toBe(true);
  });

  it("shows short zones only when zoomed in enough", () => {
    expect(isConstructionZoneVisibleAtZoom(200, 3)).toBe(false);
    expect(isConstructionZoneVisibleAtZoom(200, 12)).toBe(true);
  });

  it("shows very long zones even when zoomed far out", () => {
    expect(isConstructionZoneVisibleAtZoom(20000, 0)).toBe(true);
    expect(isConstructionZoneVisibleAtZoom(20000, 12)).toBe(true);
  });

  it("hides a zone exactly at its threshold boundary minus an epsilon", () => {
    const threshold = minConstructionZoneLaengeForZoom(7);
    expect(isConstructionZoneVisibleAtZoom(threshold, 7)).toBe(true);
    expect(isConstructionZoneVisibleAtZoom(threshold - 1, 7)).toBe(false);
  });
});
