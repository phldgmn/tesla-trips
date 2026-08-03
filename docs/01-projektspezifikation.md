# Projektspezifikation: Personalisierter Tesla-Tripplaner

## Zielsetzung

Entwicklung eines hochgradig personalisierten Reiseplaners für ein Tesla Model 3. Der Fokus liegt nicht auf schneller Standardnavigation, sondern auf einer realistischen Simulation des Energieverbrauchs und einer optimalen Ladeplanung.

Im Gegensatz zu bestehenden Lösungen (z. B. ABRP) soll das System:

- den individuellen Fahrstil berücksichtigen,
- auf einem physikalisch fundierten Verbrauchsmodell basieren, das später durch eigene Fahrdaten kalibriert werden kann,
- umfangreiche persönliche Präferenzen berücksichtigen,
- vollständig lokal bzw. ohne Abhängigkeit von proprietären Routingdiensten funktionieren.

Die Innovation liegt nicht in der Routenberechnung selbst, sondern in der Optimierung der Gesamtreise: Energieverbrauch, Ladeverhalten, Umweltbedingungen und persönliche Präferenzen werden zu einer realistischen, individualisierten Reiseplanung kombiniert.

---

## Grundarchitektur

Das Projekt wird in weitgehend unabhängige Module zerlegt (siehe `02-architektur.md`).

### Routing

Berechnung einer oder mehrerer sinnvoller Straßenrouten zwischen Start und Ziel. Diese Komponente kennt zunächst keine Informationen über Batteriestand, Ladeplanung oder Energieverbrauch.

**Favorisiert: GraphHopper**
- lokale Nutzung mit OSM-Daten
- ausgereifte Routingqualität, gut dokumentiert, erweiterbar
- Unterstützung von Turn Costs, Höhenprofilen, Fahrzeugprofilen, individuellen Weightings
- saubere API

**Alternative: Valhalla** — gute Unterstützung unterschiedlicher Fahrzeugtypen, flexible Kostenfunktionen, ebenfalls lokal betreibbar. Bleibt sinnvolle Alternative, GraphHopper aktuell einfacher erweiterbar.

**Nicht empfohlen: OSRM** — extrem schnell, aber zu wenig Flexibilität bei eigenen Kostenfunktionen und Fahrzeugmodellen.

### OSM-Daten

Straßeninfrastruktur liegt vollständig lokal vor (offline, reproduzierbar, keine API-Limits). Benötigt: Straßenklasse, Tempolimits, Kurven, Kreuzungen, Abbiegekosten, Straßentyp, Oberflächen, Tunnel/Brücken.

### Höhenmodell

Quellen: Copernicus DEM oder SRTM. Einmaliger lokaler Import. Dient der Berechnung von Steigungen, Gefällen, Rekuperation und potenzieller Energie.

---

## Energieverbrauch

Physikalisch modelliert, modular aufgebaut, später durch reale Fahrzeugdaten kalibrierbar.

**Kernkomponenten:** Rollwiderstand, Luftwiderstand, Höhenenergie, Rekuperation, Nebenverbraucher, Batterieverhalten.

**Einflussgrößen:** Fahrzeuggeschwindigkeit, Außentemperatur, Wind, Niederschlag, Schneefall, Straßenbelag, Fahrzeugbeladung, Dachbox, Winterreifen.

---

## Wettermodell

Segmentweise entlang der Route (nicht pro Straßenmeter) — z. B. alle 20–50 km oder alle 20–30 Minuten Fahrzeit. Für jeden Punkt werden die Bedingungen zum erwarteten Durchfahrtszeitpunkt abgefragt.

**Quelle: Open-Meteo** — kostenlos, kein API-Key, mehrere Wettermodelle, historische Daten, Stundenauflösung.

**Benötigte Variablen:** Temperatur, Windgeschwindigkeit, Windrichtung, Niederschlag, Schneefall, Luftdruck, Luftfeuchtigkeit, Sonneneinstrahlung, Bewölkung.

### Windmodell

Entscheidend ist die Projektion der Windrichtung auf die Fahrtrichtung (Gegenwind/Rückenwind/Seitenwind). Gegenwind auf Autobahnen kann den Verbrauch um deutlich mehr als 10 % erhöhen.

