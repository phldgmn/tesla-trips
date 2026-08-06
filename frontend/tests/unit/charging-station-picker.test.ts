import { describe, it, expect } from "vitest";
import { filterStations } from "@/components/ChargingStationPicker";

const STATIONS = [
  {
    stationId: "a",
    name: "Berlin Alexanderplatz",
    position: [1, 2] as [number, number],
    country: "DE",
    maxLadeleistungKw: 2500,
  },
  {
    stationId: "b",
    name: "Kopenhagen Airport",
    position: [3, 4] as [number, number],
    country: "DK",
    maxLadeleistungKw: 3000,
  },
  {
    stationId: "c",
    name: "Malmö Urban",
    position: [5, 6] as [number, number],
    country: "SE",
    maxLadeleistungKw: 2500,
  },
];

describe("filterStations", () => {
  it("should match by name substring (case-insensitive)", () => {
    const result = filterStations(STATIONS, "berlin");
    expect(result.length).toBe(1);
    expect(result[0].stationId).toBe("a");
  });

  it("should match by country substring (case-insensitive)", () => {
    const result = filterStations(STATIONS, "d");
    expect(result.length).toBe(2);
    expect(new Set(result.map((s) => s.stationId))).toEqual(
      new Set(["a", "b"]),
    );
  });

  it("should return all stations for empty query", () => {
    expect(filterStations(STATIONS, "")).toEqual(STATIONS);
  });

  it("should return all stations for whitespace-only query", () => {
    expect(filterStations(STATIONS, "   ")).toEqual(STATIONS);
  });

  it("should return empty array when no match", () => {
    expect(filterStations(STATIONS, "tokyo")).toEqual([]);
  });
});
