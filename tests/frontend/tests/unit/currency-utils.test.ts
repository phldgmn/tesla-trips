import { describe, it, expect } from "vitest";
import { formatCost, formatCostOrDash } from "@/utils/currency-utils";

describe("formatCost", () => {
  it("formats a EUR amount with German locale currency style", () => {
    expect(formatCost(10, "EUR")).toContain("10,00");
    expect(formatCost(10, "EUR")).toContain("€");
  });

  it("formats a DKK amount", () => {
    expect(formatCost(45.5, "DKK")).toContain("45,50");
  });

  it("formats a SEK amount", () => {
    expect(formatCost(3.79, "SEK")).toContain("3,79");
  });

  it("falls back to a manual format for an unrecognized currency code", () => {
    expect(formatCost(12, "NOTACURRENCY")).toBe("12,00 NOTACURRENCY");
  });
});

describe("formatCostOrDash", () => {
  it("formats a priced amount", () => {
    expect(formatCostOrDash(10, "EUR")).toContain("10,00");
  });

  it("returns a dash when amount is null", () => {
    expect(formatCostOrDash(null, "EUR")).toBe("–");
  });

  it("returns a dash when currency is null", () => {
    expect(formatCostOrDash(10, null)).toBe("–");
  });

  it("returns a dash when both are null", () => {
    expect(formatCostOrDash(null, null)).toBe("–");
  });

  it("returns a dash when amount is undefined (missing field)", () => {
    expect(formatCostOrDash(undefined, "EUR")).toBe("–");
  });

  it("returns a dash when currency is undefined (missing field)", () => {
    expect(formatCostOrDash(10, undefined)).toBe("–");
  });
});
