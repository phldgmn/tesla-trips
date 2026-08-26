import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
} from "react";
import {
  usePersistentState,
  migrateWetterBeruecksichtigen,
} from "../utils/persistent-state";
import { Modal } from "./Modal";

import type {
  Stop,
  VehicleProfileInput,
  TripRequestPayload,
  FerryExclusion,
  FaehrZeitfenster,
  LadedauerVorgabe,
  WeatherDetailLevel,
} from "../types/trip-request";
import {
  buildTripRequestPayload,
  createEmptyStop,
  validateStops,
  TripRequestBuildError,
} from "../types/trip-request";
import {
  VEHICLE_PROFILE_PRESETS,
  DEFAULT_VEHICLE_PROFILE_PRESET_ID,
} from "../data/vehicleProfiles";
import { ChargingStationPicker } from "./ChargingStationPicker";
import { searchAddress, reverseGeocode } from "../api/geocoding";
import {
  combineDateTimeToIso,
  splitIsoToDateTime,
  formatUhrzeit,
  formatDatumKurz,
  formatTagMonat,
  istTageswechsel,
} from "../utils/datetime-utils";
import type { ChargingStop, FaehrSegment } from "../types";
import { buildRouteEintraege } from "@/utils/route-eintraege";
import type { SimulationFrame } from "../types";
import {
  Flag,
  MapPin,
  Milestone,
  Zap,
  Ship,
  Crosshair,
  BatteryCharging,
  CalendarDays,
  Car,
  X,
  CloudSun,
  Construction,
  Battery,
  type LucideIcon,
} from "lucide-react";

// ============================================================================
// Types
// ============================================================================

export interface TripPlannerFormProps {
  stops: Stop[];
  onStopsChange: (stops: Stop[]) => void;
  pickingStopId: string | null;
  onRequestPick: (stopId: string | null) => void;
  onSubmit: (payload: TripRequestPayload) => void;
  isSubmitting: boolean;
  submitError?: string | null;
  /** Fährverbindungen, die in der zuletzt berechneten Route erkannt wurden
   *  (aus `TripSimulationResult.erkannte_faehren`), zur Anzeige als
   *  "vermeiden"-Checkboxen. `undefined`/leer, solange noch keine Route
   *  berechnet wurde. */
  erkannteFaehren?: FaehrSegment[];
  /** Ladehalte des zuletzt berechneten Ladeplans (aus
   *  `TripSimulationResult.charging_stops`), zur Anzeige mit editierbarer
   *  Ladedauer-Vorgabe. `undefined`/leer, solange noch keine Route berechnet
   *  wurde. */
  chargingStops?: ChargingStop[];
  /** Simulationsframes, zur zeitlichen Einordnung von Stops, Ladehalten und
   *  Fähren (für `buildRouteEintraege`). `undefined`/leer, solange noch keine
   *  Route berechnet wurde. */
  frames?: SimulationFrame[];
}

export interface GeocodingState {
  /** Der Text, FÜR DEN aktuell Suchergebnisse angezeigt werden (leer = geschlossen). */
  query: string;
  /** Aktuelles Dropdown-Ergebnis. */
  suggestions: GeocodeSuggestionDisplay[];
  /** Lade-Indikator. */
  loading: boolean;
}

export interface GeocodeSuggestionDisplay {
  label: string;
  position: [number, number];
}

// ============================================================================
// Pure Helper Functions (exported for unit testing)
// ============================================================================

/** Erzeugt den Rollen-Namen eines Stopps anhand seiner Position im Array. */
export function getStopRole(stops: Stop[], index: number): string {
  if (stops.length === 0) return "";
  if (index === 0) return "Start";
  if (index === stops.length - 1) return "Ziel";
  return `Stop ${index}`;
}

/** Prüft, ob ein Address-String wie ein roher Koordinaten-String aussieht
 *  (fallback-Format, das von reverseGeocode erzeugt wird, z. B. "52.5200, 13.4050"). */
export function isRawCoordinateLabel(address: string): boolean {
  return /^-?\d+\.?\d*,\s*-?\d+\.?\d*$/.test(address.trim());
}

/** Prüft, ob eine Adresse als "nicht aufgelöst" gilt (leer oder roh-Koordinaten-Format). */
export function isUnresolvedAddress(stop: Stop): boolean {
  return (
    !stop.position || stop.address === "" || isRawCoordinateLabel(stop.address)
  );
}

/** Tauscht zwei Elemente im Array an den gegebenen Indizes. */
export function swapStops(stops: Stop[], from: number, to: number): Stop[] {
  if (
    from < 0 ||
    to < 0 ||
    from >= stops.length ||
    to >= stops.length ||
    from === to
  ) {
    return stops;
  }
  const updated = [...stops];
  // Array-Destructuring-Tausch
  const tmp = updated[from];
  updated[from] = updated[to];
  updated[to] = tmp;
  return updated;
}

/** Validiert die Formulardaten (Stops + SoC) und gibt Fehlermeldungen zurück. */
export function validateForm(args: {
  stops: Stop[];
  startSoc: number;
  mindestAnkunftsSocPct: number;
  mindestLadezeitMin: number;
  zielSoc: number;
}): string[] {
  const errors: string[] = [];

  // Stop-spezifische Validierung (aus dem Vertrag)
  errors.push(...validateStops(args.stops));

  // SoC-Validierung
  if (
    typeof args.startSoc !== "number" ||
    isNaN(args.startSoc) ||
    args.startSoc < 0 ||
    args.startSoc > 100
  ) {
    errors.push("Start-SoC muss zwischen 0 und 100 % liegen.");
  }
  if (
    typeof args.zielSoc !== "number" ||
    isNaN(args.zielSoc) ||
    args.zielSoc < 0 ||
    args.zielSoc > 100
  ) {
    errors.push("Ziel-SoC muss zwischen 0 und 100 % liegen.");
  }
  if (
    typeof args.mindestLadezeitMin !== "number" ||
    isNaN(args.mindestLadezeitMin) ||
    args.mindestLadezeitMin < 0 ||
    args.mindestLadezeitMin > 30
  ) {
    errors.push("Min. Ladedauer muss zwischen 0 und 30 Minuten liegen.");
  }
  if (
    typeof args.mindestAnkunftsSocPct !== "number" ||
    isNaN(args.mindestAnkunftsSocPct) ||
    args.mindestAnkunftsSocPct < 0 ||
    args.mindestAnkunftsSocPct > 100
  ) {
    errors.push("Min. SoC an Ladestationen muss zwischen 0 und 100 % liegen.");
  }

  return errors;
}

/** Vergleicht zwei FaehrAusschluss-Einträge auf inhaltliche Gleichheit. */
export function sameFaehrAusschluss(
  a: FerryExclusion,
  b: FerryExclusion,
): boolean {
  return (
    a.name === b.name &&
    a.bbox_sw[0] === b.bbox_sw[0] &&
    a.bbox_sw[1] === b.bbox_sw[1] &&
    a.bbox_no[0] === b.bbox_no[0] &&
    a.bbox_no[1] === b.bbox_no[1]
  );
}

/** Ergänzt oder entfernt eine Fährverbindung aus der Ausschlussliste. */
export function toggleFaehrAusschluss(
  liste: FerryExclusion[],
  faehre: FerryExclusion,
  vermeiden: boolean,
): FerryExclusion[] {
  const bereitsVorhanden = liste.some((f) => sameFaehrAusschluss(f, faehre));
  if (vermeiden) {
    return bereitsVorhanden ? liste : [...liste, faehre];
  }
  return liste.filter((f) => !sameFaehrAusschluss(f, faehre));
}

/** Setzt oder entfernt das Zeitfenster für eine Fährverbindung. `zeitfenster`
 *  wird entfernt, wenn `abfahrt`/`ankunft` beide leer sind. */
export function setFaehrZeitfensterFuer(
  liste: FaehrZeitfenster[],
  eintrag: FerryExclusion,
  abfahrt: string,
  ankunft: string,
): FaehrZeitfenster[] {
  const rest = liste.filter((f) => !sameFaehrAusschluss(f, eintrag));
  if (!abfahrt || !ankunft) return rest;
  return [...rest, { ...eintrag, abfahrt, ankunft }];
}

