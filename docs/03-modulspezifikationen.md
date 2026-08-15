# Modulspezifikationen

Jedes Modul ist ein eigenständiges Python-Package unter `src/tripplanner/<modulname>/` mit eigener Testsuite unter `tests/<modulname>/`. Datenstrukturen werden als Pydantic-Modelle in `src/tripplanner/<modulname>/models.py` definiert und von anderen Modulen ausschließlich darüber konsumiert.

---

## 1. `routing`

**Zweck:** Straßenroute zwischen Start, Zwischenstopps und Ziel berechnen.

**Eingaben:** Start-Koordinate, Ziel-Koordinate, geordnete Liste von Zwischenstopp-Koordinaten, Fahrzeugprofil.

**Ausgaben:** `Route` (Liste von `RouteSegment`: Geometrie, Länge, Straßenklasse, Tempolimit, Steigung-Rohdaten sofern von GraphHopper geliefert). Fährverbindungen werden mittels `routing.faehren.erkenne_faehren()` erkannt; sie können global vermieden werden (`TripRequest.alle_faehren_vermeiden`) oder gezielt für einzelne, zuvor erkannte Fähren (`vermiedene_faehren`) – beide Pfade wirken über das GraphHopper `custom_model`.

**Abhängigkeiten:** GraphHopper-HTTP-Client (`graphhopper_client.py`). Kein direkter Zugriff auf OSM-Rohdaten außerhalb dieses Moduls.

**Testbarkeit:** GraphHopper-Antworten werden für Tests als Fixtures (aufgezeichnete JSON-Responses) hinterlegt; kein Live-Request in Unit-Tests. Ein Integrationstest gegen eine lokale GraphHopper-Instanz ist gesondert markiert (`@pytest.mark.integration`).

---

## 2. `elevation`

**Zweck:** Höhenprofil für eine gegebene Route liefern.

**Eingaben:** Liste von Koordinaten (aus `routing`-Segmenten).

**Ausgaben:** Höhe je Koordinate, daraus abgeleitet Steigung/Gefälle je Segment.

**Abhängigkeiten:** Lokale DEM-Kacheln (Copernicus DEM/SRTM), Zugriff via `rasterio`.

**Testbarkeit:** Kleine synthetische Test-Kachel im Testfixture; keine Notwendigkeit, echte globale DEM-Daten im Testlauf vorzuhalten.

---

## 3. `weather`

**Zweck:** Wetterbedingungen für Streckenpunkte zu gegebenen Zeitpunkten liefern.

**Eingaben:** Liste von `(Koordinate, Zeitpunkt)`-Paaren.

**Ausgaben:** `WeatherSample` je Punkt: Temperatur, Windgeschwindigkeit, Windrichtung, Niederschlag, Schneefall, Luftdruck, Luftfeuchtigkeit, Sonneneinstrahlung, Bewölkung.

**Abhängigkeiten:** Open-Meteo-HTTP-Client.

**Besonderheit:** Muss die iterative Abfrage aus `02-architektur.md` unterstützen — d. h. eine Methode zur Neuabfrage bereits abgefragter Punkte mit aktualisiertem Zeitpunkt.

**Testbarkeit:** HTTP-Client hinter Interface (`WeatherProvider`), in Tests durch Fake ersetzt.

---

## 4. `wind`

**Zweck:** Aus Windgeschwindigkeit/-richtung und Fahrtrichtung die effektive Längs- und Seitenwindkomponente berechnen.

**Eingaben:** `WeatherSample`, Fahrtrichtung (Bearing) am Segment.

**Ausgaben:** Gegenwind-/Rückenwind-Komponente (m/s, vorzeichenbehaftet), Seitenwind-Komponente.

**Abhängigkeiten:** Keine externen Datenquellen — reine Berechnungslogik, daher vollständig unit-testbar mit einfachen Zahlenwerten.

---

## 5. `construction` (Baustellen)

**Zweck:** Aktive Baustellen/Sperrungen/Tempolimits entlang der Route liefern.

**Eingaben:** Route-Geometrie, Länderfilter (DE, DK, SE).

**Ausgaben:** Liste von `ConstructionZone`: betroffener Streckenabschnitt, Tempolimit, Sperrungstyp, Umleitungshinweis (falls vorhanden).

**Abhängigkeiten:** DATEX-II-Feeds der jeweiligen Länder, gemeinsamer Parser (ein Parser für alle Länder, da einheitlicher Standard).

**Testbarkeit:** Beispiel-DATEX-II-XML-Dateien als Fixtures.

---

## 6. `energy`

**Zweck:** Energieverbrauch je Segment physikalisch berechnen.

**Eingaben:** Segment (Länge, Steigung, Straßenklasse/Oberfläche, Tempolimit/Baustellen-Tempolimit), `WeatherSample`, Windkomponenten, Fahrzeugparameter (Masse inkl. Beladung, cW-Wert, Stirnfläche, Rollwiderstandsbeiwert, Nebenverbraucher-Baseline, Reifentyp).

