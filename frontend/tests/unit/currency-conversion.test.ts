import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  convertToEUR,
  convertAllToEUR,
  clearRateCache,
} from "@/utils/currency-conversion";

function mockRatesResponse(rates: Record<string, number>) {
  return {
    ok: true,
    json: async () =>
      Object.entries(rates).map(([quote, rate]) => ({
        date: "2026-08-25",
        base: "EUR",
        quote,
        rate,
      })),
  };
}

describe("currency-conversion", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
  });

  describe("convertToEUR", () => {
    it("returns the amount unchanged for EUR without calling the API", async () => {
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      const result = await convertToEUR(10, "EUR");
      expect(result).toBe(10);
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("converts a DKK amount to EUR using the fetched rate", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        mockRatesResponse({ DKK: 7.46, SEK: 11.2 }) as Response,
      );
      const result = await convertToEUR(74.6, "DKK");
      expect(result).toBeCloseTo(10, 5);
    });

    it("falls back to the original amount when the currency has no known rate", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        mockRatesResponse({ DKK: 7.46 }) as Response,
      );
      vi.spyOn(console, "error").mockImplementation(() => {});
      const result = await convertToEUR(50, "XYZ");
      expect(result).toBe(50);
    });

    it("falls back to the original amount when the API request fails", async () => {
      vi.spyOn(globalThis, "fetch").mockRejectedValue(
        new Error("network down"),
      );
      vi.spyOn(console, "error").mockImplementation(() => {});
      const result = await convertToEUR(50, "DKK");
      expect(result).toBe(50);
    });

    it("reuses cached rates within the TTL instead of issuing a second request", async () => {
      const fetchSpy = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(mockRatesResponse({ DKK: 7.46 }) as Response);
      await convertToEUR(74.6, "DKK");
      await convertToEUR(7.46, "DKK");
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });

    it("fetches fresh rates again after the cache is cleared", async () => {
      const fetchSpy = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(mockRatesResponse({ DKK: 7.46 }) as Response);
      await convertToEUR(74.6, "DKK");
      clearRateCache();
      await convertToEUR(74.6, "DKK");
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
  });

  describe("convertAllToEUR", () => {
    it("sums converted amounts across multiple currencies and reports a per-entry breakdown", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        mockRatesResponse({ DKK: 7.46, SEK: 11.2 }) as Response,
      );
      const { totalEUR, breakdown } = await convertAllToEUR([
        { currency: "EUR", amount: 10 },
        { currency: "DKK", amount: 74.6 },
        { currency: "SEK", amount: 11.2 },
      ]);
      expect(totalEUR).toBeCloseTo(21, 5);
      expect(breakdown).toEqual([
        { currency: "EUR", originalAmount: 10, eurAmount: 10 },
        {
          currency: "DKK",
          originalAmount: 74.6,
          eurAmount: expect.closeTo(10, 5),
        },
        {
          currency: "SEK",
          originalAmount: 11.2,
          eurAmount: expect.closeTo(1, 5),
        },
      ]);
    });

    it("returns a zero total and empty breakdown for an empty input", async () => {
      const { totalEUR, breakdown } = await convertAllToEUR([]);
      expect(totalEUR).toBe(0);
      expect(breakdown).toEqual([]);
    });
  });
});