/** Setzt oder entfernt die Ladedauer-Vorgabe für eine Station. Die Vorgabe
 *  wird entfernt, wenn `ladedauerMin` nicht positiv ist. */
export function setLadedauerVorgabeFuer(
  liste: LadedauerVorgabe[],
  stationId: string,
  ladedauerMin: number,
): LadedauerVorgabe[] {
  const rest = liste.filter((v) => v.station_id !== stationId);
  if (!(ladedauerMin > 0)) return rest;
  return [
    ...rest,
    { station_id: stationId, ladedauer_s: Math.round(ladedauerMin * 60) },
  ];
}

/** Stabiler Identitäts-Schlüssel für eine Fährverbindung (Name + Bounding Box),
 *  zur Indizierung von React-State und -Listen abseits von Array-Index. */
export function faehrKey(eintrag: FerryExclusion): string {
  return `${eintrag.name}|${eintrag.bbox_sw.join(",")}|${eintrag.bbox_no.join(",")}`;
}

/** Icon + Hintergrundfarbe des Timeline-Markers für einen Stopp, abhängig
 *  von dessen Rolle (Start/Zwischenstopp/Ziel). */
export function getStopTimelineIcon(
  stops: Stop[],
  index: number,
): { Icon: LucideIcon; background: string } {
  if (index === 0) return { Icon: Flag, background: "#e0e7ff" };
  if (index === stops.length - 1) {
    return { Icon: MapPin, background: "#fef3c7" };
  }
  return { Icon: Milestone, background: "#f3f4f6" };
}

/** Prüft, ob zwei Zeitpunkte als angezeigte Uhrzeit (HH:mm) unterscheidbar
 *  sind. Unterdrückt ein redundantes zweites Zeit-Badge, wenn Ankunft und
 *  Abfahrt eines Routen-Eintrags auf dieselbe Minute fallen - insbesondere
 *  bei Fähren, deren Zeitpunkt mangels simulierter Fahrt "auf dem Wasser"
 *  nur als einzelner Positions-Cluster geschätzt wird (siehe
 *  `estimatePositionTiming` in `timing-utils.ts`), Ankunft und Abfahrt dort
 *  also identisch sein können. `null` gilt immer als unterscheidbar (der
 *  Aufrufer entscheidet anhand von `null` bereits, ob überhaupt ein Badge
 *  gerendert wird). */
export function unterscheidetSichAlsUhrzeit(
  a: string | null,
  b: string | null,
): boolean {
  if (a === null || b === null) return true;
  return formatUhrzeit(a) !== formatUhrzeit(b);
}

/** Präfix, unter dem alle Tesla-Supercharger-Stationen in `data/superchargers.ts`
 *  benannt sind (z. B. "Tesla Supercharger - Berlin Alexanderplatz"). In der
 *  kompakten Routen-Timeline redundant, da das Zap-Icon des Ladehalts bereits
 *  eindeutig als Ladestopp erkennbar ist. */
const SUPERCHARGER_NAME_PRAEFIX = "Tesla Supercharger - ";

/** Kürzt den Anzeigenamen einer Ladestation um den redundanten
 *  "Tesla Supercharger - "-Präfix (siehe `SUPERCHARGER_NAME_PRAEFIX`). Namen
 *  ohne diesen Präfix (z. B. andere Anbieter) bleiben unverändert. */
export function formatLadestationName(name: string): string {
  return name.startsWith(SUPERCHARGER_NAME_PRAEFIX)
    ? name.slice(SUPERCHARGER_NAME_PRAEFIX.length)
    : name;
}

/** Formatiert eine Fahrsegment-Distanz in km, eine Nachkommastelle,
 *  deutsches Zahlenformat (Komma statt Punkt). */
export function formatFahrsegmentStrecke(distanzKm: number): string {
  return `${distanzKm.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} km`;
}

/** Formatiert eine Fahrsegment-Dauer in Minuten als "Xh Ymin" bzw. "Ymin"
 *  (gleiches Format wie `formatChargingDuration` in `Map.tsx`). */
export function formatFahrsegmentDauer(dauerMin: number): string {
  const gesamtMinuten = Math.round(dauerMin);
  const stunden = Math.floor(gesamtMinuten / 60);
  const minuten = gesamtMinuten % 60;
  return stunden > 0 ? `${stunden}h ${minuten}min` : `${minuten}min`;
}

/** Einheitlicher vertikaler Abstand NACH jedem Timeline-Eintrag (Stopp-/
 *  Ladehalt-/Fähren-Karte, Tagestrenner, Fahrsegment-Zeile) - ausschließlich
 *  über `paddingBottom` (nie `paddingTop`/`margin`), damit der Abstand
 *  zwischen zwei beliebigen aufeinanderfolgenden Einträgen unabhängig von
 *  deren Typ immer exakt gleich groß ist. */
const ZEILEN_ABSTAND = "0.85rem";

/** Ein Eintrag der vertikalen Routen-Timeline: Icon-Marker links (auf der
 *  durchgehenden Linie, analog gängiger "Tracking-Timeline"-Komponenten) +
 *  beliebiger Inhalt (Stopp-/Ladehalt-/Fähren-Karte) rechts. */
