/** Tests for the responsive sidebar of the interactive export viewer
 *  (`ExportViewer`): hidden by default on small screens, shown on large
 *  ones, toggled by the menu button over the map.
 */
import {
  describe,
  it,
  expect,
  beforeAll,
  afterAll,
  vi,
  type MockInstance,
} from "vitest";
import { createRoot } from "react-dom/client";
import { act } from "react";
import { ExportViewer } from "@/export/viewer/ExportViewer";
import type { TripExportData } from "@/export/export-html";
import type { TripSimulationResult, Stop } from "@/types";

// MapVisualization creates a real MapLibre map on mount; jsdom has no
// WebGL2, so MapLibre throws synchronously (GPUInitializationError). The
// tests only exercise the sidebar/menu-button layout, not the map: mock the
// MapVisualization component with the same DOM surface (map container div).
vi.mock("@/components/Map", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/components/Map")>();
  return {
    ...original,
    MapVisualization: () => <div className="map-container" />,
  };
});

// Minimal trip data: one drive frame, no stops, no charging stops.
const RESULT: TripSimulationResult = {
  frames: [
    {
      timestamp: "2026-09-27T10:00:00Z",
      position: [57.78, 14.1],
      distanceM: 0,
      socPct: 100,
      state: "FAHREN",
      speedKmh: 0,
      temperatureC: null,
      windSpeedMs: null,
      windDirectionDeg: null,
      precipitationMm: null,
    },
  ],
  totalDistanceKm: 0,
  totalDrivingTimeMin: 0,
  totalChargingTimeMin: 0,
  totalWaitingTimeMin: 0,
  startSocPct: 100,
  targetSocPct: 20,
  chargingStops: [],
  waypointStops: [],
  routeGeometry: [[57.78, 14.1]],
  detectedFerries: [],
  totalChargingCost: [],
  chargingStopsMissingPricing: 0,
  constructionZones: [],
};

const DATA: TripExportData = {
  version: 1,
  exportedAt: "2026-09-27T10:00:00Z",
  title: "Test trip",
  stops: [] as Stop[],
  result: RESULT,
};

let matchMediaSpy: MockInstance | undefined;

/** Stubs `window.matchMedia` so `ExportViewer` starts with the wanted
 *  sidebar state (jsdom has no matchMedia and no real viewport). */
function stubMatchMedia(matches: boolean): void {
  matchMediaSpy?.mockRestore();
  if (typeof window.matchMedia !== "function") {
    // jsdom does not implement matchMedia at all: define it, spy on it.
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: () => mediaQuery(matches),
    });
    matchMediaSpy = vi.spyOn(window, "matchMedia");
  } else {
    matchMediaSpy = vi
      .spyOn(window, "matchMedia")
      .mockImplementation(() => mediaQuery(matches));
  }
}

/** Minimal MediaQueryList stub for `window.matchMedia`. */
function mediaQuery(matches: boolean): MediaQueryList {
  return {
    matches,
    media: "",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as MediaQueryList;
}

beforeAll(() => {
  // MapVisualization renders a MapLibre map; jsdom cannot create real WebGL
  // contexts - the map stays in its loading state, which is fine here: the
  // tests only exercise the sidebar/menu-button layout, not the map.
  stubMatchMedia(false);
});

afterAll(() => {
  matchMediaSpy?.mockRestore();
});

/** Renders `ExportViewer` into a fresh container and returns a teardown
 *  function plus the container (for `querySelector`). */
function renderViewer(): { container: HTMLDivElement; unmount: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<ExportViewer data={DATA} />);
  });
  return {
    container,
    unmount: () =>
      act(() => {
        root.unmount();
        container.remove();
      }),
  };
}

/** Clicks the menu button (found by its accessible name) inside `container`. */
function clickMenuButton(container: HTMLDivElement): void {
  const button = Array.from(container.querySelectorAll("button")).find(
    (b) => b.getAttribute("aria-label") !== null,
  );
  if (!button) throw new Error("Menu button not found");
  act(() => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("ExportViewer responsive sidebar", () => {
  it("hides the sidebar on small screens and shows it via the menu button", () => {
    stubMatchMedia(false);
    const { container, unmount } = renderViewer();
    const aside = container.querySelector("aside");
    expect(aside).not.toBeNull();
    expect(aside!.getAttribute("style")).toContain("display: none");

    const menuButton = Array.from(container.querySelectorAll("button")).find(
      (b) => b.getAttribute("aria-label") === "Show overview",
    );
    expect(menuButton).not.toBeUndefined();
    expect(menuButton!.getAttribute("aria-expanded")).toBe("false");

    clickMenuButton(container);
    expect(aside!.getAttribute("style")).toContain("display: block");
    expect(
      Array.from(container.querySelectorAll("button")).find(
        (b) => b.getAttribute("aria-label") === "Hide overview",
      ),
    ).not.toBeUndefined();

    // Re-hide via the same button
    clickMenuButton(container);
    expect(aside!.getAttribute("style")).toContain("display: none");
    unmount();
  });

  it("shows the sidebar by default on large screens", () => {
    stubMatchMedia(true);
    const { container, unmount } = renderViewer();
    const aside = container.querySelector("aside");
    expect(aside!.getAttribute("style")).toContain("display: block");
    unmount();
  });
});
