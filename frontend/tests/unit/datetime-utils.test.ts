import { describe, it, expect } from "vitest";
import {
  formatUhrzeit,
  formatDatumKurz,
  isoDatumSchluessel,
  istTageswechsel,
} from "@/utils/datetime-utils";

describe("formatUhrzeit", () => {
  it("gibt 'unbekannt' bei null zurück", () => {
    expect(formatUhrzeit(null)).toBe("unbekannt");
  });

  it("formatiert nur die Uhrzeit, ohne Datum", () => {
    const result = formatUhrzeit("2026-08-16T19:17:00");
    expect(result).not.toMatch(/2026|08|16\./);
    expect(result).toMatch(/^\d{1,2}:\d{2}$/);
  });

  it("gibt 'Invalid Date' bei nicht parsbarem Wert zurück (kein Wurf, `Date` parst defensiv)", () => {
    expect(formatUhrzeit("kein-datum")).toBe("Invalid Date");
  });
});

describe("formatDatumKurz", () => {
  it("formatiert Wochentag + Tag.Monat, ohne Uhrzeit", () => {
    const result = formatDatumKurz("2026-08-16T19:17:00");
    expect(result).not.toMatch(/19:17/);
    expect(result).toContain("16");
    expect(result).toContain("08");
  });

  it("gibt 'Invalid Date' bei nicht parsbarem Wert zurück (kein Wurf, `Date` parst defensiv)", () => {
    expect(formatDatumKurz("kein-datum")).toBe("Invalid Date");
  });
});

describe("isoDatumSchluessel", () => {
  it("extrahiert YYYY-MM-DD per String-Slicing (keine Zeitzonen-Konvertierung)", () => {
    expect(isoDatumSchluessel("2026-08-16T23:59:00")).toBe("2026-08-16");
    expect(isoDatumSchluessel("2026-08-17T00:00:00")).toBe("2026-08-17");
  });
});

describe("istTageswechsel", () => {
  it("erkennt einen Tageswechsel bei unterschiedlichem Kalendertag", () => {
    expect(istTageswechsel("2026-08-16T23:00:00", "2026-08-17T01:00:00")).toBe(
      true,
    );
  });

  it("erkennt keinen Tageswechsel am selben Kalendertag", () => {
    expect(istTageswechsel("2026-08-16T08:00:00", "2026-08-16T20:00:00")).toBe(
      false,
    );
  });

  it("gibt false zurück, wenn einer der beiden Zeitpunkte unbekannt ist", () => {
    expect(istTageswechsel(null, "2026-08-16T20:00:00")).toBe(false);
    expect(istTageswechsel("2026-08-16T20:00:00", null)).toBe(false);
    expect(istTageswechsel(null, null)).toBe(false);
  });
});
