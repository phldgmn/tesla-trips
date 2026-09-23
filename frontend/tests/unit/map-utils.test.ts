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
  findWaypointStopAt,
  buildStopPopupHtml,
  buildChargingStopMarkerElement,
  buildChargingStopPopupHtml,
  buildChargingStopPopupElement,
  buildSuperchargerPopoverElement,
  formatChargingDuration,
  buildRouteHoverText,
  isValidMapViewState,
  DEFAULT_MAP_VIEW,
} from "@/components/Map";
import type { Stop, StopRole } from "@/components/Map";
import type { ChargingStop, WaypointStop } from "@/types";
import type { RouteSample } from "@/utils/route-line";
import type { SuperchargerStation } from "@/api/chargingApi";
import type { StyleSpecification } from "maplibre-gl";
import { formatTimestamp, formatShortDateTime } from "@/utils/datetime-utils";

describe("MapVisualization utilities", () => {
  describe("socToColor", () => {
    it("returns the exact anchor colors at the palette's SoC breakpoints", () => {
      expect(socToColor(0)).toBe("#ef4444");
      expect(socToColor(5)).toBe("#ef4444");
      expect(socToColor(15)).toBe("#f97316");
      expect(socToColor(25)).toBe("#eab308");
      expect(socToColor(75)).toBe("#22c55e");
      expect(socToColor(100)).toBe("#3b82f6");
    });

    it("clamps out-of-range SoC values to the nearest anchor color", () => {
      expect(socToColor(-10)).toBe("#ef4444");
      expect(socToColor(150)).toBe("#3b82f6");
    });

    it("interpolates continuously between anchors instead of snapping to buckets", () => {
      // Regression: die alte Bucket-Implementierung lieferte fuer den
      // gesamten Bereich (20, 40] dieselbe Farbe (#f97316) - dadurch
      // entstanden auf der Karte flache Farbplateaus mit abrupten
      // Uebergaengen an den Bucket-Grenzen statt eines stufenlosen
      // Verlaufs. Werte innerhalb eines Anker-Intervalls muessen sich
      // daher unterscheiden.
      const a = socToColor(21);
      const b = socToColor(30);
      const c = socToColor(40);
      expect(a).not.toBe(b);
      expect(b).not.toBe(c);
      expect(a).not.toBe(c);
    });

    it("blends RGB channels strictly between the two neighboring anchors", () => {
      // Zwischen rot (0xef,0x44,0x44) und orange (0xf9,0x73,0x16) bei t=0.5
      // (SoC 12.5 liegt in der Mitte des Anker-Intervalls [0, 25]).
      const hex = socToColor(12.5);
      const r = parseInt(hex.slice(1, 3), 16);
      const g = parseInt(hex.slice(3, 5), 16);
      const b = parseInt(hex.slice(5, 7), 16);
      expect(r).toBeGreaterThan(0xef);
      expect(r).toBeLessThan(0xf9);
      expect(g).toBeGreaterThan(0x44);
      expect(g).toBeLessThan(0x73);
      expect(b).toBeLessThan(0x44);
      expect(b).toBeGreaterThan(0x16);
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
        is_24_7: true,
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

    it("should return 'Stop' für middle", () => {
      expect(roleToLabel("middle")).toBe("Stop");
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

    it("should build a div with a pin icon (not a bare dot) for middle role", () => {
      const el = buildMarkerElement("middle");
      expect(el instanceof HTMLElement).toBe(true);
      expect(el.innerHTML).toContain("<svg");
      expect(el.style.backgroundColor).toBe("rgb(59, 130, 246)");
    });

    it("has no inline position or z-index (prevents zoom-drift, see construction-zone-popup.test.ts)", () => {
      const el = buildMarkerElement("middle");
      expect(el.style.position).toBe("");
      expect(el.style.zIndex).toBe("");
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

  describe("findWaypointStopAt", () => {
    const waypoint: WaypointStop = {
      position: [48.858844, 2.294351],
      distanceM: 12345,
      arrivalTime: "2024-05-01T10:00:00Z",
      departureTime: "2024-05-01T10:30:00Z",
      ladeleistungKw: 11,
      arrivalSocPct: 40,
      targetSocPct: 80,
      energyChargedKwh: 8,
    };

    it("finds the waypoint stop at (nearly) the same position", () => {
      expect(findWaypointStopAt([waypoint], [48.858844, 2.294351])).toBe(
        waypoint,
      );
      // Minor floating-point drift (well within the matching tolerance).
      expect(findWaypointStopAt([waypoint], [48.85885, 2.29436])).toBe(
        waypoint,
      );
    });

    it("returns null when no waypoint stop is within tolerance", () => {
      expect(findWaypointStopAt([waypoint], [52.52, 13.405])).toBeNull();
    });

    it("returns null for an empty waypoint stop list", () => {
      expect(findWaypointStopAt([], [48.858844, 2.294351])).toBeNull();
    });
  });

  describe("buildStopPopupHtml", () => {
    const middleRole: StopRole = "middle";
    const stop: Stop = {
      id: "s1",
      address: "Rastplatz Muster",
      position: [48.858844, 2.294351],
    };

    it("shows only the address/title when no waypoint stop matches", () => {
      const html = buildStopPopupHtml(stop, middleRole, null);
      expect(html).toContain("Rastplatz Muster");
      expect(html).not.toContain("<table");
    });

    it("shows arrival/departure details when a waypoint stop matches", () => {
      const waypoint: WaypointStop = {
        position: [48.858844, 2.294351],
        distanceM: 12345,
        arrivalTime: "2024-05-01T10:00:00Z",
        departureTime: "2024-05-01T10:30:00Z",
        ladeleistungKw: null,
        arrivalSocPct: 40,
        targetSocPct: 45,
        energyChargedKwh: 0,
      };
      const html = buildStopPopupHtml(stop, middleRole, waypoint);
      expect(html).toContain("Rastplatz Muster");
      expect(html).toContain("<table");
      expect(html).toContain("40% SoC");
      expect(html).toContain("45% SoC");
      expect(html).not.toContain("Ladeleistung");
    });

    it("includes Ladeleistung/Geladen rows only when charging occurred", () => {
      const waypoint: WaypointStop = {
        position: [48.858844, 2.294351],
        distanceM: 12345,
        arrivalTime: "2024-05-01T10:00:00Z",
        departureTime: "2024-05-01T10:30:00Z",
        ladeleistungKw: 11,
        arrivalSocPct: 40,
        targetSocPct: 80,
        energyChargedKwh: 8,
      };
      const html = buildStopPopupHtml(stop, middleRole, waypoint);
      expect(html).toContain("Ladeleistung");
      expect(html).toContain("11.0 kW");
      expect(html).toContain("Geladen");
      expect(html).toContain("8.0 kWh");
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
      stationId: "hamm-1",
      position: [51.6806, 7.8206],
      arrivalSocPct: 22,
      targetSocPct: 80,
      chargingDurationS: 1800,
      energyChargedKwh: 33.5,
      arrivalTime: "2026-08-15T14:05:00",
      departureTime: "2026-08-15T14:35:00",
      pricePerKwh: 0.4,
      currency: "EUR",
      estimatedCost: 13.4,
      pricingUpdatedUtc: "2026-08-01T00:00:00",
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
      expect(html).toContain(formatTimestamp(stop.arrivalTime));
      expect(html).toContain(formatTimestamp(stop.departureTime));
    });

    it("should include formatted charging duration", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain("30min");
    });

    it("should include charged energy in kWh", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain("33.5 kWh");
    });

    it("should include the formatted estimated cost when pricing is cached", () => {
      expect(buildChargingStopPopupHtml(stop)).toContain("13,40");
    });

    it("should show a dash when no pricing data is cached for the station", () => {
      const unpriced: ChargingStop = {
        ...stop,
        pricePerKwh: null,
        currency: null,
        estimatedCost: null,
        pricingUpdatedUtc: null,
      };
      const rows = buildChargingStopPopupHtml(unpriced);
      expect(rows).toContain("Preis");
      expect(rows).toContain(
        '<td style="padding:2px 4px;text-align:right;">–</td>',
      );
    });
  });

  describe("buildChargingStopPopupElement", () => {
    const pricedStop: ChargingStop = {
      name: "Tesla Supercharger Hamm",
      stationId: "hamm-1",
      position: [51.6806, 7.8206],
      arrivalSocPct: 22,
      targetSocPct: 80,
      chargingDurationS: 1800,
      energyChargedKwh: 33.5,
      arrivalTime: "2026-08-15T14:05:00",
      departureTime: "2026-08-15T14:35:00",
      pricePerKwh: 0.4,
      currency: "EUR",
      estimatedCost: 13.4,
      pricingUpdatedUtc: "2026-08-01T00:00:00",
    };
    const unpricedStop: ChargingStop = {
      ...pricedStop,
      pricePerKwh: null,
      currency: null,
      estimatedCost: null,
      pricingUpdatedUtc: null,
    };

    it("omits the pricing-refresh action when pricing is already cached", () => {
      const el = buildChargingStopPopupElement(
        pricedStop,
        { status: "idle" },
        () => {},
      );
      expect(
        el.querySelector('button[aria-label="Preis von Tesla abrufen"]'),
      ).toBeNull();
    });

    it("shows a pricing-refresh icon button when the stop has no cached pricing", () => {
      const el = buildChargingStopPopupElement(
        unpricedStop,
        { status: "idle" },
        () => {},
      );
      expect(
        el.querySelector('button[aria-label="Preis von Tesla abrufen"]'),
      ).not.toBeNull();
    });

    it("invokes the refresh callback when the pricing button is clicked", () => {
      let called = false;
      const el = buildChargingStopPopupElement(
        unpricedStop,
        { status: "idle" },
        () => {
          called = true;
        },
      );
      const btn = el.querySelector<HTMLButtonElement>(
        'button[aria-label="Preis von Tesla abrufen"]',
      );
      btn?.click();
      expect(called).toBe(true);
    });

    it("renders fetched pricing tiers once loaded", () => {
      const el = buildChargingStopPopupElement(
        unpricedStop,
        {
          status: "loaded",
          pricing: {
            slug: "hamm-1",
            tiers: [
              {
                tier_label: "Charging Fees for Tesla Owner",
                time_label: null,
                currency: "EUR",
                amount: 0.45,
                unit: "kWh",
                idle_fee_text: null,
              },
            ],
            updated_utc: "2026-08-20T10:00:00Z",
          },
        },
        () => {},
      );
      expect(el.textContent).toContain("0.45 EUR/kWh");
    });

    it("renders time-window labels in 24h format", () => {
      const el = buildChargingStopPopupElement(
        unpricedStop,
        {
          status: "loaded",
          pricing: {
            slug: "hamm-1",
            tiers: [
              {
                tier_label: "Charging Fees for Tesla Owner",
                time_label: "12:00 AM - 4:00 PM",
                currency: "SEK",
                amount: 3.9,
                unit: "kWh",
                idle_fee_text: null,
              },
              {
                tier_label: "Charging Fees for Tesla Owner",
                time_label: "4:00 PM - 8:00 PM",
                currency: "SEK",
                amount: 4.1,
                unit: "kWh",
                idle_fee_text: null,
              },
            ],
            updated_utc: "2026-08-20T10:00:00Z",
          },
        },
        () => {},
      );
      expect(el.textContent).toContain("00:00 - 16:00");
      expect(el.textContent).toContain("16:00 - 20:00");
      expect(el.textContent).not.toContain("AM");
      expect(el.textContent).not.toContain("PM");
    });

    it("collapses the 'Other EV' pricing tier by default", () => {
      const el = buildChargingStopPopupElement(
        unpricedStop,
        {
          status: "loaded",
          pricing: {
            slug: "hamm-1",
            tiers: [
              {
                tier_label: "Charging Fees for Tesla Owner",
                time_label: null,
                currency: "EUR",
                amount: 0.45,
                unit: "kWh",
                idle_fee_text: null,
              },
              {
                tier_label: "Charging Fees for Other EV",
                time_label: null,
                currency: "EUR",
                amount: 0.55,
                unit: "kWh",
                idle_fee_text: null,
              },
            ],
            updated_utc: "2026-08-20T10:00:00Z",
          },
        },
        () => {},
      );
      const details = Array.from(el.querySelectorAll("details")).find((d) =>
        d.textContent?.includes("Charging Fees for Other EV"),
      );
      expect(details).toBeDefined();
      expect(details?.open).toBe(false);
      const otherEvHeading = el.querySelector("details summary");
      expect(otherEvHeading?.textContent).toBe("Charging Fees for Other EV");
      // Der Tesla-Owner-Tier bleibt sofort sichtbar (kein <details>).
      expect(el.textContent).toContain("Charging Fees for Tesla Owner");
    });

    it("shows the error message returned by a failed refresh", () => {
      const el = buildChargingStopPopupElement(
        unpricedStop,
        { status: "error", message: "Tesla API nicht erreichbar" },
        () => {},
      );
      expect(el.textContent).toContain("Tesla API nicht erreichbar");
    });
  });

  describe("buildSuperchargerPopoverElement", () => {
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
        is_24_7: true,
        date_opened: "2020-01-01",
        ...overrides,
      };
    }

    it("renders three distinct icon-only action buttons when pricing is available", () => {
      const el = buildSuperchargerPopoverElement(
        station(),
        false,
        () => {},
        { status: "idle" },
        () => {},
      );
      const buttons = Array.from(el.querySelectorAll("button"));
      expect(buttons).toHaveLength(3);
      const labels = buttons.map((b) => b.getAttribute("aria-label"));
      expect(labels).toEqual([
        "Stationsdaten von Tesla aktualisieren",
        "Preise von Tesla abrufen",
        "Stationsdaten und Preise aktualisieren",
      ]);
      // Icon-only: keine sichtbaren Textlabels auf den Buttons selbst.
      for (const btn of buttons) {
        expect(btn.textContent?.trim()).toBe("");
        expect(btn.querySelector("svg")).not.toBeNull();
      }
    });

    it("renders a single info action button when no pricing callback is given", () => {
      const el = buildSuperchargerPopoverElement(station(), false, () => {});
      const buttons = Array.from(el.querySelectorAll("button"));
      expect(buttons).toHaveLength(1);
      expect(buttons[0].getAttribute("aria-label")).toBe(
        "Stationsdaten von Tesla aktualisieren",
      );
    });

    it("invokes both refresh callbacks from the combined action button", () => {
      let infoRefreshed = false;
      let pricingRefreshed = false;
      const el = buildSuperchargerPopoverElement(
        station(),
        false,
        () => {
          infoRefreshed = true;
        },
        { status: "idle" },
        () => {
          pricingRefreshed = true;
        },
      );
      const combinedBtn = el.querySelector<HTMLButtonElement>(
        'button[aria-label="Stationsdaten und Preise aktualisieren"]',
      );
      combinedBtn?.click();
      expect(infoRefreshed).toBe(true);
      expect(pricingRefreshed).toBe(true);
    });

    it("disables the info and combined buttons while a station refresh is in progress", () => {
      const el = buildSuperchargerPopoverElement(
        station(),
        true,
        () => {},
        { status: "idle" },
        () => {},
      );
      expect(
        el.querySelector<HTMLButtonElement>(
          'button[aria-label="Stationsdaten von Tesla aktualisieren"]',
        )?.disabled,
      ).toBe(true);
      expect(
        el.querySelector<HTMLButtonElement>(
          'button[aria-label="Stationsdaten und Preise aktualisieren"]',
        )?.disabled,
      ).toBe(true);
      // Der Preis-Button bleibt unabhaengig aktiv.
      expect(
        el.querySelector<HTMLButtonElement>(
          'button[aria-label="Preise von Tesla abrufen"]',
        )?.disabled,
      ).toBe(false);
    });

    it("disables the pricing and combined buttons while pricing is loading", () => {
      const el = buildSuperchargerPopoverElement(
        station(),
        false,
        () => {},
        { status: "loading" },
        () => {},
      );
      expect(
        el.querySelector<HTMLButtonElement>(
          'button[aria-label="Preise von Tesla abrufen"]',
        )?.disabled,
      ).toBe(true);
      expect(
        el.querySelector<HTMLButtonElement>(
          'button[aria-label="Stationsdaten und Preise aktualisieren"]',
        )?.disabled,
      ).toBe(true);
    });
  });

  describe("buildRouteHoverText", () => {
    const sample: RouteSample = {
      distanzM: 1000,
      timestamp: "2026-08-15T14:05:00",
      socPct: 63.4,
    };

    it("should include the short formatted date/time", () => {
      expect(buildRouteHoverText(sample)).toContain(
        formatShortDateTime(sample.timestamp ?? null),
      );
    });

    it("should not include the long formatted date/time", () => {
      expect(buildRouteHoverText(sample)).not.toContain(
        formatTimestamp(sample.timestamp ?? null),
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

    it("should include the assumed speed when present", () => {
      expect(buildRouteHoverText({ ...sample, speedKmh: 118.6 })).toContain(
        "119 km/h",
      );
    });

    it("should omit speed when not present", () => {
      expect(buildRouteHoverText(sample)).not.toContain("km/h");
    });

    it("should include temperature and wind when weather is assumed", () => {
      const text = buildRouteHoverText({
        ...sample,
        temperatureC: 8.2,
        windSpeedKmh: 14.4,
      });
      expect(text).toContain("8°C");
      expect(text).toContain("Wind 14 km/h");
    });

    it("should include the wind direction as a compass abbreviation", () => {
      const text = buildRouteHoverText({
        ...sample,
        temperatureC: 8.2,
        windSpeedKmh: 14.4,
        windDirectionDeg: 315,
      });
      expect(text).toContain("Wind NW 14 km/h");
    });

    it("should show wind direction even without a wind speed", () => {
      const text = buildRouteHoverText({
        ...sample,
        temperatureC: 8.2,
        windDirectionDeg: 0,
      });
      expect(text).toContain("Wind N");
    });

    it("should include precipitation only when it is present", () => {
      const withRain = buildRouteHoverText({
        ...sample,
        temperatureC: 8.2,
        precipitationMm: 2.5,
      });
      expect(withRain).toContain("2.5 mm/h");

      const withoutRain = buildRouteHoverText({
        ...sample,
        temperatureC: 8.2,
        precipitationMm: 0,
      });
      expect(withoutRain).not.toContain("mm/h");
    });

    it("should omit weather entirely when temperature is not present", () => {
      expect(buildRouteHoverText(sample)).not.toContain("°C");
    });

    it("should render each piece of information on its own line", () => {
      const text = buildRouteHoverText({
        ...sample,
        speedKmh: 119,
        temperatureC: 8,
        windSpeedKmh: 14,
        windDirectionDeg: 315,
        precipitationMm: 2.5,
      });
      expect(text.split("\n")).toEqual([
        formatShortDateTime(sample.timestamp ?? null),
        "63% SoC",
        "119 km/h",
        "8°C",
        "Wind NW 14 km/h",
        "2.5 mm/h Regen",
      ]);
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
