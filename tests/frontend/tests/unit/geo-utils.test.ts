import { describe, it, expect } from "vitest";
import {
  toLngLat,
  haversineDistanceM,
  bearingDeg,
  interpolatePosition,
} from "@/utils/geo-utils";

describe("geo-utils", () => {
  describe("toLngLat", () => {
    it("should convert (lat, lon) to [lng, lat]", () => {
      const result = toLngLat([52.52, 13.4]);
      expect(result).toEqual([13.4, 52.52]);
    });

    it("should handle negative values correctly", () => {
      const result = toLngLat([-33.8688, 151.2093]);
      expect(result).toEqual([151.2093, -33.8688]);
    });

    it("should handle origin correctly", () => {
      const result = toLngLat([0, 0]);
      expect(result).toEqual([0, 0]);
    });
  });

  describe("haversineDistanceM", () => {
    it("should compute distance between Berlin and Hamburg", () => {
      const berlin: [number, number] = [52.52, 13.4];
      const hamburg: [number, number] = [53.5511, 9.9937];

      const distance = haversineDistanceM(berlin, hamburg);

      // Expected ~255 km (~255,000 m)
      expect(distance).toBeGreaterThan(250_000);
      expect(distance).toBeLessThan(260_000);
    });

    it("should be 0 for same point", () => {
      const point: [number, number] = [48.1351, 11.582];
      expect(haversineDistanceM(point, point)).toBeCloseTo(0, 0);
    });
  });

  describe("bearingDeg", () => {
    it("should compute bearing from Berlin to Hamburg (~298 deg NW)", () => {
      const berlin: [number, number] = [52.52, 13.4];
      const hamburg: [number, number] = [53.5511, 9.9937];

      const bearing = bearingDeg(berlin, hamburg);

      expect(bearing).toBeCloseTo(298, -1);
    });

    it("should be ~0 for north", () => {
      const south: [number, number] = [0, 0];
      const north: [number, number] = [1, 0];

      const bearing = bearingDeg(south, north);
      expect(bearing).toBeCloseTo(0, 1);
    });

    it("should be ~90 for east", () => {
      const west: [number, number] = [0, 0];
      const east: [number, number] = [0, 1];

      const bearing = bearingDeg(west, east);
      expect(bearing).toBeCloseTo(90, 1);
    });
  });

  describe("interpolatePosition", () => {
    it("should return start at progress 0", () => {
      const points: [number, number][] = [
        [0, 0],
        [1, 1],
      ];
      const result = interpolatePosition(points, 0);
      expect(result).toEqual([0, 0]);
    });

    it("should return end at progress 1", () => {
      const points: [number, number][] = [
        [0, 0],
        [1, 1],
      ];
      const result = interpolatePosition(points, 1);
      expect(result).toEqual([1, 1]);
    });

    it("should return midpoint at progress 0.5", () => {
      const points: [number, number][] = [
        [0, 0],
        [2, 2],
      ];
      const result = interpolatePosition(points, 0.5);
      expect(result).toEqual([1, 1]);
    });

    it("should return single point when only one exists", () => {
      const points: [number, number][] = [[52.52, 13.4]];
      const result = interpolatePosition(points, 0.5);
      expect(result).toEqual([52.52, 13.4]);
    });

    it("should throw on empty array", () => {
      const points: [number, number][] = [];
      expect(() => interpolatePosition(points, 0.5)).toThrow();
    });

    it("should interpolate correctly across multiple points", () => {
      const points: [number, number][] = [
        [0, 0],
        [1, 1],
        [2, 2],
      ];

      // At progress 0.5 we should be at (1,1)
      const result = interpolatePosition(points, 0.5);
      expect(result).toEqual([1, 1]);
    });
  });
});
