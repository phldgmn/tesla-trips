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
  buildRouteHoverText,
  isValidMapViewState,
  DEFAULT_MAP_VIEW,
} from "@/components/Map";
import type { Stop, StopRole } from "@/components/Map";
import type { ChargingStop } from "@/types";
import type { RouteSample } from "@/utils/route-line";
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
    function sample(
      distanzM: number,
      socPct: number,
      critical = false,
    ): RouteSample {
      return critical ? { distanzM, socPct, critical } : { distanzM, socPct };
    }

    it("returns a flat fallback expression for an empty sample list", () => {
      expect(buildSocGradientExpression([], 1000)).toEqual([
        "interpolate",
        ["linear"],
        ["line-progress"],
        0,
        "#3b82f6",
        1,
        "#3b82f6",
      ]);
    });

    it("returns a flat fallback expression for a zero/negative total distance", () => {
      const samples = [sample(0, 80)];
      expect(buildSocGradientExpression(samples, 0)).toEqual([
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
      const samples = [sample(0, 100), sample(5000, 60), sample(10000, 20)];
      const expr = buildSocGradientExpression(samples, 10000);
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

    it("positions stops by distanzM against the given totalDistanceM", () => {
      const samples = [sample(0, 90), sample(9000, 50)];
      const expr = buildSocGradientExpression(samples, 10000);
      const stops = expr.slice(3);
      expect(stops[0]).toBe(0);
      expect(stops[2]).toBe(0.9);
    });

    it("dedupes samples at an identical distanzM (e.g. a charging pause)", () => {
      const samples = [
        sample(0, 80),
        sample(5000, 50),
        sample(5000, 50), // Ladehalt: identische Distanz
        sample(5000, 80), // Ladehalt: identische Distanz
        sample(10000, 80),
      ];
      const expr = buildSocGradientExpression(samples, 10000);
      const stops = expr.slice(3);
      const progressValues = stops.filter((_, i) => i % 2 === 0) as number[];
      // Duplikate mit gleichem Progress-Wert wurden entfernt (sonst waere
      // ["interpolate", ...] fuer MapLibre ungueltig).
      expect(new Set(progressValues).size).toBe(progressValues.length);
    });

    it("never downsamples away critical (charging arrival/departure) samples", () => {
      // Viele reguläre Fahr-Stützpunkte um zwei nah beieinanderliegende
      // kritische Ladehalt-Stützpunkte herum - der SoC-Sprung an der
      // Ladestation muss trotz `maxStops`-Downsampling sichtbar bleiben.
      const regular = Array.from({ length: 1000 }, (_, i) =>
        sample(i * 10, 70),
      );
      const samples = [
        ...regular,
        sample(5005, 15, true),
        sample(5006, 85, true),
      ];
      const expr = buildSocGradientExpression(samples, 9990, 16);
      const stops = expr.slice(3);
      const colors = stops.filter((_, i) => i % 2 === 1) as string[];
      expect(colors).toContain(socToColor(15));
      expect(colors).toContain(socToColor(85));
    });

    it("downsamples long sample lists to at most maxStops points", () => {
      const samples = Array.from({ length: 5000 }, (_, i) =>
        sample(i * 10, 100 - (i / 5000) * 100),
      );
      const expr = buildSocGradientExpression(samples, 4999 * 10, 32);
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

  describe("buildRouteHoverText", () => {
    const sample: RouteSample = {
      distanzM: 1000,
      zeitpunkt: "2026-08-15T14:05:00",
      socPct: 63.4,
    };

    it("should include the formatted date/time", () => {
      expect(buildRouteHoverText(sample)).toContain(
        formatZeitpunkt(sample.zeitpunkt ?? null),
      );
    });

    it("should include the rounded SoC percentage", () => {
      expect(buildRouteHoverText(sample)).toContain("63% SoC");
    });

    it("should render 'unbekannt' when the sample has no timestamp", () => {
      expect(buildRouteHoverText({ distanzM: 0, socPct: 50 })).toContain(
        "unbekannt",
      );
    });
  });

  describe("isValidMapViewState", () => {
    it("accepts a well-formed state", () => {
      expect(isValidMapViewState({ center: [13.4, 52.5], zoom: 9 })).toBe(true);
    });

    it("accepts the default state", () => {
      expect(isValidMapViewState(DEFAULT_MAP_VIEW)).toBe(true);
    });

    it("rejects null/undefined/non-object values", () => {
      expect(isValidMapViewState(null)).toBe(false);
      expect(isValidMapViewState(undefined)).toBe(false);
      expect(isValidMapViewState("not an object")).toBe(false);
      expect(isValidMapViewState(42)).toBe(false);
    });

    it("rejects a center that is not a 2-tuple of finite numbers", () => {
      expect(isValidMapViewState({ center: [13.4], zoom: 9 })).toBe(false);
      expect(isValidMapViewState({ center: [13.4, 52.5, 1], zoom: 9 })).toBe(
        false,
      );
      expect(isValidMapViewState({ center: [13.4, "52.5"], zoom: 9 })).toBe(
        false,
      );
      expect(isValidMapViewState({ center: [13.4, Infinity], zoom: 9 })).toBe(
        false,
      );
      expect(isValidMapViewState({ center: [13.4, NaN], zoom: 9 })).toBe(false);
    });

    it("rejects a non-finite or missing zoom", () => {
      expect(isValidMapViewState({ center: [13.4, 52.5] })).toBe(false);
      expect(isValidMapViewState({ center: [13.4, 52.5], zoom: "9" })).toBe(
        false,
      );
      expect(isValidMapViewState({ center: [13.4, 52.5], zoom: NaN })).toBe(
        false,
      );
    });
  });
});
