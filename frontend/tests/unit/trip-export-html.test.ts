import { describe, it, expect } from "vitest";
import {
  TRIP_EXPORT_VERSION,
  EXPORT_DATA_ELEMENT_ID,
  WORKER_SOURCE_ELEMENT_ID,
  buildTripExportData,
  buildExportHtml,
  parseTripExportData,
  exportFileName,
} from "@/export/export-html";
import { createEmptyStop } from "@/types/trip-request";
import type { Stop } from "@/types/trip-request";
import type { TripSimulationResult } from "@/types";

/** Minimales TripSimulationResult mit zwei Frames (fuer Titel/Dateiname). */
function makeResult(
  overrides: Partial<TripSimulationResult> = {},
): TripSimulationResult {
  return {
    frames: [
      {
        timestamp: "2026-08-15T08:00:00",
        position: [52.52, 13.405],
        distanceM: 0,
        socPct: 100,
        state: "FAHREN",
        speedKmh: 50,
      },
      {
        timestamp: "2026-08-15T09:00:00",
        position: [53.55, 9.99],
        distanceM: 250_000,
        socPct: 60,
        state: "FAHREN",
        speedKmh: 90,
      },
    ],
    totalDistanceKm: 250,
    totalDrivingTimeMin: 60,
    totalChargingTimeMin: 0,
    totalWaitingTimeMin: 0,
    startSocPct: 100,
    targetSocPct: 60,
    chargingStops: [],
    waypointStops: [],
    detectedFerries: [],
    constructionZones: [],
    routeGeometry: [
      [52.52, 13.405],
      [53.55, 9.99],
    ],
    totalChargingCost: [],
    chargingStopsMissingPricing: 0,
    ...overrides,
  };
}

function makeStops(): Stop[] {
  return [
    { ...createEmptyStop("a"), address: "Berlin Hbf, Europaplatz 1" },
    { ...createEmptyStop("b") },
  ];
}

describe("buildTripExportData", () => {
  it("builds the title from start/destination labels", () => {
    const stops = makeStops();
    stops[1].position = [53.55, 9.99];
    const data = buildTripExportData(makeResult(), stops, new Date(0));
    expect(data.version).toBe(TRIP_EXPORT_VERSION);
    expect(data.title).toBe("Berlin Hbf → 53.5500, 9.9900");
  });

  it("falls back to role labels for stops without address/position", () => {
    const data = buildTripExportData(
      makeResult(),
      [createEmptyStop("a"), createEmptyStop("b")],
      new Date(0),
    );
    expect(data.title).toBe("Start → Ziel");
  });
});

describe("buildExportHtml round trip", () => {
  it("produces a self-contained document whose data parses back unchanged", () => {
    const stops = makeStops();
    stops[0].address = "</script><script>alert(1)</script>, Berlin";
    const data = buildTripExportData(makeResult(), stops, new Date(0));
    const html = buildExportHtml(data, {
      viewerJs: 'const s = "</script>"; // <!-- tricky',
      viewerCss: '.x { content: "</style>"; }',
      workerJs: 'self.postMessage("</script><!--");',
    });

    const doc = new DOMParser().parseFromString(html, "text/html");
    // Exactly three script elements (data, worker source, viewer); the
    // escaped </script> sequences inside them must not terminate any.
    const scripts = doc.querySelectorAll("script");
    expect(scripts).toHaveLength(3);
    expect(doc.getElementById(EXPORT_DATA_ELEMENT_ID)).not.toBeNull();
    expect(doc.getElementById(WORKER_SOURCE_ELEMENT_ID)).not.toBeNull();
    // The <style> escape must keep the viewer CSS inside its element.
    expect(doc.querySelectorAll("style")).toHaveLength(2);
    // Title is HTML-escaped, so the parser sees the raw string.
    expect(doc.title).toBe(data.title);

    expect(
      parseTripExportData(
        doc.getElementById(EXPORT_DATA_ELEMENT_ID)!.textContent,
      ),
    ).toEqual(data);
  });
});

describe("parseTripExportData", () => {
  it("returns null for missing or non-JSON input", () => {
    expect(parseTripExportData(null)).toBeNull();
    expect(parseTripExportData(undefined)).toBeNull();
    expect(parseTripExportData("")).toBeNull();
    expect(parseTripExportData("not json")).toBeNull();
  });

  it("returns null for a foreign version", () => {
    const data = buildTripExportData(makeResult(), makeStops(), new Date(0));
    expect(
      parseTripExportData(JSON.stringify({ ...data, version: 2 })),
    ).toBeNull();
  });

  it("returns null for a structurally broken payload", () => {
    const data = buildTripExportData(makeResult(), makeStops(), new Date(0));
    expect(
      parseTripExportData(JSON.stringify({ ...data, result: null })),
    ).toBeNull();
    expect(
      parseTripExportData(JSON.stringify({ ...data, result: { frames: "x" } })),
    ).toBeNull();
    expect(
      parseTripExportData(JSON.stringify({ ...data, stops: "x" })),
    ).toBeNull();
    expect(parseTripExportData(JSON.stringify("a string"))).toBeNull();
  });
});

describe("exportFileName", () => {
  it("uses the departure date of the first frame", () => {
    const data = buildTripExportData(makeResult(), makeStops(), new Date(0));
    expect(exportFileName(data)).toBe("tesla-trip-2026-08-15.html");
  });

  it("falls back to the export date without frames", () => {
    const data = buildTripExportData(
      makeResult({ frames: [] }),
      makeStops(),
      new Date("2026-09-27T12:00:00Z"),
    );
    expect(exportFileName(data)).toBe("tesla-trip-2026-09-27.html");
  });
});