function TimelineRow({
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
function Zeitbadge({
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
function Tagestrenner({
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
function FahrsegmentZeile({
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

// ============================================================================
// Component
// ============================================================================

const GEOCODING_DEBOUNCE_MS = 400;
const GEOCODING_MIN_QUERY_LENGTH = 3;

export function TripPlannerForm({
  stops,
  onStopsChange,
  pickingStopId,
  onRequestPick,
  onSubmit,
  isSubmitting,
  submitError,
  erkannteFaehren,
  chargingStops,
  frames,
}: TripPlannerFormProps) {
  // --- Local State (Fahrzeug, SoC, Picker) ---
  const [vehicleProfile, setVehicleProfile] =
    usePersistentState<VehicleProfileInput>(
      "vehicle-profile",
      () =>
        VEHICLE_PROFILE_PRESETS.find(
          (p) => p.id === DEFAULT_VEHICLE_PROFILE_PRESET_ID,
        )?.profile ?? VEHICLE_PROFILE_PRESETS[0].profile,
    );
  const [selectedPresetId, setSelectedPresetId] = usePersistentState<
    string | null
  >("vehicle-profile-preset-id", DEFAULT_VEHICLE_PROFILE_PRESET_ID);
  const [showAdvancedVehicle, setShowAdvancedVehicle] = usePersistentState(
    "show-advanced-vehicle",
    false,
  );
  const [startSoc, setStartSoc] = usePersistentState("start-soc", 80);
  const [mindestAnkunftsSocPct, setMindestAnkunftsSocPct] = usePersistentState(
    "mindest-ankunfts-soc",
    5,
  );
  const [zielSoc, setZielSoc] = usePersistentState("ziel-soc", 20);
  const [mindestLadezeitMin, setMindestLadezeitMin] = usePersistentState(
    "mindest-ladezeit-s",
    10,
  );
  const [alleFaehrenVermeiden, setAlleFaehrenVermeiden] = usePersistentState(
    "alle-faehren-vermeiden",
    false,
  );
  const [wetterDetailgrad, setWetterDetailgrad] =
    usePersistentState<WeatherDetailLevel>("wetter-detailgrad", () =>
      migrateWetterBeruecksichtigen(),
    );
  const [baustellenBeruecksichtigen, setBaustellenBeruecksichtigen] =
    usePersistentState("baustellen-beruecksichtigen", true);
  const [vermiedeneFaehren, setVermiedeneFaehren] = usePersistentState<
    FerryExclusion[]
  >("vermiedene-faehren", []);
  const [faehrZeitfenster, setFaehrZeitfenster] = usePersistentState<
    FaehrZeitfenster[]
  >("faehr-zeitfenster", []);
  const [ladedauerVorgaben, setLadedauerVorgaben] = usePersistentState<
    LadedauerVorgabe[]
  >("ladedauer-vorgaben", []);
  // Haelt den Zwischenstand der Fährfahrplan-Eingabe (Datum + Zeit je Feld
  // separat eingegeben), solange noch nicht beide Felder (Abfahrt UND
  // Ankunft) vollständig ausgefüllt sind - `faehrZeitfenster` speichert
  // absichtlich NUR vollständige Zeitfenster (siehe `setFaehrZeitfensterFuer`),
  // ohne diesen separaten Entwurfs-State würde ein kontrolliertes Eingabefeld
  // nach jedem Tastendruck auf "" zurückspringen, solange das jeweils andere
  // Feld noch leer ist.
  const [faehrZeitfensterDraft, setFaehrZeitfensterDraft] = useState<
    Record<string, { abfahrt: string; ankunft: string }>
  >({});

  // --- "Ignorierte Fähren"- und "Fahrzeug & Ladestand"-Modal-Sichtbarkeit
  //     (bewusst nicht persistent - reiner UI-Zustand) ---
  const [isIgnorierteFaehrenModalOpen, setIsIgnorierteFaehrenModalOpen] =
    useState(false);
  const [isVehicleModalOpen, setIsVehicleModalOpen] = useState(false);
  // Stop-ID, für die aktuell der SoC-Inline-Editor (Start-/Ziel-SoC-Button
  // in der Kopfzeile der Start-/Ziel-Karte) geöffnet ist, oder `null`.
  const [socEditingStopId, setSocEditingStopId] = useState<string | null>(null);

  // --- Geocoding State (per stop) ---
  const [geocodingStates, setGeocodingStates] = useState<
    Record<string, GeocodingState>
  >({});

  // --- Charging Station Picker State ---
  const [pickerTargetId, setPickerTargetId] = useState<string | null>(null);

  // AbortController-Ref für laufende Geocoding-Requests
  const geocodingAbortRef = useRef<AbortController | null>(null);

  // Timeout-Ref für das Debounce des Geocoding-Requests. MUSS bei jedem
  // Tastenanschlag den vorherigen, noch ausstehenden Timeout löschen -
  // andernfalls (siehe Bugfix unten) plant jeder Tastenanschlag einen
  // eigenen, unabhängigen 400ms-Timer, der garantiert feuert, egal wie
  // schnell weitergetippt wird: bei normaler Tippgeschwindigkeit löst das
  // EINEN Nominatim-Request PRO ZEICHEN aus statt nur einen nach Tippende -
  // verletzt die 1-req/s-Nutzungsrichtlinie und führt zu HTTP 429.
  const geocodingTimeoutRef = useRef<number | null>(null);

  // Reverse-Geocoding-Guard: speichert "{lat},{lon}" → true, sobald einmal aufgelöst
  const resolvedPositionsRef = useRef<Set<string>>(new Set());

  // --- Reverse-Geocoding-Effect ---
  // Sobald sich die position eines Stopps ändert und die Adresse noch nicht
  // menschenlesbar ist, lösen wir das rückwärts auf.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    stops.map(async (stop) => {
      if (!stop.position) return;
      const posKey = `${stop.position[0]},${stop.position[1]}`;
      if (resolvedPositionsRef.current.has(posKey)) return;
      if (!isUnresolvedAddress(stop)) return;

      // Markieren, damit wir nicht erneut versuchen
      resolvedPositionsRef.current.add(posKey);

      try {
        const label = await reverseGeocode(stop.position, controller.signal);
        if (cancelled) return;
        if (label) {
          onStopsChange(
            stops.map((s) => (s.id === stop.id ? { ...s, address: label } : s)),
          );
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // abgebrochen, ignorieren
        }
      }
    });

    // Da die Closure über stops und onStopsChange stale werden kann,
    // lassen wir dieses Effect nur feuern, wenn sich die Positions-Keys
    // tatsächlich ändern – siehe dependency: serialisierte Positionsliste.
    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    stops
      .map((s) => (s.position ? `${s.position[0]},${s.position[1]}` : "null"))
      .join("|"),
  ]);

  // --- Geocoding ---

  const handleAddressInput = useCallback(
    (stopId: string, value: string) => {
      // Adresse im Stop setzen und Position zurücksetzen (Bearbeitung invalidiert Geocoding)
      onStopsChange(
        stops.map((s) =>
          s.id === stopId ? { ...s, address: value, position: null } : s,
        ),
      );

      // Vorherigen Debounce-Timer UND laufende Geocoding-Request abbrechen.
      // Ohne `clearTimeout` hier würde jeder Tastenanschlag einen eigenen,
      // unabhängigen Timer planen, der garantiert feuert (siehe Ref-
      // Deklaration oben) - EIN Request pro Zeichen statt einer pro
      // Tippende-Pause.
      if (geocodingTimeoutRef.current !== null) {
        clearTimeout(geocodingTimeoutRef.current);
        geocodingTimeoutRef.current = null;
      }
      if (geocodingAbortRef.current) {
        geocodingAbortRef.current.abort();
      }

      if (value.trim().length < GEOCODING_MIN_QUERY_LENGTH) {
        setGeocodingStates((prev) => ({
          ...prev,
          [stopId]: { query: value, suggestions: [], loading: false },
        }));
        return;
      }

      // Status setzen: Ladezustand anzeigen
      setGeocodingStates((prev) => ({
        ...prev,
        [stopId]: {
          query: value,
          suggestions: prev[stopId]?.suggestions ?? [],
          loading: true,
        },
      }));

      // Debounce
      geocodingTimeoutRef.current = setTimeout(async () => {
        geocodingTimeoutRef.current = null;
        const controller = new AbortController();
        geocodingAbortRef.current = controller;

        try {
          const results = await searchAddress(value, controller.signal);
          setGeocodingStates((prev) => ({
            ...prev,
            [stopId]: {
              query: value,
              suggestions: results.map((r) => ({
                label: r.label,
                position: r.position,
              })),
              loading: false,
            },
          }));
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          // Netzwerkfehler: einfach keine Vorschläge
          setGeocodingStates((prev) => ({
            ...prev,
            [stopId]: { query: value, suggestions: [], loading: false },
          }));
        }
      }, GEOCODING_DEBOUNCE_MS);
    },
    [stops, onStopsChange],
  );

  const handleSuggestionSelect = useCallback(
    (stopId: string, suggestion: GeocodeSuggestionDisplay) => {
      onStopsChange(
        stops.map((s) =>
          s.id === stopId
            ? { ...s, address: suggestion.label, position: suggestion.position }
            : s,
        ),
      );
      setGeocodingStates((prev) => ({
        ...prev,
        [stopId]: { query: suggestion.label, suggestions: [], loading: false },
      }));
    },
    [stops, onStopsChange],
  );

  const handleCloseSuggestions = useCallback((stopId: string) => {
    setGeocodingStates((prev) => ({
      ...prev,
      [stopId]: {
        query: prev[stopId]?.query ?? "",
        suggestions: [],
        loading: false,
      },
    }));
  }, []);

  // --- Handlers ---

  const handleAddStop = () => {
    // Neuen Stopp VOR dem letzten Element einfügen (Ziel bleibt Ziel)
    if (stops.length < 1) return;
    const newStop = createEmptyStop();
    const insertAt = stops.length - 1;
    onStopsChange([
      ...stops.slice(0, insertAt),
      newStop,
      stops[stops.length - 1],
    ]);
  };

  const handleRemoveStop = (stopId: string) => {
    if (stops.length <= 2) return; // Mindestens Start + Ziel
    onStopsChange(stops.filter((s) => s.id !== stopId));
  };

  const handleMoveUp = (index: number) => {
    if (index <= 0) return;
    onStopsChange(swapStops(stops, index, index - 1));
  };

  const handleMoveDown = (index: number) => {
    if (index >= stops.length - 1) return;
    onStopsChange(swapStops(stops, index, index + 1));
  };

  const handleLeaveAtChange = (stopId: string, date: string, time: string) => {
    const iso = combineDateTimeToIso(date, time);
    onStopsChange(
      stops.map((s) => (s.id === stopId ? { ...s, leaveAt: iso } : s)),
    );
  };

  const handleLeaveAtClear = (stopId: string) => {
    onStopsChange(
      stops.map((s) => {
        if (s.id !== stopId) return s;
        const rest = { ...s };
        delete rest.leaveAt;
        return rest;
      }),
    );
  };

  const handleChargingPowerChange = (stopId: string, value: string) => {
    const parsed = value.trim() === "" ? undefined : Number(value);
    onStopsChange(
      stops.map((s) => {
        if (s.id !== stopId) return s;
        if (parsed === undefined || Number.isNaN(parsed)) {
          const rest = { ...s };
          delete rest.chargingPowerKw;
          return rest;
        }
        return { ...s, chargingPowerKw: parsed };
      }),
    );
  };

  const handlePresetChange = (presetId: string) => {
    const preset = VEHICLE_PROFILE_PRESETS.find((p) => p.id === presetId);
    if (preset) {
      setVehicleProfile(preset.profile);
      setSelectedPresetId(presetId);
    }
  };

  const handleVehicleFieldChange = <K extends keyof VehicleProfileInput>(
    field: K,
    value: VehicleProfileInput[K],
  ) => {
    setVehicleProfile((prev) => ({ ...prev, [field]: value }));
    setSelectedPresetId(null);
  };

  const handleChargingStationSelect = (station: {
    position: [number, number];
    label: string;
  }) => {
    if (pickerTargetId) {
      onStopsChange(
        stops.map((s) =>
          s.id === pickerTargetId
            ? { ...s, address: station.label, position: station.position }
            : s,
        ),
      );
      setPickerTargetId(null);
    }
  };

  const handleChargingStationCancel = () => {
    setPickerTargetId(null);
  };

  const buildAndSubmit = (
    alleFaehren: boolean,
    vermiedene: FerryExclusion[],
    zeitfenster: FaehrZeitfenster[],
    ladedauern: LadedauerVorgabe[],
  ) => {
    const errors = validateForm({
      stops,
      startSoc,
      zielSoc,
      mindestAnkunftsSocPct,
      mindestLadezeitMin,
    });

    if (errors.length > 0) {
      return;
    }

    try {
      const payload = buildTripRequestPayload({
        stops,
        fahrzeugprofil: vehicleProfile,
        startSocPct: startSoc,
        zielSocPct: zielSoc,
        mindestLadezeitS: mindestLadezeitMin * 60,
        mindestAnkunftsSocPct,
        praeferenzen: {},
        alleFaehrenVermeiden: alleFaehren,
        vermiedeneFaehren: vermiedene,
        faehrZeitfenster: zeitfenster,
        ladedauerVorgaben: ladedauern,
        wetterDetailgrad,
        baustellenBeruecksichtigen,
      });
      onSubmit(payload);
    } catch (error) {
      if (error instanceof TripRequestBuildError) {
        // Wird als submitError an die Eltern-Komponente übergeben
        // Hier fangen wir es als zusätzliche Inline-Fehlermeldung
        // (die Props-Integration kann das auch über submitError handhaben)
      }
    }
  };

  const handleSubmit = () =>
    buildAndSubmit(
      alleFaehrenVermeiden,
      vermiedeneFaehren,
      faehrZeitfenster,
      ladedauerVorgaben,
    );

  // --- Validation ---

  const validationErrors = validateForm({
    stops,
    startSoc,
    zielSoc,
    mindestLadezeitMin,
    mindestAnkunftsSocPct,
  });

  // --- Route-Liste: chronologisch sortierte Stopps, Ladehalte und
  //     (nicht ignorierte) Fähren, siehe utils/route-eintraege.ts ---
  const routeEintraege = buildRouteEintraege({
    stops,
    frames,
    chargingStops,
    erkannteFaehren,
    vermiedeneFaehren,
  });

  // --- Render Helpers ---

  const renderVehicleAdvanced = () => {
    const v = vehicleProfile;
    return (
      <details
        open={showAdvancedVehicle}
        onToggle={() => setShowAdvancedVehicle(!showAdvancedVehicle)}
        style={{ marginTop: "1rem" }}
      >
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
          Erweitert
        </summary>
        <div style={{ marginTop: "0.75rem", display: "grid", gap: "0.75rem" }}>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Masse (kg)
            </label>
            <input
              type="number"
              step="1"
              value={v.masse_kg}
              onChange={(e) =>
                handleVehicleFieldChange("masse_kg", parseFloat(e.target.value))
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              cW-Wert
            </label>
            <input
              type="number"
              step="0.001"
              value={v.cw_wert}
              onChange={(e) =>
                handleVehicleFieldChange("cw_wert", parseFloat(e.target.value))
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Stirnfläche (m²)
            </label>
            <input
              type="number"
              step="0.01"
              value={v.stirnflaeche_m2}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "stirnflaeche_m2",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Rollwiderstandsbeiwert
            </label>
            <input
              type="number"
              step="0.0001"
              value={v.rollwiderstandsbeiwert}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "rollwiderstandsbeiwert",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Batteriekapazität (kWh)
            </label>
            <input
              type="number"
              step="0.1"
              value={v.batteriekapazitaet_kwh}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "batteriekapazitaet_kwh",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Nebenverbraucher Baseline (kW)
            </label>
            <input
              type="number"
              step="0.01"
              value={v.nebenverbraucher_baseline_kw}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "nebenverbraucher_baseline_kw",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Reifentyp
            </label>
            <select
              value={v.reifentyp}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "reifentyp",
                  e.target.value as VehicleProfileInput["reifentyp"],
                )
              }
              style={{ width: "100%", padding: "0.5rem" }}
            >
              <option value="standard">Standard</option>
              <option value="winter">Winter</option>
              <option value="low_rolling_resistance">
                Low Rolling Resistance
              </option>
              <option value="performance">Performance</option>
            </select>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <input
              type="checkbox"
              checked={v.dachbox}
              onChange={(e) =>
                handleVehicleFieldChange("dachbox", e.target.checked)
              }
              id="dachbox-checkbox"
            />
            <label htmlFor="dachbox-checkbox">Dachbox</label>
          </div>
        </div>
      </details>
    );
  };

  // --- Main Render ---
  return (
    <div
      style={{
        width: "380px",
        maxHeight: "100vh",
        overflowY: "auto",
        padding: "1.5rem",
        background: "#fafafa",
        borderRight: "1px solid #e5e7eb",
        fontFamily: "system-ui, -apple-system, sans-serif",
        fontSize: "0.9rem",
        lineHeight: "1.5",
      }}
    >
      {/* Submit Error Banner */}
      {submitError && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.75rem",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: "4px",
            color: "#991b1b",
            fontSize: "0.85rem",
          }}
        >
          {submitError}
        </div>
      )}

      {/* Validation Errors */}
      {validationErrors.length > 0 && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.75rem",
            background: "#fffbeb",
            border: "1px solid #fde68a",
            borderRadius: "4px",
            color: "#92400e",
            fontSize: "0.85rem",
          }}
        >
          <ul style={{ margin: "0 0 0 1rem", padding: 0 }}>
            {validationErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 1. Fahrzeug & Ladestand */}
      <button
        type="button"
        onClick={() => setIsVehicleModalOpen(true)}
        style={{
          width: "100%",
          padding: "0.75rem",
          marginBottom: "0.75rem",
          background: "white",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <div style={{ fontWeight: 600 }}>Fahrzeug & Ladestand</div>
        <div
          style={{ fontSize: "0.8rem", color: "#6b7280", marginTop: "0.2rem" }}
        >
          {VEHICLE_PROFILE_PRESETS.find((p) => p.id === selectedPresetId)
            ?.label ?? "Benutzerdefiniert"}
        </div>
      </button>

      <Modal
        open={isVehicleModalOpen}
        onClose={() => setIsVehicleModalOpen(false)}
        title="Fahrzeug & Ladestand"
      >
        <select
          value={selectedPresetId ?? ""}
          onChange={(e) => handlePresetChange(e.target.value)}
          style={{ width: "100%", padding: "0.5rem", marginBottom: "0.5rem" }}
        >
          {VEHICLE_PROFILE_PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.label}
            </option>
          ))}
          <option value="">— Benutzerdefiniert —</option>
        </select>

        {renderVehicleAdvanced()}

        <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Min. SoC an Ladestationen (%)
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={mindestAnkunftsSocPct}
              onChange={(e) =>
                setMindestAnkunftsSocPct(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Ermöglicht, den SoC an Ladestationen niedriger sinken zu lassen
              als die allgemeine Sicherheitsreserve, um die schnellere
              Ladeleistung im unteren SoC-Bereich zu nutzen.
            </small>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Min. Ladedauer (min)
            </label>
            <input
              type="number"
              min="0"
              max="30"
              step="1"
              value={mindestLadezeitMin}
              onChange={(e) =>
                setMindestLadezeitMin(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Verhindert unnötig kurze Ladehalte - ein Halt dauert entweder gar
              nicht oder mindestens so lange.
            </small>
          </div>
        </div>
      </Modal>

      {/* 2. Alle Fähren vermeiden + Zugriff auf ignorierte Fähren - in einer
          Zeile, wie die übrigen Buttons/Kacheln formatiert (statt isolierter
          Checkbox + separatem Vollbreite-Button) */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          marginBottom: "1rem",
        }}
      >
        <label
          htmlFor="alle-faehren-vermeiden-checkbox"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            flex: 1,
            minWidth: 0,
            padding: "0.6rem 0.75rem",
            background: alleFaehrenVermeiden ? "#eff6ff" : "white",
            border: `1px solid ${alleFaehrenVermeiden ? "#93c5fd" : "#e5e7eb"}`,
            borderRadius: "6px",
            cursor: isSubmitting ? "not-allowed" : "pointer",
            boxSizing: "border-box",
          }}
        >
          <input
            type="checkbox"
            checked={alleFaehrenVermeiden}
            onChange={(e) => setAlleFaehrenVermeiden(e.target.checked)}
            id="alle-faehren-vermeiden-checkbox"
            disabled={isSubmitting}
          />
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            Alle Fähren vermeiden
          </span>
        </label>
        <button
          type="button"
          onClick={() => setIsIgnorierteFaehrenModalOpen(true)}
          style={{
            flexShrink: 0,
            padding: "0.6rem 0.6rem",
            background: "#f3f4f6",
            border: "1px solid #d1d5db",
            borderRadius: "6px",
            cursor: "pointer",
            fontSize: "0.8rem",
            whiteSpace: "nowrap",
          }}
          title="Ignorierte Fähren verwalten"
        >
          Ignoriert ({vermiedeneFaehren.length})
        </button>
      </div>

      {/* 2b. Wetter/Baustellen einzeln deaktivierbar: umgeht KEINEN Bug, gibt
          dem Nutzer aber die Kontrolle, einen langsamen/ratenlimitierten
          Provider für eine schnellere Berechnung zu überspringen (siehe
          `wetter_detailgrad`/`baustellen_beruecksichtigen` im Backend). */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
          marginBottom: "1rem",
        }}
      >
        <button
          type="button"
          onClick={() => {
            const stufen: WeatherDetailLevel[] = [
              "off",
              "low",
              "medium",
              "high",
            ];
            const idx = stufen.indexOf(wetterDetailgrad);
            setWetterDetailgrad(stufen[(idx + 1) % stufen.length]);
          }}
          disabled={isSubmitting}
          aria-pressed={wetterDetailgrad !== "off"}
          title={
            wetterDetailgrad === "off"
              ? "Wetterdaten werden ignoriert (klicken: Niedrig → Mittel → Hoch → Aus)"
              : "Durchklicken: nächste Stufe (Niedrig → Mittel → Hoch → Aus)"
          }
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.3rem",
            padding: "0.35rem 0.65rem",
            background: wetterDetailgrad !== "off" ? "#eff6ff" : "#f9fafb",
            border: `1px solid ${
              wetterDetailgrad !== "off" ? "#93c5fd" : "#e5e7eb"
            }`,
            borderRadius: "999px",
            cursor: isSubmitting ? "not-allowed" : "pointer",
            fontSize: "0.78rem",
            color: wetterDetailgrad !== "off" ? "#1d4ed8" : "#6b7280",
          }}
        >
          <CloudSun size={13} />
          {wetterDetailgrad === "off" && "Aus"}
          {wetterDetailgrad === "low" && "Niedrig"}
          {wetterDetailgrad === "medium" && "Mittel"}
          {wetterDetailgrad === "high" && "Hoch"}
        </button>
        <button
          type="button"
          onClick={() =>
            setBaustellenBeruecksichtigen(!baustellenBeruecksichtigen)
          }
          disabled={isSubmitting}
          aria-pressed={baustellenBeruecksichtigen}
          title={
            baustellenBeruecksichtigen
              ? "Baustellen werden bei der Berechnung berücksichtigt (klicken zum Deaktivieren)"
              : "Baustellen werden bei der Berechnung ignoriert (klicken zum Aktivieren)"
          }
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.3rem",
            padding: "0.35rem 0.65rem",
            background: baustellenBeruecksichtigen ? "#eff6ff" : "#f9fafb",
            border: `1px solid ${baustellenBeruecksichtigen ? "#93c5fd" : "#e5e7eb"}`,
            borderRadius: "999px",
            cursor: isSubmitting ? "not-allowed" : "pointer",
            fontSize: "0.78rem",
            color: baustellenBeruecksichtigen ? "#1d4ed8" : "#6b7280",
          }}
        >
          <Construction size={13} />
          Baustellen
        </button>
      </div>

      {/* 3. Route (dynamische Stopp-/Ladehalt-/Fähren-Timeline) */}
      <ol
        style={{
          listStyle: "none",
          margin: 0,
          marginBottom: "0.75rem",
          padding: 0,
          marginLeft: "0.9rem",
          borderLeft: "2px solid #e5e7eb",
        }}
      >
        {routeEintraege.map((eintrag, entryIdx) => {
          if (eintrag.art === "Stopp") {
            const { stop, stopIndex: idx } = eintrag;
            const role = getStopRole(stops, idx);
            const isLast = idx === stops.length - 1;
            const geoState = geocodingStates[stop.id];
            const showSuggestions = geoState && geoState.suggestions.length > 0;

            // leaveAt aufsplitten für date/time-Inputs
            const leaveAtParts = stop.leaveAt
              ? splitIsoToDateTime(stop.leaveAt)
              : null;
            const showLeaveAt = !isLast;

            const { Icon, background } = getStopTimelineIcon(stops, idx);

            // Tageswechsel INNERHALB dieses Eintrags (Ankunft und Abfahrt an
            // unterschiedlichen Kalendertagen, z. B. ein Zwischenstopp über
            // Mitternacht) - Abfahrts-Badge bekommt dann zusätzlich das
            // Datum (siehe `TagestrennerEintrag`-Docstring in
            // `route-eintraege.ts`).
            const abfahrtDatumKurz =
              eintrag.timing.departure !== null &&
              istTageswechsel(eintrag.timing.arrival, eintrag.timing.departure)
                ? formatTagMonat(eintrag.timing.departure)
                : undefined;

            return (
              <TimelineRow key={stop.id} icon={Icon} background={background}>
                <div
                  style={{
                    position: "relative",
                    border: "1px solid #e5e7eb",
                    borderRadius: "6px",
                    padding: "0.75rem",
                    background: "white",
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.5rem",
                  }}
                >
                  {eintrag.timing.arrival !== null && (
                    <Zeitbadge
                      kante="oben"
                      iso={eintrag.timing.arrival}
                      socPct={eintrag.timing.arrivalSocPct ?? undefined}
                    />
                  )}
                  {eintrag.timing.departure !== null &&
                    unterscheidetSichAlsUhrzeit(
                      eintrag.timing.arrival,
                      eintrag.timing.departure,
                    ) && (
                      <Zeitbadge
                        kante="unten"
                        iso={eintrag.timing.departure}
                        socPct={eintrag.timing.departureSocPct ?? undefined}
                        datumKurz={abfahrtDatumKurz}
                      />
                    )}
                  {/* Header: Role Badge + Move/Remove */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: "0.5rem",
                    }}
                  >
                    <span
                      style={{
                        padding: "0.15rem 0.5rem",
                        borderRadius: "999px",
                        fontSize: "0.7rem",
                        fontWeight: 600,
                        background:
                          idx === 0
                            ? "#e0e7ff"
                            : isLast
                              ? "#fef3c7"
                              : "#f3f4f6",
                        color:
                          idx === 0
                            ? "#3730a3"
                            : isLast
                              ? "#92400e"
                              : "#4b5563",
                      }}
                    >
                      {role}
                    </span>
                    <div style={{ display: "flex", gap: "0.25rem" }}>
                      {(idx === 0 || isLast) && (
                        <button
                          type="button"
                          onClick={() =>
                            setSocEditingStopId(
                              socEditingStopId === stop.id ? null : stop.id,
                            )
                          }
                          disabled={isSubmitting}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "0.15rem",
                            padding: "0.2rem 0.4rem",
                            fontSize: "0.7rem",
                            fontWeight: 600,
                            background:
                              socEditingStopId === stop.id
                                ? "#7c3aed"
                                : "#f5f3ff",
                            border: `1px solid ${socEditingStopId === stop.id ? "#7c3aed" : "#c4b5fd"}`,
                            borderRadius: "4px",
                            color:
                              socEditingStopId === stop.id
                                ? "white"
                                : "#6d28d9",
                            cursor: isSubmitting ? "not-allowed" : "pointer",
                            opacity: isSubmitting ? 0.5 : 1,
                          }}
                          title={
                            idx === 0
                              ? "Start-SoC festlegen"
                              : "Ziel-SoC festlegen"
                          }
                          aria-label={
                            idx === 0
                              ? "Start-SoC festlegen"
                              : "Ziel-SoC festlegen"
                          }
                        >
                          <Battery size={12} />
                          {idx === 0 ? startSoc : zielSoc}%
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          onRequestPick(
                            pickingStopId === stop.id ? null : stop.id,
                          )
                        }
                        disabled={isSubmitting}
                        style={{
                          display: "flex",
                          padding: "0.2rem",
                          background:
                            pickingStopId === stop.id ? "#2563eb" : "#eff6ff",
                          border: `1px solid ${pickingStopId === stop.id ? "#2563eb" : "#93c5fd"}`,
                          borderRadius: "4px",
                          color:
                            pickingStopId === stop.id ? "white" : "#1d4ed8",
                          cursor: isSubmitting ? "not-allowed" : "pointer",
                          opacity: isSubmitting ? 0.5 : 1,
                        }}
                        title={
                          pickingStopId === stop.id
                            ? "Kartenauswahl abbrechen"
                            : "Auf Karte wählen"
                        }
                        aria-label={
                          pickingStopId === stop.id
                            ? "Kartenauswahl abbrechen"
                            : "Auf Karte wählen"
                        }
                      >
                        {pickingStopId === stop.id ? (
                          <X size={14} />
                        ) : (
                          <Crosshair size={14} />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setPickerTargetId(
                            pickerTargetId === stop.id ? null : stop.id,
                          )
                        }
                        disabled={isSubmitting}
                        style={{
                          display: "flex",
                          padding: "0.2rem",
                          background:
                            pickerTargetId === stop.id ? "#166534" : "#dcfce7",
                          border: `1px solid ${pickerTargetId === stop.id ? "#166534" : "#86efac"}`,
                          borderRadius: "4px",
                          color:
                            pickerTargetId === stop.id ? "white" : "#166534",
                          cursor: isSubmitting ? "not-allowed" : "pointer",
                          opacity: isSubmitting ? 0.5 : 1,
                        }}
                        title={
                          pickerTargetId === stop.id
                            ? "Ladestationsauswahl abbrechen"
                            : "Ladestation statt Adresse wählen"
                        }
                        aria-label={
                          pickerTargetId === stop.id
                            ? "Ladestationsauswahl abbrechen"
                            : "Ladestation statt Adresse wählen"
                        }
                      >
                        {pickerTargetId === stop.id ? (
                          <X size={14} />
                        ) : (
                          <BatteryCharging size={14} />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleMoveUp(idx)}
                        disabled={idx === 0 || isSubmitting}
                        style={{
                          padding: "0.2rem 0.5rem",
                          fontSize: "0.75rem",
                          background: "#f3f4f6",
                          border: "1px solid #d1d5db",
                          borderRadius: "4px",
                          cursor:
                            idx === 0 || isSubmitting
                              ? "not-allowed"
                              : "pointer",
                          opacity: idx === 0 || isSubmitting ? 0.5 : 1,
                        }}
                        aria-label="Nach oben verschieben"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        onClick={() => handleMoveDown(idx)}
                        disabled={isLast || isSubmitting}
                        style={{
                          padding: "0.2rem 0.5rem",
                          fontSize: "0.75rem",
                          background: "#f3f4f6",
                          border: "1px solid #d1d5db",
                          borderRadius: "4px",
                          cursor:
                            isLast || isSubmitting ? "not-allowed" : "pointer",
                          opacity: isLast || isSubmitting ? 0.5 : 1,
                        }}
                        aria-label="Nach unten verschieben"
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        onClick={() => handleRemoveStop(stop.id)}
                        disabled={stops.length <= 2 || isSubmitting}
                        style={{
                          padding: "0.2rem 0.5rem",
                          fontSize: "0.75rem",
                          background: "#fef2f2",
                          border: "1px solid #fecaca",
                          borderRadius: "4px",
                          color: "#991b1b",
                          cursor:
                            stops.length <= 2 || isSubmitting
                              ? "not-allowed"
                              : "pointer",
                          opacity: stops.length <= 2 || isSubmitting ? 0.5 : 1,
                        }}
                        aria-label="Entfernen"
                      >
                        ✕
                      </button>
                    </div>
                  </div>

                  {/* Inline-Editor für Start-/Ziel-SoC, geöffnet über den
                      Prozent-Button in der Kopfzeile. */}
                  {socEditingStopId === stop.id && (idx === 0 || isLast) && (
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "0.4rem",
                      }}
                    >
                      <label style={{ fontSize: "0.8rem", fontWeight: 500 }}>
                        {idx === 0 ? "Start-SoC (%)" : "Ziel-SoC (%)"}
                      </label>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="1"
                        autoFocus
                        value={idx === 0 ? startSoc : zielSoc}
                        onChange={(e) => {
                          const value = parseInt(e.target.value, 10) || 0;
                          if (idx === 0) {
                            setStartSoc(value);
                          } else {
                            setZielSoc(value);
                          }
                        }}
                        onBlur={() => setSocEditingStopId(null)}
                        style={{
                          width: "5rem",
                          padding: "0.3rem",
                          fontSize: "0.85rem",
                        }}
                      />
                    </div>
                  )}

                  {/* Address Input + Geocoding */}
                  <div style={{ position: "relative" }}>
                    <input
                      type="text"
                      placeholder="Adresse eingeben…"
                      value={stop.address}
                      onChange={(e) =>
                        handleAddressInput(stop.id, e.target.value)
                      }
                      onBlur={() => {
                        // Beim Verlassen die Vorschläge erst nach kurzer Verzögerung schließen,
                        // damit der Klick auf einen Vorschlag noch registriert wird
                        setTimeout(() => handleCloseSuggestions(stop.id), 200);
                      }}
                      onFocus={(e) => {
                        // Bei Fokus und genug Text: Suche neu starten
                        if (
                          e.target.value.trim().length >=
                          GEOCODING_MIN_QUERY_LENGTH
                        ) {
                          handleAddressInput(stop.id, e.target.value);
                        }
                      }}
                      disabled={isSubmitting}
                      style={{
                        width: "100%",
                        padding: "0.5rem",
                        border: "1px solid #d1d5db",
                        borderRadius: "4px",
                        fontSize: "0.85rem",
                        boxSizing: "border-box",
                      }}
                    />
                    {geoState?.loading && (
                      <span
                        style={{
                          position: "absolute",
                          right: "0.5rem",
                          top: "0.6rem",
                          fontSize: "0.75rem",
                          color: "#6b7280",
                        }}
                      >
                        Suche…
                      </span>
                    )}
                    {showSuggestions && (
                      <ul
                        style={{
                          position: "absolute",
                          top: "100%",
                          left: 0,
                          right: 0,
                          zIndex: 10,
                          background: "white",
                          border: "1px solid #d1d5db",
                          borderTop: "none",
                          borderRadius: "0 0 4px 4px",
                          listStyle: "none",
                          margin: 0,
                          padding: 0,
                          maxHeight: "200px",
                          overflowY: "auto",
                          boxShadow: "0 2px 6px rgba(0,0,0,0.1)",
                        }}
                      >
                        {geoState.suggestions.map((sugg, sIdx) => (
                          <li
                            key={sIdx}
                            onMouseDown={(e) => {
                              e.preventDefault();
                              handleSuggestionSelect(stop.id, sugg);
                            }}
                            style={{
                              padding: "0.5rem",
                              cursor: "pointer",
                              fontSize: "0.85rem",
                              borderBottom:
                                sIdx < geoState.suggestions.length - 1
                                  ? "1px solid #f3f4f6"
                                  : "none",
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.background = "#f3f4f6";
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.background = "white";
                            }}
                          >
                            {sugg.label}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  {pickingStopId === stop.id && (
                    <p
                      style={{
                        margin: 0,
                        fontSize: "0.8rem",
                        color: "#2563eb",
                      }}
                    >
                      Klicken Sie auf die Karte, um den Punkt zu platzieren.
                    </p>
                  )}

                  {/* Charging Station Picker (inline, nur für dieses Ziel) */}
                  {pickerTargetId === stop.id && (
                    <ChargingStationPicker
                      onSelect={handleChargingStationSelect}
                      onCancel={handleChargingStationCancel}
                    />
                  )}

                  {/* Abfahrt hier (nur für nicht-letzte Stopps) */}
                  {showLeaveAt && (
                    <div>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          marginBottom: "0.25rem",
                        }}
                      >
                        <label
                          style={{
                            fontSize: "0.8rem",
                            fontWeight: 500,
                          }}
                        >
                          Abfahrt hier
                        </label>
                        {stop.leaveAt && (
                          <button
                            type="button"
                            onClick={() => handleLeaveAtClear(stop.id)}
                            style={{
                              display: "flex",
                              padding: "0.1rem 0.3rem",
                              fontSize: "0.8rem",
                              lineHeight: 1,
                              background: "none",
                              border: "none",
                              color: "#991b1b",
                              cursor: "pointer",
                            }}
                            title="Abfahrtszeit löschen"
                            aria-label="Abfahrtszeit löschen"
                          >
                            <X size={14} />
                          </button>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: "0.4rem" }}>
                        <input
                          type="date"
                          value={leaveAtParts?.date ?? ""}
                          onChange={(e) => {
                            const newTime = leaveAtParts?.time ?? "12:00";
                            if (e.target.value) {
                              handleLeaveAtChange(
                                stop.id,
                                e.target.value,
                                newTime,
                              );
                            } else {
                              handleLeaveAtClear(stop.id);
                            }
                          }}
                          style={{
                            flex: 1,
                            padding: "0.4rem",
                            fontSize: "0.85rem",
                          }}
                        />
                        <input
                          type="time"
                          value={leaveAtParts?.time ?? ""}
                          onChange={(e) => {
                            const newDate = leaveAtParts?.date ?? "";
                            if (e.target.value) {
                              handleLeaveAtChange(
                                stop.id,
                                newDate,
                                e.target.value,
                              );
                            } else {
                              handleLeaveAtClear(stop.id);
                            }
                          }}
                          style={{
                            flex: 1,
                            padding: "0.4rem",
                            fontSize: "0.85rem",
                          }}
                        />
                      </div>
                      {idx !== 0 && (
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "0.4rem",
                            marginTop: "0.5rem",
                          }}
                        >
                          <label
                            style={{
                              fontSize: "0.8rem",
                              fontWeight: 500,
                              whiteSpace: "nowrap",
                            }}
                          >
                            Ladeleistung
                          </label>
                          <input
                            type="number"
                            min="0"
                            step="0.1"
                            placeholder="optional"
                            value={stop.chargingPowerKw ?? ""}
                            onChange={(e) =>
                              handleChargingPowerChange(stop.id, e.target.value)
                            }
                            style={{
                              flex: 1,
                              minWidth: 0,
                              padding: "0.4rem",
                              fontSize: "0.85rem",
                            }}
                          />
                          <span
                            style={{
                              fontSize: "0.8rem",
                              color: "#6b7280",
                              whiteSpace: "nowrap",
                            }}
                          >
                            kW
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </TimelineRow>
            );
          }

          if (eintrag.art === "Ladehalt") {
            const stop = eintrag.chargingStop;
            const vorgabe = ladedauerVorgaben.find(
              (v) => v.station_id === stop.station_id,
            );
            const ladedauerMin = Math.round(
              (vorgabe?.ladedauer_s ?? stop.ladedauer_s) / 60,
            );
            const abfahrtDatumKurz = istTageswechsel(
              stop.ankunftszeit,
              stop.abfahrtszeit,
            )
              ? formatTagMonat(stop.abfahrtszeit)
              : undefined;

            return (
              <TimelineRow
                key={stop.station_id}
                icon={Zap}
                background="#dcfce7"
              >
                <div
                  style={{
                    position: "relative",
                    border: "1px solid #e5e7eb",
                    borderRadius: "6px",
                    padding: "0.5rem 0.75rem",
                    background: "white",
                    fontSize: "0.8rem",
                    display: "grid",
                    gap: "0.3rem",
                  }}
                >
                  <Zeitbadge
                    kante="oben"
                    iso={stop.ankunftszeit}
                    socPct={stop.ankunfts_soc_pct}
                  />
                  <Zeitbadge
                    kante="unten"
                    iso={stop.abfahrtszeit}
                    socPct={stop.ziel_soc_pct}
                    datumKurz={abfahrtDatumKurz}
                  />
                  <strong>{formatLadestationName(stop.name)}</strong>
                  <label
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.4rem",
                    }}
                  >
                    Ladedauer (min)
                    <input
                      type="number"
                      min="0"
                      step="5"
                      value={ladedauerMin}
                      disabled={isSubmitting}
                      onChange={(e) => {
                        const next = setLadedauerVorgabeFuer(
                          ladedauerVorgaben,
                          stop.station_id,
                          parseFloat(e.target.value) || 0,
                        );
                        setLadedauerVorgaben(next);
                      }}
                      onBlur={() =>
                        buildAndSubmit(
                          alleFaehrenVermeiden,
                          vermiedeneFaehren,
                          faehrZeitfenster,
                          ladedauerVorgaben,
                        )
                      }
                      style={{ width: "5rem", padding: "0.3rem" }}
                    />
                  </label>
                </div>
              </TimelineRow>
            );
          }

          if (eintrag.art === "Tagestrenner") {
            return (
              <Tagestrenner
                key={`trenner-${entryIdx}`}
                vorherigeIso={eintrag.vonIso}
                aktuelleIso={eintrag.bisIso}
              />
            );
          }

          if (eintrag.art === "Fahrsegment") {
            // Fahrsegment quer über Mitternacht: Strecke/Zeit/beide Daten
            // in EINER Zeile statt zusätzlich einen separaten
            // Tagestrenner zu rendern (siehe `FahrsegmentEintrag`-
            // Docstring in `route-eintraege.ts`).
            const tageswechsel = istTageswechsel(eintrag.vonIso, eintrag.bisIso)
              ? { vonIso: eintrag.vonIso, bisIso: eintrag.bisIso }
              : undefined;
            return (
              <FahrsegmentZeile
                key={`fahrsegment-${entryIdx}`}
                distanzKm={eintrag.distanzKm}
                dauerMin={eintrag.dauerMin}
                tageswechsel={tageswechsel}
              />
            );
          }

          // eintrag.art === "Fähre"
          const faehre = eintrag.faehre;
          const ausschlussEintrag: FerryExclusion = {
            name: faehre.name,
            bbox_sw: faehre.bbox_sw,
            bbox_no: faehre.bbox_no,
          };
          const zeitfensterEintrag = faehrZeitfenster.find((f) =>
            sameFaehrAusschluss(f, ausschlussEintrag),
          );
          const key = faehrKey(ausschlussEintrag);
          const draft = faehrZeitfensterDraft[key] ?? {
            abfahrt: zeitfensterEintrag?.abfahrt ?? "",
            ankunft: zeitfensterEintrag?.ankunft ?? "",
          };
          const abfahrtParts = draft.abfahrt
            ? splitIsoToDateTime(draft.abfahrt)
            : null;
          const ankunftParts = draft.ankunft
            ? splitIsoToDateTime(draft.ankunft)
            : null;

          const handleZeitfensterChange = (
            feld: "abfahrt" | "ankunft",
            date: string,
            time: string,
          ) => {
            const iso = date && time ? combineDateTimeToIso(date, time) : "";
            const nextDraft = {
              abfahrt: feld === "abfahrt" ? iso : draft.abfahrt,
              ankunft: feld === "ankunft" ? iso : draft.ankunft,
            };
            setFaehrZeitfensterDraft((prev) => ({ ...prev, [key]: nextDraft }));
            const next = setFaehrZeitfensterFuer(
              faehrZeitfenster,
              ausschlussEintrag,
              nextDraft.abfahrt,
              nextDraft.ankunft,
            );
            setFaehrZeitfenster(next);
            if (nextDraft.abfahrt && nextDraft.ankunft) {
              buildAndSubmit(
                alleFaehrenVermeiden,
                vermiedeneFaehren,
                next,
                ladedauerVorgaben,
              );
            }
          };

          const handleIgnorieren = () => {
            const next = toggleFaehrAusschluss(
              vermiedeneFaehren,
              ausschlussEintrag,
              true,
            );
            setVermiedeneFaehren(next);
            buildAndSubmit(
              alleFaehrenVermeiden,
              next,
              faehrZeitfenster,
              ladedauerVorgaben,
            );
          };

          const abfahrtDatumKurz =
            eintrag.timing.departure !== null &&
            istTageswechsel(eintrag.timing.arrival, eintrag.timing.departure)
              ? formatTagMonat(eintrag.timing.departure)
              : undefined;

          return (
            <TimelineRow key={key} icon={Ship} background="#dbeafe">
              <div
                style={{
                  position: "relative",
                  border: "1px solid #bfdbfe",
                  borderRadius: "6px",
                  padding: "0.5rem 0.75rem",
                  background: "#eff6ff",
                  display: "grid",
                  gap: "0.4rem",
                }}
              >
                {eintrag.timing.arrival !== null && (
                  <Zeitbadge
                    kante="oben"
                    iso={eintrag.timing.arrival}
                    socPct={eintrag.timing.arrivalSocPct ?? undefined}
                  />
                )}
                {eintrag.timing.departure !== null &&
                  unterscheidetSichAlsUhrzeit(
                    eintrag.timing.arrival,
                    eintrag.timing.departure,
                  ) && (
                    <Zeitbadge
                      kante="unten"
                      iso={eintrag.timing.departure}
                      socPct={eintrag.timing.departureSocPct ?? undefined}
                      datumKurz={abfahrtDatumKurz}
                    />
                  )}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "0.5rem",
                  }}
                >
                  <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    ⛴ {faehre.name} ({(faehre.laenge_m / 1000).toFixed(1)} km)
                  </span>
                  <button
                    type="button"
                    onClick={handleIgnorieren}
                    disabled={isSubmitting}
                    style={{
                      padding: "0.2rem 0.5rem",
                      fontSize: "0.75rem",
                      background: "#fef2f2",
                      border: "1px solid #fecaca",
                      borderRadius: "4px",
                      color: "#991b1b",
                      cursor: isSubmitting ? "not-allowed" : "pointer",
                    }}
                  >
                    Ignorieren
                  </button>
                </div>
                <div
                  style={{
                    display: "grid",
                    gap: "0.25rem",
                    fontSize: "0.8rem",
                  }}
                >
                  <span>Fährfahrplan (optional, für die Planung):</span>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <input
                      type="date"
                      value={abfahrtParts?.date ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleZeitfensterChange(
                          "abfahrt",
                          e.target.value,
                          abfahrtParts?.time ?? "12:00",
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                    <input
                      type="time"
                      value={abfahrtParts?.time ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleZeitfensterChange(
                          "abfahrt",
                          abfahrtParts?.date ?? "",
                          e.target.value,
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                  </div>
                  <span>Ankunft:</span>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <input
                      type="date"
                      value={ankunftParts?.date ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleZeitfensterChange(
                          "ankunft",
                          e.target.value,
                          ankunftParts?.time ?? "12:00",
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                    <input
                      type="time"
                      value={ankunftParts?.time ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleZeitfensterChange(
                          "ankunft",
                          ankunftParts?.date ?? "",
                          e.target.value,
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                  </div>
                </div>
              </div>
            </TimelineRow>
          );
        })}
      </ol>

      {/* Ignorierte Fähren - Modal, ausgelöst über den "Ignoriert (n)"-Button
          oben neben "Alle Fähren vermeiden"; enthält dauerhaft ausgeschlossene
          Fährverbindungen (unabhängig von der aktuell berechneten Route). */}
      <Modal
        open={isIgnorierteFaehrenModalOpen}
        onClose={() => setIsIgnorierteFaehrenModalOpen(false)}
        title="Ignorierte Fähren"
      >
        {vermiedeneFaehren.length === 0 ? (
          <p style={{ margin: 0, color: "#6b7280" }}>
            Keine ignorierten Fähren.
          </p>
        ) : (
          <div style={{ display: "grid", gap: "0.5rem" }}>
            {vermiedeneFaehren.map((eintrag) => {
              const laengeM =
                (erkannteFaehren ?? []).find((f) =>
                  sameFaehrAusschluss(
                    { name: f.name, bbox_sw: f.bbox_sw, bbox_no: f.bbox_no },
                    eintrag,
                  ),
                )?.laenge_m ?? null;
              return (
                <div
                  key={faehrKey(eintrag)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "0.5rem",
                    border: "1px solid #e5e7eb",
                    borderRadius: "6px",
                    padding: "0.5rem 0.75rem",
                  }}
                >
                  <span style={{ fontSize: "0.85rem" }}>
                    {eintrag.name}
                    {laengeM !== null && ` (${(laengeM / 1000).toFixed(1)} km)`}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      const next = toggleFaehrAusschluss(
                        vermiedeneFaehren,
                        eintrag,
                        false,
                      );
                      setVermiedeneFaehren(next);
                      buildAndSubmit(
                        alleFaehrenVermeiden,
                        next,
                        faehrZeitfenster,
                        ladedauerVorgaben,
                      );
                    }}
                    disabled={isSubmitting}
                    style={{
                      padding: "0.2rem 0.5rem",
                      fontSize: "0.75rem",
                      background: "#dcfce7",
                      border: "1px solid #86efac",
                      borderRadius: "4px",
                      color: "#166534",
                      cursor: isSubmitting ? "not-allowed" : "pointer",
                    }}
                  >
                    Wieder zulassen
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </Modal>

      {/* Add Stop Button */}
      <button
        type="button"
        onClick={handleAddStop}
        disabled={isSubmitting || stops.length < 1}
        style={{
          width: "100%",
          padding: "0.5rem",
          marginBottom: "0.75rem",
          background: "#f3f4f6",
          border: "1px solid #d1d5db",
          borderRadius: "4px",
          cursor: isSubmitting || stops.length < 1 ? "not-allowed" : "pointer",
          opacity: isSubmitting || stops.length < 1 ? 0.6 : 1,
        }}
      >
        + Stopp hinzufügen
      </button>

      {/* 4. Submit */}
      <button
        type="button"
        onClick={handleSubmit}
        disabled={isSubmitting || validationErrors.length > 0}
        style={{
          width: "100%",
          padding: "0.75rem",
          fontSize: "1rem",
          fontWeight: 600,
          background:
            validationErrors.length > 0 || isSubmitting ? "#9ca3af" : "#2563eb",
          color: "white",
          border: "none",
          borderRadius: "6px",
          cursor:
            validationErrors.length > 0 || isSubmitting
              ? "not-allowed"
              : "pointer",
        }}
      >
        {isSubmitting ? "Berechne…" : "Route berechnen"}
      </button>
    </div>
  );
}

export default TripPlannerForm;
