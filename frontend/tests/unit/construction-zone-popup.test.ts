import { describe, it, expect } from "vitest";
import {
  buildConstructionZoneMarkerElement,
  buildChargingStopMarkerElement,
  buildConstructionZonePopupHtml,
} from "@/components/Map";
import type { ConstructionZone } from "@/types";

/** Hilfsfunktion zum Bauen einer test-ConstructionZone. */
function makeZone(
  events: ConstructionZone["events"],
  lengthM: number | null = null,
): ConstructionZone {
  return {
    position: [51.0, 7.0],
    events,
    lengthM,
  };
}

describe("buildConstructionZonePopupHtml", () => {
  describe("single-event zone", () => {
    it("renders the event label and all fields", () => {
      const zone = makeZone([
        {
          closureType: "fullyClosed",
          speedLimitKmh: 60,
          detourNotice: "Umleitung über B56",
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: "2026-08-31T23:59:59",
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Vollsperrung");
      expect(html).toContain("Tempolimit");
      expect(html).toContain("60 km/h");
      expect(html).toContain("Umleitung");
      expect(html).toContain("Umleitung über B56");
      expect(html).toContain("Gültig ab");
      expect(html).toContain("Gültig bis");
      expect(html).toContain("01.08.2026");
      expect(html).toContain("31.08.2026");
    });

    it("skips null optional fields", () => {
      const zone = makeZone([
        {
          closureType: "partiallyClosed",
          speedLimitKmh: null,
          detourNotice: null,
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: null,
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Teilsperrung");
      expect(html).not.toContain("Tempolimit");
      expect(html).not.toContain("Umleitung");
      expect(html).toContain("unbestimmt");
    });

    it("falls back to raw sperrungstyp for unknown labels", () => {
      const zone = makeZone([
        {
          closureType: "unknownType",
          speedLimitKmh: null,
          detourNotice: null,
          country: "SE",
          validFrom: "2026-01-01T00:00:00",
          validTo: null,
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("unknownType");
    });
  });

  describe("multi-event zone (merged)", () => {
    it("renders each event in a separate section with 'N von M' heading", () => {
      const zone = makeZone([
        {
          closureType: "fullyClosed",
          speedLimitKmh: 60,
          detourNotice: null,
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: "2026-08-31T23:59:59",
        },
        {
          closureType: "temporarySpeedLimit",
          speedLimitKmh: 40,
          detourNotice: "Umleitung A5",
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: "2026-08-31T23:59:59",
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Vollsperrung (1 von 2)");
      expect(html).toContain("Tempolimit (2 von 2)");
      expect(html).toContain("<hr");
      // First event has tempolimit but no umleitung
      expect(html).toContain("60 km/h");
      // Second event has umleitung
      expect(html).toContain("Umleitung A5");
    });

    it("skips null fields per event independently", () => {
      const zone = makeZone([
        {
          closureType: "fullyClosed",
          speedLimitKmh: null,
          detourNotice: null,
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: null,
        },
        {
          closureType: "laneClosed",
          speedLimitKmh: 30,
          detourNotice: "Umleitung B",
          country: "DE",
          validFrom: "2026-08-15T00:00:00",
          validTo: "2026-09-30T23:59:59",
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Vollsperrung (1 von 2)");
      expect(html).toContain("Fahrspur gesperrt (2 von 2)");
      // Event 1 has tempolimit=null (skipped), event 2 has 30km/h
      expect(html).toContain("30 km/h");
      expect(html).toContain("Umleitung B");
    });

    it("has exactly one separator between two events", () => {
      const zone = makeZone([
        {
          closureType: "fullyClosed",
          speedLimitKmh: null,
          detourNotice: null,
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: null,
        },
        {
          closureType: "partiallyClosed",
          speedLimitKmh: null,
          detourNotice: null,
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: null,
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      const hrCount = (html.match(/<hr/g) || []).length;
      expect(hrCount).toBe(1); // exactly one separator between two events
    });

    it("has no separator for single event", () => {
      const zone = makeZone([
        {
          closureType: "fullyClosed",
          speedLimitKmh: null,
          detourNotice: null,
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: null,
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      const hrCount = (html.match(/<hr/g) || []).length;
      expect(hrCount).toBe(0);
    });
  });

  describe("structure", () => {
    it("wraps content in the expected outer div", () => {
      const zone = makeZone([
        {
          closureType: "fullyClosed",
          speedLimitKmh: null,
          detourNotice: null,
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: null,
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain(
        'style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px;"',
      );
      expect(html).toContain("<table");
      expect(html).toContain("</table>");
    });
  });

  describe("length field", () => {
    it("renders the length when laenge_m is provided", () => {
      const zone = makeZone(
        [
          {
            closureType: "fullyClosed",
            speedLimitKmh: null,
            detourNotice: null,
            country: "DE",
            validFrom: "2026-08-01T00:00:00",
            validTo: null,
          },
        ],
        2500,
      );
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Länge");
      expect(html).toContain("2,5 km");
    });

    it("renders length in meters when laenge_m < 1000", () => {
      const zone = makeZone(
        [
          {
            closureType: "partiallyClosed",
            speedLimitKmh: null,
            detourNotice: null,
            country: "DE",
            validFrom: "2026-08-01T00:00:00",
            validTo: null,
          },
        ],
        450,
      );
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Länge");
      expect(html).toContain("450 m");
    });

    it("rounds fractional meter lengths to whole meters", () => {
      const zone = makeZone(
        [
          {
            closureType: "partiallyClosed",
            speedLimitKmh: null,
            detourNotice: null,
            country: "DE",
            validFrom: "2026-08-01T00:00:00",
            validTo: null,
          },
        ],
        452.8734,
      );
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("453 m");
      expect(html).not.toContain("452.8734");
      expect(html).not.toContain("452,8734");
    });

    it("rounds fractional km lengths to one decimal", () => {
      const zone = makeZone(
        [
          {
            closureType: "fullyClosed",
            speedLimitKmh: null,
            detourNotice: null,
            country: "DE",
            validFrom: "2026-08-01T00:00:00",
            validTo: null,
          },
        ],
        2537.6,
      );
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("2,5 km");
    });

    it("does not render length when laenge_m is null", () => {
      const zone = makeZone(
        [
          {
            closureType: "fullyClosed",
            speedLimitKmh: null,
            detourNotice: null,
            country: "DE",
            validFrom: "2026-08-01T00:00:00",
            validTo: null,
          },
        ],
        null,
      );
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).not.toContain("Länge");
    });
  });

  describe("marker element styles", () => {
    it("construction zone marker has no inline position or z-index (prevents zoom-drift)", () => {
      const el = buildConstructionZoneMarkerElement();
      expect(el.style.position).toBe("");
      expect(el.style.zIndex).toBe("");
    });

    it("charging stop marker has no inline position or z-index", () => {
      const el = buildChargingStopMarkerElement();
      expect(el.style.position).toBe("");
      expect(el.style.zIndex).toBe("");
    });
  });
});

describe("buildConstructionZonePopupHtml escaping", () => {
  it("escapes third-party free text", () => {
    const html = buildConstructionZonePopupHtml({
      position: [51.0, 7.0],
      lengthM: null,
      events: [
        {
          closureType: "<b>x</b>",
          speedLimitKmh: null,
          detourNotice: "<img src=x onerror=alert(1)>",
          country: "DE",
          validFrom: "2026-08-01T00:00:00",
          validTo: null,
        },
      ],
    } as ConstructionZone);
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
  });
});