---

## Abfahrtszeit und Zeitplanung

Der Nutzer legt eine **Abfahrtszeit** fest. Alle zeitabhängigen Größen (Wetter, Ladesäulen-Öffnungszeiten sofern relevant) werden relativ zur erwarteten Durchfahrtszeit an jedem Segment berechnet.

Da die erwartete Durchfahrtszeit selbst vom Energieverbrauch und der Ladeplanung abhängt (die wiederum vom Wetter abhängen), erfolgt die Zeitschätzung **iterativ**: eine initiale grobe ETA-Schätzung wird zur ersten Wetterabfrage genutzt, anschließend werden Energieverbrauch und Ladeplan berechnet, die ETA je Segment aktualisiert und bei relevanter Abweichung die Wetterabfrage verfeinert (siehe `02-architektur.md`, Abschnitt „Iterative Zeit-/Wetterauflösung").

## Zwischenstopps

Der Nutzer kann **einen oder mehrere verpflichtende Zwischenstopps** (Waypoints) zwischen Start und Ziel definieren, die die Route in der angegebenen Reihenfolge passieren muss. Ein Zwischenstopp ist konzeptionell unabhängig von einem Ladestopp:

- Ein Zwischenstopp kann mit einer optionalen Aufenthaltsdauer versehen werden (z. B. „30 Minuten Pause an Ort X").
- Zwischenstopps fließen als harte Constraints in die Routing- und Optimierungsschicht ein.
- Ob an einem Zwischenstopp geladen wird, entscheidet die Ladeplanung eigenständig (ein Zwischenstopp ist kein automatischer Ladepunkt, kann aber mit einem kombiniert werden, falls dort ein Tesla Supercharger existiert).

---

## Ladeplanung

Zentraler Optimierungsschritt. Nicht die Route bestimmt den Ladeplan — die Kombination aus Route, Batteriestand, Ladekurve, Wetter, Höhenprofil, Abfahrtszeit und Zwischenstopps bestimmt den optimalen Ladeplan.

**Optimierungsziele:** möglichst kurze Ladezeiten, niedrige Gesamtreisezeit, möglichst kleine Sicherheitsreserven, möglichst hohe Robustheit.

Es wird ausdrücklich **nicht** grundsätzlich bis 100 % geladen. Optimiert werden Ladefenster wie 8→42 %, 12→55 %, 15→37 % je nach Gesamtsituation.

### Ladekurven

Modell der Ladekurve mit Einflussgrößen: Batterietyp, aktueller SoC, Batterietemperatur, Außentemperatur, Degradation, Ladeleistung der Säule. Später kalibrierbar aus eigenen Fahrzeugdaten.

### Ladeinfrastruktur

**Ausschließlich Tesla Supercharger** werden als Ladeinfrastruktur berücksichtigt. Andere Anbieter (OpenChargeMap, Chargemap etc.) sind explizit nicht Teil des Datenmodells.

**Benötigte Informationen je Standort:** Ladeleistung (Stall-Typ, z. B. V3/V4), Anzahl Stalls, Steckertyp, Standort/Koordinaten, Zuverlässigkeit/Status (sofern über die Datenquelle verfügbar).

Diese Einschränkung vereinfacht zugleich die Behandlung von Öffnungszeiten (Tesla Supercharger sind i. d. R. 24/7 zugänglich) — eine gesonderte Öffnungszeiten-Logik für Ladepunkte entfällt dadurch weitgehend.

---

## Baustellen

Relevanz liegt nicht in der Fahrzeit, sondern in Tempolimits, Sperrungen und Umleitungen.

**Quelle: DATEX II** — europäischer Standard, verfügbar für Deutschland, Dänemark, Schweden (relevante Länder für aktuelle Routen). Ein gemeinsamer Parser genügt, da alle Länder denselben Standard nutzen.

## Verkehr

Live-Verkehr ist explizit **nicht Bestandteil dieses Projekts** (hohe Kosten, proprietäre Daten, geringer Mehrwert für die Energieoptimierung).

## Kalibrierbarkeit des Verbrauchsmodells

Das physikalische Verbrauchsmodell wird von Beginn an so aufgebaut, dass es **später** anhand realer Tesla-Fahrdaten kalibriert werden kann (z. B. Anpassung der Modellparameter für Rollwiderstand, Luftwiderstand-Koeffizient, Nebenverbraucher-Baseline). Das ist eine Architekturanforderung an das Energiemodul (klare Trennung Modellstruktur/Parameter), keine eigenständige Projektphase im aktuellen Umsetzungsumfang.

---

## Reiseoptimierung

Erfolgt nach der Routenberechnung, auf dem durch GraphHopper bestimmten Straßenverlauf (siehe `02-architektur.md` für das Zwei-Phasen-Modell).

**Zustandsraum:** aktuelle Position, aktueller SoC, aktuelle Uhrzeit (für Wetter/ETA-Konsistenz).

**Kostenfunktion:** Fahrzeit + Ladezeit + Umweg + Sicherheitsreserve (+ optionale Strafkosten für Constraint-Verletzungen).

**Nebenbedingungen:**
- SoC darf Mindestwert nicht unterschreiten
- gewünschter SoC am Ziel
- maximale Etappenlänge
- verpflichtende Zwischenstopps in vorgegebener Reihenfolge (inkl. optionaler Aufenthaltsdauer)
- feste Abfahrtszeit als Startbedingung

### Optimierungsbibliotheken

- **Google OR-Tools** — leistungsfähig bei Optimierungsproblemen mit Nebenbedingungen.
- **NetworkX** — geeignet für den ersten Prototyp.
- **rustworkx** — Option für spätere, größere Graphen.

Die konkrete Bibliothekswahl ist zweitrangig; der Mehrwert liegt in der Definition von Zustandsraum und Kostenfunktion.

---

## Datenfluss

1. OSM-Routing berechnen (inkl. Zwischenstopps als Pflicht-Waypoints).
2. Höhenprofil extrahieren.
3. Route in Segmente unterteilen.
4. Initiale ETA je Segment aus Abfahrtszeit + grober Geschwindigkeitsannahme schätzen.
5. Wetterdaten entlang der Route zu den initialen ETAs abrufen.
6. Baustellen entlang der Route einbeziehen (Tempolimits/Sperrungen/Umleitungen).
7. Energieverbrauch je Segment berechnen (inkl. Wetter, Höhenprofil, Baustellen-Tempolimits).
8. Optimalen Ladeplan bestimmen (nur Tesla Supercharger, unter Berücksichtigung der Zwischenstopps).
9. ETA je Segment mit tatsächlicher Fahr-/Ladezeit aktualisieren; bei relevanter Abweichung Wetterabfrage (Schritt 5) verfeinern.
10. Gesamtreise simulieren.
11. Ergebnisse visualisieren.

---

## Modularer Aufbau

Unabhängige Module: Routing, Höhenmodell, Wetter, Energieverbrauch, Batteriemodell, Ladekurven, Ladeinfrastruktur (Tesla), Baustellen, Optimierung, Visualisierung. Einzelne Komponenten sind später austauschbar/verbesserbar (siehe `03-modulspezifikationen.md`).

---

## Aktuelle Architekturentscheidung

| Komponente | Entscheidung |
|---|---|
| Routing | GraphHopper |
| Kartendaten | OpenStreetMap |
| Höhenmodell | Copernicus DEM oder SRTM |
| Wetter | Open-Meteo |
| Baustellen | DATEX II |
| Ladepunkte | Tesla Supercharger (ausschließlich) |
| Verbrauch | Eigenes physikalisches Modell, kalibrierbar |
| Optimierung | Eigener zustandsbasierter Suchalgorithmus (A*/Dijkstra) mit frei definierbarer Kostenfunktion |
| Visualisierung | Eigene Kartenoberfläche (MapLibre GL JS oder Leaflet) |

**Entwurfsgrundsatz:** Vorhandene Software wird überall dort genutzt, wo sie ein bereits gelöstes Problem zuverlässig abdeckt (Routing, Karten, Wetter, Höhenmodell, Baustellen). Der eigentliche Mehrwert entsteht ausschließlich durch die selbst entwickelte Optimierungs- und Simulationslogik.
