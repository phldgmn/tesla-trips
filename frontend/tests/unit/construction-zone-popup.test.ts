import { describe, it, expect } from "vitest";
import {
  buildConstructionZoneMarkerElement,
  buildChargingStopMarkerElement,
  buildConstructionZonePopupHtml,
} from "@/components/Map";
import type { ConstructionZone } from "@/types";

/** Hilfsfunktion zum Bauen einer test-ConstructionZone. */
function makeZone(events: ConstructionZone["events"]): ConstructionZone {
  return {
    position: [51.0, 7.0],
    events,
  };
}

describe("buildConstructionZonePopupHtml", () => {
  describe("single-event zone", () => {
    it("renders the event label and all fields", () => {
      const zone = makeZone([
        {
          sperrungstyp: "fullyClosed",
          tempolimit_kmh: 60,
          umleitungshinweis: "Umleitung über B56",
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: "2026-08-31T23:59:59",
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Vollsperrung");
      expect(html).toContain("Tempolimit");
      expect(html).toContain("60 km/h");
      expect(html).toContain("Umleitung");
      expect(html).toContain("Umleitung über B56");
      expect(html).toContain("Land");
      expect(html).toContain("DE");
      expect(html).toContain("Gültig ab");
      expect(html).toContain("Gültig bis");
      expect(html).toContain("01.08.2026");
      expect(html).toContain("31.08.2026");
    });

    it("skips null optional fields", () => {
      const zone = makeZone([
        {
          sperrungstyp: "partiallyClosed",
          tempolimit_kmh: null,
          umleitungshinweis: null,
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: null,
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      expect(html).toContain("Teilsperrung");
      expect(html).not.toContain("Tempolimit");
      expect(html).not.toContain("Umleitung");
      expect(html).toContain("Land");
      expect(html).toContain("DE");
      expect(html).toContain("unbestimmt");
    });

    it("falls back to raw sperrungstyp for unknown labels", () => {
      const zone = makeZone([
        {
          sperrungstyp: "unknownType",
          tempolimit_kmh: null,
          umleitungshinweis: null,
          land: "SE",
          gueltig_von: "2026-01-01T00:00:00",
          gueltig_bis: null,
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
          sperrungstyp: "fullyClosed",
          tempolimit_kmh: 60,
          umleitungshinweis: null,
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: "2026-08-31T23:59:59",
        },
        {
          sperrungstyp: "temporarySpeedLimit",
          tempolimit_kmh: 40,
          umleitungshinweis: "Umleitung A5",
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: "2026-08-31T23:59:59",
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
          sperrungstyp: "fullyClosed",
          tempolimit_kmh: null,
          umleitungshinweis: null,
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: null,
        },
        {
          sperrungstyp: "laneClosed",
          tempolimit_kmh: 30,
          umleitungshinweis: "Umleitung B",
          land: "DE",
          gueltig_von: "2026-08-15T00:00:00",
          gueltig_bis: "2026-09-30T23:59:59",
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
          sperrungstyp: "fullyClosed",
          tempolimit_kmh: null,
          umleitungshinweis: null,
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: null,
        },
        {
          sperrungstyp: "partiallyClosed",
          tempolimit_kmh: null,
          umleitungshinweis: null,
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: null,
        },
      ]);
      const html = buildConstructionZonePopupHtml(zone);
      const hrCount = (html.match(/<hr/g) || []).length;
      expect(hrCount).toBe(1); // exactly one separator between two events
    });

    it("has no separator for single event", () => {
      const zone = makeZone([
        {
          sperrungstyp: "fullyClosed",
          tempolimit_kmh: null,
          umleitungshinweis: null,
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: null,
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
          sperrungstyp: "fullyClosed",
          tempolimit_kmh: null,
          umleitungshinweis: null,
          land: "DE",
          gueltig_von: "2026-08-01T00:00:00",
          gueltig_bis: null,
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
