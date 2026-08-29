import { type ReactNode } from "react";
import { CalendarDays, Car, type LucideIcon } from "lucide-react";
import {
  formatFahrsegmentStrecke,
  formatFahrsegmentDauer,
} from "./form-helpers";
import { formatDatumKurz, formatUhrzeit } from "@/utils/datetime-utils";

/** Timeline row sub-components for the route timeline display.
 *  These are pure presentational components used by TripPlannerForm.
 */
const ZEILEN_ABSTAND = "0.85rem";

/** Ein Eintrag der vertikalen Routen-Timeline: Icon-Marker links (auf der
 *  durchgehenden Linie, analog gängiger "Tracking-Timeline"-Komponenten) +
 *  beliebiger Inhalt (Stopp-/Ladehalt-/Fähren-Karte) rechts. */
export function TimelineRow({
  icon: Icon,
  background,
  children,
}: {
  icon: LucideIcon;
  background: string;
  children: ReactNode;
}) {
  return (
    <li
      style={{
        position: "relative",
        marginLeft: "1.5rem",
        paddingBottom: ZEILEN_ABSTAND,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: "-1.9rem",
          top: 0,
          width: "1.8rem",
          height: "1.8rem",
          borderRadius: "9999px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background,
          boxShadow: "0 0 0 4px #fafafa",
          zIndex: 1,
        }}
      >
        <Icon size={15} strokeWidth={2} color="#1f2937" />
      </span>
      {children}
    </li>
  );
}

/** Kleines, auf dem oberen bzw. unteren Kartenrahmen "überhängendes" Badge mit
 *  Uhrzeit (und optional SoC, für Ladehalte) - überlagert die Rahmenlinie
 *  statt zusätzlichen vertikalen Platz im Karten-Layout zu beanspruchen
 *  (`position: absolute`, kein Einfluss auf die Kartenhöhe). Der umgebende
 *  Karten-Container braucht dafür `position: relative`. Wird nur gerendert,
 *  wenn eine Zeit bekannt ist ("wo anwendbar", siehe Aufrufer). `datumKurz`
 *  ergänzt bei Bedarf das Datum (z. B. wenn Ankunft und Abfahrt DESSELBEN
 *  Eintrags auf unterschiedliche Kalendertage fallen, siehe
 *  `istTageswechsel`-Aufrufe in den Aufrufern). */
export function Zeitbadge({
  kante,
  iso,
  socPct,
  datumKurz,
}: {
  kante: "oben" | "unten";
  iso: string;
  socPct?: number;
  datumKurz?: string;
}) {
  const kantenStyle =
    kante === "oben"
      ? { top: 0, left: "0.75rem", transform: "translateY(-50%)" }
      : { bottom: 0, right: "0.75rem", transform: "translateY(50%)" };
  return (
    <span
      style={{
        position: "absolute",
        ...kantenStyle,
        background: "white",
        border: "1px solid #d1d5db",
        borderRadius: "999px",
        padding: "0.05rem 0.45rem",
        fontSize: "0.65rem",
        fontWeight: 600,
        color: "#374151",
        whiteSpace: "nowrap",
        zIndex: 1,
      }}
    >
      {formatUhrzeit(iso)}
      {socPct !== undefined ? ` · ${Math.round(socPct)}%` : ""}
      {datumKurz !== undefined ? ` · ${datumKurz}` : ""}
    </span>
  );
}

/** Kompakter Tageswechsel-Trenner in der Routen-Timeline: dünne Linie mit
 *  den beiden angrenzenden Kalendertagen (vorheriger Tag oben, neuer Tag
 *  unten) in kleiner Schrift - bewusst knapp gehalten, um in der Liste kaum
 *  zusätzlichen vertikalen Platz zu beanspruchen (siehe `istTageswechsel`).
 *  Trägt wie `TimelineRow` einen eigenen Icon-Marker auf der Timeline-Linie,
 *  damit der Tageswechsel dort selbst sofort erkennbar ist statt nur an der
 *  kleinen Schrift. Wird nur zwischen zwei Einträgen OHNE Fahrsegment
 *  dazwischen gerendert (siehe `TagestrennerEintrag`-Docstring in
 *  `route-eintraege.ts` - mit Fahrsegment wird der Tageswechsel stattdessen
 *  in dessen Zeile kombiniert, siehe `FahrsegmentZeile`). */
export function Tagestrenner({
  vorherigeIso,
  aktuelleIso,
}: {
  vorherigeIso: string;
  aktuelleIso: string;
}) {
  return (
    <li
      style={{
        position: "relative",
        listStyle: "none",
        marginLeft: "1.5rem",
        paddingBottom: ZEILEN_ABSTAND,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: "-1.9rem",
          top: 0,
          width: "1.8rem",
          height: "1.8rem",
          borderRadius: "9999px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#e5e7eb",
          boxShadow: "0 0 0 4px #fafafa",
          zIndex: 1,
        }}
      >
        <CalendarDays size={13} strokeWidth={2} color="#4b5563" />
      </span>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "0.1rem",
          minHeight: "1.8rem",
        }}
      >
        <span style={{ fontSize: "0.65rem", lineHeight: 1, color: "#9ca3af" }}>
          {formatDatumKurz(vorherigeIso)}
        </span>
        <div style={{ width: "100%", height: "1px", background: "#e5e7eb" }} />
        <span style={{ fontSize: "0.65rem", lineHeight: 1, color: "#9ca3af" }}>
          {formatDatumKurz(aktuelleIso)}
        </span>
      </div>
    </li>
  );
}

/** Kompakte "Fahrt dazwischen"-Zeile in der Routen-Timeline: gefahrene
 *  Strecke und Zeit zwischen zwei Stopp-/Ladehalt-/Fähren-Karten (siehe
 *  `Fahrsegment` in `route-eintraege.ts`). Bewusst deutlich unauffälliger
 *  als ein "echter" Halt - kein Kartenrahmen, kein farbiger Icon-Kreis
 *  (nur das blasse Icon direkt auf der Timeline-Linie), einzeilig statt
 *  mehrzeilig - repräsentiert schließlich nur die Verbindung dazwischen,
 *  nicht einen eigenen Stopp. Überspannt das Fahrsegment einen Tageswechsel
 *  (`tageswechsel` gesetzt), werden Strecke, Zeit UND beide Kalendertage in
 *  dieser einen Zeile kombiniert, statt zusätzlich einen separaten
 *  `Tagestrenner` zu rendern. */
export function FahrsegmentZeile({
  distanzKm,
  dauerMin,
  tageswechsel,
}: {
  distanzKm: number;
  dauerMin: number;
  tageswechsel?: { vonIso: string; bisIso: string };
}) {
  return (
    <li
      style={{
        position: "relative",
        listStyle: "none",
        marginLeft: "1.5rem",
        paddingBottom: ZEILEN_ABSTAND,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: "-1.40625rem",
          top: "50%",
          transform: "translateY(-50%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1,
        }}
      >
        <Car size={13} strokeWidth={2} color="#b0b5bd" />
      </span>
      <div style={{ lineHeight: 1 }}>
        <span style={{ fontSize: "0.7rem", color: "#9ca3af" }}>
          {formatFahrsegmentStrecke(distanzKm)} ·{" "}
          {formatFahrsegmentDauer(dauerMin)}
          {tageswechsel && (
            <>
              {" · "}
              {formatDatumKurz(tageswechsel.vonIso)} →{" "}
              {formatDatumKurz(tageswechsel.bisIso)}
            </>
          )}
        </span>
      </div>
    </li>
  );
}
