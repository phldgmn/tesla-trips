import { describe, it, expect, beforeEach } from "vitest";
import { createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react-dom/test-utils";

import {
  usePersistentState,
  migrateWetterBeruecksichtigen,
} from "@/utils/persistent-state";

/** Rendert eine Test-Komponente, die `usePersistentState` verwendet, und
 *  gibt Setter + eine Funktion zum Auslesen des zuletzt gerenderten Werts
 *  zurück. `unmountAndRemount` simuliert einen Browser-Refresh: die
 *  Komponente wird zerstört und (mit demselben `localStorage`-Inhalt) neu
 *  gemountet - Persistenz zeigt sich genau darin, dass der wiederhergestellte
 *  Wert NICHT auf `initialValue` zurückfällt. */
function mountCounter(key: string, initialValue: number) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  let root: Root = createRoot(container);
  let lastValue: number | undefined;
  let setValue!: (v: number) => void;

  function TestComponent() {
    const [value, setter] = usePersistentState(key, initialValue);
    lastValue = value;
    setValue = setter;
    return null;
  }

  function render() {
    root = createRoot(container);
    act(() => {
      root.render(createElement(TestComponent));
    });
  }
  render();

  return {
    getValue: () => lastValue,
    setValue: (v: number) => act(() => setValue(v)),
    unmountAndRemount: () => {
      act(() => {
        root.unmount();
      });
      render();
    },
  };
}

describe("usePersistentState", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("uses the initial value when nothing is stored yet", () => {
    const counter = mountCounter("counter-a", 42);
    expect(counter.getValue()).toBe(42);
  });

  it("survives an unmount/remount (simulated page refresh) after a change", () => {
    const counter = mountCounter("counter-b", 0);
    counter.setValue(7);
    expect(counter.getValue()).toBe(7);

    counter.unmountAndRemount();

    expect(counter.getValue()).toBe(7);
  });

  it("persists the value under a namespaced localStorage key", () => {
    const counter = mountCounter("counter-c", 0);
    counter.setValue(99);

    const raw = window.localStorage.getItem("tesla-trips:v1:counter-c");
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string)).toBe(99);
  });

  it("keeps state for different keys independent", () => {
    const a = mountCounter("counter-d1", 1);
    const b = mountCounter("counter-d2", 2);
    a.setValue(100);

    expect(a.getValue()).toBe(100);
    expect(b.getValue()).toBe(2);
  });

  it("falls back to the initial value when the stored JSON is corrupted", () => {
    window.localStorage.setItem("tesla-trips:v1:counter-e", "{not valid json");
    const counter = mountCounter("counter-e", 5);
    expect(counter.getValue()).toBe(5);
  });
});

describe("migrateWetterBeruecksichtigen", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("maps old true value to 'high' and removes old key", () => {
    window.localStorage.setItem(
      "tesla-trips:v1:wetter-beruecksichtigen",
      "true",
    );
    const result = migrateWetterBeruecksichtigen();
    expect(result).toBe("high");
    expect(
      window.localStorage.getItem("tesla-trips:v1:wetter-beruecksichtigen"),
    ).toBeNull();
  });

  it("maps old false value to 'off' and removes old key", () => {
    window.localStorage.setItem(
      "tesla-trips:v1:wetter-beruecksichtigen",
      "false",
    );
    const result = migrateWetterBeruecksichtigen();
    expect(result).toBe("off");
    expect(
      window.localStorage.getItem("tesla-trips:v1:wetter-beruecksichtigen"),
    ).toBeNull();
  });

  it("returns default 'high' when old key is missing", () => {
    const result = migrateWetterBeruecksichtigen();
    expect(result).toBe("high");
    expect(
      window.localStorage.getItem("tesla-trips:v1:wetter-beruecksichtigen"),
    ).toBeNull();
  });

  it("falls back to 'high' when old key holds corrupted JSON", () => {
    window.localStorage.setItem(
      "tesla-trips:v1:wetter-beruecksichtigen",
      "{not valid json",
    );
    const result = migrateWetterBeruecksichtigen();
    expect(result).toBe("high");
  });

  it("falls back to 'high' when old key holds a non-boolean string", () => {
    window.localStorage.setItem(
      "tesla-trips:v1:wetter-beruecksichtigen",
      '"foo"',
    );
    const result = migrateWetterBeruecksichtigen();
    expect(result).toBe("high");
  });
});