**Ausgaben:** Energiebedarf (kWh) je Segment, unter Berücksichtigung von Rekuperation (nur bei Gefälle/Verzögerung, physikalisch plausibel begrenzt).

**Design-Anforderung:** Modellparameter (cW, Rollwiderstand etc.) sind als eigene, austauschbare Konfiguration/Objekt zu übergeben, nicht hartkodiert — das ist Voraussetzung für spätere Kalibrierung mit echten Fahrdaten (kein eigenes Kalibrierungs-Feature im aktuellen Umfang, aber diese Trennung muss von Anfang an bestehen).

**Testbarkeit:** Rein deterministische Berechnung, sehr gut mit bekannten Referenzwerten (z. B. "100 km/h, eben, windstill → X kWh/100km") unit-testbar.

---

## 7. `battery` (Ladekurve/Batteriezustand)

**Zweck:** SoC-Verlauf während Fahrt (Entladung) und während Ladevorgängen (Ladekurve) modellieren.

**Eingaben:** Aktueller SoC, Energiebedarf (aus `energy`), Batterietemperatur (vereinfachtes Modell, ggf. aus Außentemperatur abgeleitet), Ladeleistung der Säule.

**Ausgaben:** SoC nach Fahrsegment; SoC-über-Zeit-Kurve während eines Ladevorgangs (zur Bestimmung, wie lange bis zu welchem Ziel-SoC geladen werden muss).

**Testbarkeit:** Ladekurve als parametrisierte Funktion (z. B. stückweise linear/Lookup-Tabelle je SoC-Bereich), mit Referenzwerten testbar.

---

## 8. `charging_infrastructure`

**Zweck:** Verfügbare Tesla Supercharger entlang bzw. in der Nähe der Route liefern.

**Eingaben:** Route-Geometrie, Suchradius.

**Ausgaben:** Liste von `ChargingStation`: Koordinate, Anzahl Stalls, max. Ladeleistung, Standortname.

**Abhängigkeiten:** Tesla-eigene Ladepunktdaten (offizielle Quelle, siehe Rechercheaufgabe in `06-offene-punkte-widersprueche.md` zur konkreten Datenherkunft/Lizenzfrage).

**Design-Hinweis:** Zugriff hinter einem `ChargingStationProvider`-Interface kapseln, damit die Datenquelle austauschbar bleibt, auch wenn aktuell nur ein Provider implementiert wird.

---

## 9. `optimization`

**Zweck:** Optimalen Ladeplan über die gesamte Route bestimmen.

**Eingaben:** Route mit Segmenten, Energiebedarf je Segment, verfügbare Ladestationen, Zwischenstopps (inkl. Aufenthaltsdauer), Abfahrtszeit, Start-SoC, Ziel-SoC, Nebenbedingungen (Mindest-SoC etc.).

**Ausgaben:** `ChargingPlan`: geordnete Liste von Ladehalten mit Ankunfts-SoC, Ziel-SoC, geschätzter Ladedauer, sowie Gesamtreisezeit.

**Abhängigkeiten:** OR-Tools und/oder NetworkX hinter einer gemeinsamen `Optimizer`-Schnittstelle, damit die konkrete Bibliothek austauschbar ist.

**Testbarkeit:** Kleine synthetische Szenarien (wenige Segmente, wenige Ladestationen) mit von Hand nachvollziehbarem optimalem Ergebnis als Regressionstests.

---

## 10. `simulation`

**Zweck:** Aus Route + Ladeplan eine vollständige Zeit-/SoC-Simulation der Reise erzeugen (für Visualisierung und Validierung).

**Eingaben:** Route, `ChargingPlan`, Energie- und Wetterdaten je Segment.

**Ausgaben:** Zeitreihe (Zeitpunkt, Position, SoC, aktuelle Geschwindigkeit/Zustand: Fahren/Laden/Pause).

---

## 11. `visualization`

**Zweck:** Route, Ladehalte, Zwischenstopps und SoC-Verlauf grafisch darstellen.

**Technologie:** TypeScript, MapLibre GL JS oder Leaflet. Konsumiert die Ausgabe von `simulation` als JSON-Schnittstelle (klarer Contract, z. B. via JSON-Schema aus den Pydantic-Modellen generiert).

---

## 12. `trip_input` / CLI bzw. API-Schicht

**Zweck:** Eingabe von Start, Ziel, Zwischenstopps, Abfahrtszeit, Fahrzeugparametern, Präferenzen entgegennehmen und an die Pipeline übergeben. Als konkretes Beispiel implementiert: Fährvermeidung über zwei typisierte Felder (`alle_faehren_vermeiden`/`vermiedene_faehren`), unabhängig vom generischen `praeferenzen`-Dict.

**Form (aktueller Umfang):** CLI und/oder einfache lokale API (z. B. FastAPI) als dünne Schicht über der Pipeline aus `02-architektur.md`, ohne eigene Optimierungslogik.
