import { describe, it, expect } from "vitest";
import {
  formatTime,
  formatShortDate,
  isoDateKey,
  isDayChange,
} from "@/utils/datetime-utils";

describe("formatTime", () => {
  it("gibt '–' bei null zurück", () => {
    expect(formatTime(null)).toBe("–");
  });

  it("formatiert nur die Uhrzeit, ohne Datum", () => {
    const result = formatTime("2026-08-16T19:17:00");
    expect(result).not.toMatch(/2026|08|16\./);
    expect(result).toMatch(/^\d{1,2}:\d{2}$/);
  });

  it("gibt 'Invalid Date' bei nicht parsbarem Wert zurück (kein Wurf, `Date` parst defensiv)", () => {
    expect(formatTime("kein-datum")).toBe("Invalid Date");
  });
});

describe("formatShortDate", () => {
  it("formatiert Wochentag + Tag.Monat, ohne Uhrzeit", () => {
    const result = formatShortDate("2026-08-16T19:17:00");
    expect(result).not.toMatch(/19:17/);
    expect(result).toContain("16");
    expect(result).toContain("08");
  });

  it("gibt 'Invalid Date' bei nicht parsbarem Wert zurück (kein Wurf, `Date` parst defensiv)", () => {
    expect(formatShortDate("kein-datum")).toBe("Invalid Date");
  });
});

describe("isoDateKey", () => {
  it("extrahiert YYYY-MM-DD per String-Slicing (keine Zeitzonen-Konvertierung)", () => {
    expect(isoDateKey("2026-08-16T23:59:00")).toBe("2026-08-16");
    expect(isoDateKey("2026-08-17T00:00:00")).toBe("2026-08-17");
  });
});

describe("isDayChange", () => {
  it("erkennt einen Tageswechsel bei unterschiedlichem Kalendertag", () => {
    expect(isDayChange("2026-08-16T23:00:00", "2026-08-17T01:00:00")).toBe(
      true,
    );
  });

  it("erkennt keinen Tageswechsel am selben Kalendertag", () => {
    expect(isDayChange("2026-08-16T08:00:00", "2026-08-16T20:00:00")).toBe(
      false,
    );
  });

  it("gibt false zurück, wenn einer der beiden Zeitpunkte unbekannt ist", () => {
    expect(isDayChange(null, "2026-08-16T20:00:00")).toBe(false);
    expect(isDayChange("2026-08-16T20:00:00", null)).toBe(false);
    expect(isDayChange(null, null)).toBe(false);
  });
});
