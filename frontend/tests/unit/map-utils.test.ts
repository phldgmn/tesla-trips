import { describe, it, expect } from "vitest";
import {
  socToColor,
  buildSocGradientExpression,
  buildSuperchargerGeoJson,
  buildBasemapStyle,
  stopRole,
  roleToMarkerColor,
  roleToLabel,
  roleToMarkerGlyph,
  buildMarkerElement,
  buildPopupText,
  buildChargingStopMarkerElement,
  buildChargingStopPopupHtml,
  formatChargingDuration,
  findNearestFrame,
  buildRouteHoverText,
} from "@/components/Map";
import type { Stop, StopRole } from "@/components/Map";
import type { ChargingStop, SimulationFrame } from "@/types";
import type { SuperchargerStation } from "@/api/chargingApi";
import type { StyleSpecification } from "maplibre-gl";
import { formatZeitpunkt } from "@/utils/datetime-utils";

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

  describe("buildSocGradientExpression", () => {
    function frame(
      position: [number, number],
      soc_pct: number,
    ): SimulationFrame {
      return {
        zeitpunkt: "2026-01-01T00:00:00Z",
        position,
        soc_pct,
        zustand: "FAHREN",
        geschwindigkeit_kmh: 100,
      };
    }

    it("returns a flat fallback expression for an empty frame list", () => {
      expect(buildSocGradientExpression([])).toEqual([
        "interpolate",
        ["linear"],
        ["line-progress"],
        0,
        "#3b82f6",
        1,
        "#3b82f6",
      ]);
    });

    it("builds strictly increasing line-progress stops from start (0) to end (1)", () => {
      const frames = [
        frame([52.5, 13.4], 100),
        frame([52.6, 13.5], 60),
        frame([52.7, 13.6], 20),
      ];
      const expr = buildSocGradientExpression(frames);
      expect(expr[0]).toBe("interpolate");
      expect(expr[1]).toEqual(["linear"]);
      expect(expr[2]).toEqual(["line-progress"]);
      const stops = expr.slice(3);
      expect(stops[0]).toBe(0);
      expect(stops[stops.length - 2]).toBe(1);
      // Farben folgen dem SoC-Verlauf (100% -> gruen, 20% -> rot)
      expect(stops[1]).toBe(socToColor(100));
      expect(stops[stops.length - 1]).toBe(socToColor(20));
      // Progress-Werte muessen strikt aufsteigend sein (MapLibre-Anforderung
      // an `interpolate`-Stops).
      const progressValues = stops.filter((_, i) => i % 2 === 0) as number[];
      for (let i = 1; i < progressValues.length; i++) {
        expect(progressValues[i]).toBeGreaterThan(progressValues[i - 1]);
      }
    });

    it("dedupes frames at an identical position (e.g. a charging pause)", () => {
      const frames = [
        frame([52.5, 13.4], 80),
        frame([52.6, 13.5], 50),
        frame([52.6, 13.5], 50), // Ladehalt: identische Position
        frame([52.6, 13.5], 80), // Ladehalt: identische Position
        frame([52.7, 13.6], 80),
      ];
      const expr = buildSocGradientExpression(frames);
      const stops = expr.slice(3);
      const progressValues = stops.filter((_, i) => i % 2 === 0) as number[];
      // Duplikate mit gleichem Progress-Wert wurden entfernt (sonst waere
      // ["interpolate", ...] fuer MapLibre ungueltig).
      expect(new Set(progressValues).size).toBe(progressValues.length);
    });

    it("downsamples long frame lists to at most maxStops points", () => {
      const frames = Array.from({ length: 5000 }, (_, i) =>
        frame([50 + i * 0.001, 10 + i * 0.001], 100 - (i / 5000) * 100),
      );
      const expr = buildSocGradientExpression(frames, 32);
      const stops = expr.slice(3);
      expect(stops.length / 2).toBeLessThanOrEqual(32);
    });
  });

  describe("buildSuperchargerGeoJson", () => {
    function station(
      overrides: Partial<SuperchargerStation> = {},
    ): SuperchargerStation {
      return {
        slug: "berlin-mitte",
        name: "Berlin Mitte",
        latitude: 52.52,
        longitude: 13.405,
        country: "DE",
        total_stalls: 8,
        power_kilowatt: 250,
        status: "OPEN",
        stalls_v2: 0,
        stalls_v3: 8,
        stalls_v3_ultra: 0,
        stalls_v4: 0,
        ist_24_7: true,
        date_opened: "2020-01-01",
        ...overrides,
      };
    }

    it("builds a Point FeatureCollection with slug in properties", () => {
      const geojson = buildSuperchargerGeoJson([
        station({ slug: "a", longitude: 13.4, latitude: 52.5 }),
        station({ slug: "b", longitude: 9.99, latitude: 53.55 }),
      ]);
      expect(geojson.type).toBe("FeatureCollection");
      expect(geojson.features).toHaveLength(2);
      expect(geojson.features[0].geometry).toEqual({
        type: "Point",
        coordinates: [13.4, 52.5],
      });
      expect(geojson.features[0].properties).toEqual({ slug: "a" });
      expect(geojson.features[1].properties).toEqual({ slug: "b" });
    });

    it("returns an empty FeatureCollection for no stations", () => {
      expect(buildSuperchargerGeoJson([])).toEqual({
        type: "FeatureCollection",
        features: [],
      });
    });
  });

  describe("buildBasemapStyle", () => {
    it("replaces only the openmaptiles source url, keeping everything else", () => {
      const baseStyle = {
        version: 8,
        sources: {
          openmaptiles: {
            type: "vector",
            url: "https://tiles.openfreemap.org/planet",
          },
          ne2_shaded: { type: "raster", tiles: ["https://x/{z}/{x}/{y}"] },
        },
        sprite: "https://tiles.openfreemap.org/sprites/ofm_f384/ofm",
        glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
        layers: [],
      } as unknown as StyleSpecification;

      const result = buildBasemapStyle(baseStyle, "http://localhost:8081");

      expect(result.sources.openmaptiles).toMatchObject({
        url: "http://localhost:8081/basemap.json",
      });
      expect(result.sources.ne2_shaded).toEqual(baseStyle.sources.ne2_shaded);
      expect(result.sprite).toBe(baseStyle.sprite);
      expect(result.glyphs).toBe(baseStyle.glyphs);
      expect(result.layers).toBe(baseStyle.layers);
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
      station_id: "hamm-1",
      position: [51.6806, 7.8206],
      ankunfts_soc_pct: 22,
      ziel_soc_pct: 80,
      ladedauer_s: 1800,
      energie_geladen_kwh: 33.5,
      ankunftszeit: "2026-08-15T14:05:00",
      abfahrtszeit: "2026-08-15T14:35:00",
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

    it("should include arrival and departure time", () => {
      const html = buildChargingStopPopupHtml(stop);
      expect(html).toContain(formatZeitpunkt(stop.ankunftszeit));
      expect(html).toContain(formatZeitpunkt(stop.abfahrtszeit));
    });

    it("should include formatted charging duration", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain("30min");
    });

    it("should include charged energy in kWh", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain("33.5 kWh");
    });
  });

  describe("findNearestFrame", () => {
    const frames: SimulationFrame[] = [
      {
        zeitpunkt: "2026-08-15T10:00:00",
        position: [52.5, 13.4],
        soc_pct: 90,
        zustand: "FAHREN",
        geschwindigkeit_kmh: 100,
      },
      {
        zeitpunkt: "2026-08-15T11:00:00",
        position: [53.0, 13.9],
        soc_pct: 70,
        zustand: "FAHREN",
        geschwindigkeit_kmh: 100,
      },
      {
        zeitpunkt: "2026-08-15T12:00:00",
        position: [53.5, 14.4],
        soc_pct: 50,
        zustand: "FAHREN",
        geschwindigkeit_kmh: 100,
      },
    ];

    it("should return undefined for an empty frame list", () => {
      expect(findNearestFrame([], [13.4, 52.5])).toBeUndefined();
    });

    it("should return the frame closest to the given lng/lat", () => {
      // Nahe am zweiten Frame (53.0, 13.9)
      const nearest = findNearestFrame(frames, [13.91, 53.01]);
      expect(nearest).toBe(frames[1]);
    });

    it("should return the first frame when closest to its position", () => {
      const nearest = findNearestFrame(frames, [13.4, 52.5]);
      expect(nearest).toBe(frames[0]);
    });

    it("should return the last frame when closest to its position", () => {
      const nearest = findNearestFrame(frames, [14.4, 53.5]);
      expect(nearest).toBe(frames[2]);
    });
  });

  describe("buildRouteHoverText", () => {
    const frame: SimulationFrame = {
      zeitpunkt: "2026-08-15T14:05:00",
      position: [52.5, 13.4],
      soc_pct: 63.4,
      zustand: "FAHREN",
      geschwindigkeit_kmh: 110,
    };

    it("should include the formatted date/time", () => {
      expect(buildRouteHoverText(frame)).toContain(
        formatZeitpunkt(frame.zeitpunkt),
      );
    });

    it("should include the rounded SoC percentage", () => {
      expect(buildRouteHoverText(frame)).toContain("63% SoC");
    });
  });
});
