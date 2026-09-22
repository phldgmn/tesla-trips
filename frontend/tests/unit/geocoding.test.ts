import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  formatDisplayAddress,
  searchAddress,
  reverseGeocode,
} from "@/api/geocoding";

// Store original fetch and restore after each test
beforeEach(() => {
  vi.stubGlobal("fetch", fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("formatDisplayAddress", () => {
  it("formats full address with road, house_number, postcode, city, country", () => {
    const entry = {
      address: {
        road: "Hauptstraße",
        house_number: "8",
        postcode: "12345",
        city: "Musterstadt",
        country: "Germany",
      },
    };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Hauptstraße 8, 12345 Musterstadt, Germany");
  });

  it("handles address with road but no house_number", () => {
    const entry = {
      address: {
        road: "Hauptstraße",
        postcode: "12345",
        city: "München",
        country: "Germany",
      },
    };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Hauptstraße, 12345 München, Germany");
  });

  it("handles address with no road (falls back to postcode+city+country)", () => {
    const entry = {
      address: {
        postcode: "12345",
        city: "Musterstadt",
        country: "Germany",
      },
    };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("12345 Musterstadt, Germany");
  });

  it("resolves city via town fallback when city is missing", () => {
    const entry = {
      address: {
        road: "Sommerstraße",
        house_number: "12",
        postcode: "12345",
        town: "Musterstadt",
        country: "Germany",
      },
    };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Sommerstraße 12, 12345 Musterstadt, Germany");
  });

  it("resolves city via village fallback when city and town are missing", () => {
    const entry = {
      address: {
        road: "Bahnhofstraße",
        house_number: "5",
        postcode: "12345",
        village: "Musterdorf",
        country: "Germany",
      },
    };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Bahnhofstraße 5, 12345 Musterdorf, Germany");
  });

  it("resolves city via municipality fallback when city, town, village are missing", () => {
    const entry = {
      address: {
        road: "Dorfstraße",
        house_number: "3",
        postcode: "12345",
        municipality: "Musterkreis",
        country: "Germany",
      },
    };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Dorfstraße 3, 12345 Musterkreis, Germany");
  });

  it("resolves city via county fallback when city, town, village, municipality are missing", () => {
    const entry = {
      address: {
        road: "Schulstraße",
        house_number: "7",
        postcode: "12345",
        county: "Musterlandkreis",
        country: "Germany",
      },
    };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Schulstraße 7, 12345 Musterlandkreis, Germany");
  });

  it("returns fallback when address object is missing", () => {
    const entry = { other: "data" };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Raw fallback");
  });

  it("returns fallback when address is not an object", () => {
    const entry = { address: "not an object" };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Raw fallback");
  });

  it("returns fallback when address object exists but has no usable parts", () => {
    const entry = { address: {} };
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Raw fallback");
  });

  it("returns fallback when entry is null", () => {
    const entry = null;
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Raw fallback");
  });

  it("returns fallback when entry is undefined", () => {
    const entry = undefined as unknown as object;
    const fallback = "Raw fallback";
    const result = formatDisplayAddress(entry, fallback);
    expect(result).toBe("Raw fallback");
  });
});

describe("searchAddress integration", () => {
  it("includes addressdetails=1 in URL and formats label", async () => {
    const mockResponse = [
      {
        lat: "50.1109",
        lon: "8.6821",
        display_name:
          "8, Hauptstraße, Stadtteil X, Musterstadt, Musterlandkreis, Hessen, 12345, Germany",
        address: {
          road: "Hauptstraße",
          house_number: "8",
          postcode: "12345",
          city: "Musterstadt",
          country: "Germany",
        },
      },
    ];

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await searchAddress("Hauptstraße 8");

    // Verify URL contains addressdetails=1
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("addressdetails=1"),
      expect.anything(),
    );

    // Verify label is formatted, not raw display_name
    expect(result).toHaveLength(1);
    expect(result[0].label).toBe("Hauptstraße 8, 12345 Musterstadt, Germany");
    expect(result[0].position).toEqual([50.1109, 8.6821]);
  });

  it("falls back to display_name when address parts are unusable", async () => {
    const mockResponse = [
      {
        lat: "48.1351",
        lon: "11.5820",
        display_name: "Lake Starnberg",
      },
    ];

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await searchAddress("Starnberger See");

    expect(result).toHaveLength(1);
    expect(result[0].label).toBe("Lake Starnberg");
  });
});

describe("reverseGeocode integration", () => {
  it("includes addressdetails=1 in URL and returns formatted address", async () => {
    const mockResponse = {
      lat: "50.1109",
      lon: "8.6821",
      display_name:
        "8, Hauptstraße, Stadtteil X, Musterstadt, Musterlandkreis, Hessen, 12345, Germany",
      address: {
        road: "Hauptstraße",
        house_number: "8",
        postcode: "12345",
        city: "Musterstadt",
        country: "Germany",
      },
    };

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await reverseGeocode([50.1109, 8.6821]);

    // Verify URL contains addressdetails=1
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("addressdetails=1"),
      expect.anything(),
    );

    // Verify result is formatted
    expect(result).toBe("Hauptstraße 8, 12345 Musterstadt, Germany");
  });

  it("returns null when response has no display_name and address parts unusable", async () => {
    const mockResponse = {
      lat: "50.1109",
      lon: "8.6821",
      // No display_name, no usable address parts
    };

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await reverseGeocode([50.1109, 8.6821]);

    expect(result).toBeNull();
  });

  it("returns null when response is not ok", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await reverseGeocode([50.1109, 8.6821]);

    expect(result).toBeNull();
  });

  it("returns null when response json is not an object", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve("not an object"),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await reverseGeocode([50.1109, 8.6821]);

    expect(result).toBeNull();
  });
});
