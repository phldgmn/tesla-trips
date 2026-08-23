# AGENTS.md — Leitlinien für KI-Coding-Agenten

Diese Datei liegt im Repo-Root und wird von KI-Coding-Agenten vor Beginn jeder Aufgabe gelesen.

## Grundprinzip

Code gilt nur dann als fertig, wenn **alle** der folgenden Punkte erfüllt sind — nicht als Checkliste zum Abhaken nach dem Schreiben, sondern als Definition of Done:

1. `uv run hk check --all` läuft ohne Fehler durch (Linting, Formatting, Type-Checking).
2. Für jede neue Funktionalität existieren Tests, die vor der Änderung fehlschlagen und danach erfolgreich sind.
3. `uv run pytest -m "not integration"` läuft vollständig grün.
4. Die Coverage-Schwelle aus `docs/04-repo-tooling-setup.md` wird nicht unterschritten (85 % für `src/tripplanner/`).
5. Keine neue Abhängigkeit zwischen Modulen außer über die in `models.py` definierten Schnittstellen (siehe `docs/03-modulspezifikationen.md`).
6. **Änderungen werden IMMER committet.** Jede abgeschlossene Aufgabe (Bugfix, Feature, Refactor) endet mit einem `git commit` der Änderungen — unabhängig davon, ob explizit danach gefragt wurde. Unfertige/experimentelle Arbeit ausdrücklich ausgenommen (z. B. auf explizite Nutzeranweisung "noch nicht committen"). Kein Task gilt als abgeschlossen, solange Änderungen nur im Arbeitsverzeichnis liegen.
7. **Häufig committen.** Statt einer Aufgabe einen einzigen großen Commit am Ende zu geben, wird in kleinen, in sich abgeschlossenen Schritten committet (z. B. nach jedem funktionierenden Zwischenstand). Das erleichtert es, bei einem Fehlschlag gezielt auf einen früheren, funktionierenden Stand zurückzusetzen, statt die gesamte Aufgabe zu verwerfen.

## Services starten und stoppen

Für Integrationstests und das manuelle Prüfen von API/Frontend wird **ausschließlich** `./run.sh` verwendet — nicht `uvicorn` direkt, nicht `npm run dev` direkt. Das Skript sorgt für:

1. **Stoppen laufender Instanzen** vor dem Neustart — auch wenn sie von einem vorherigen Agenten-Lauf übrig sind.
2. **Farbig markierte, identifizierbare Ausgabe** (`[BACKEND]` / `[FRONTEND]`) in einem gemeinsamen Terminal.
3. **PID-Tracking in `.run/`**, sodass auch nach einem Session-Wechsel klar ist, was läuft.
4. **Einheitliche Log-Dateien** (`.run/backend.log`, `.run/frontend.log`) für Debugging.

```bash
# Beide Dienste starten (Default; stoppt vorher, falls bereits etwas läuft)
./run.sh start

# Nur Backend
./run.sh start backend

# Nur Frontend
./run.sh start frontend

# Status prüfen
./run.sh status

# Stoppen
./run.sh stop

# Neu starten (stop + start)
./run.sh restart
```

**Regel:** Agenten, die für eine Aufgabe Backend oder Frontend benötigen (Integrationstests, visuelle Prüfung, API-Tests gegen einen laufenden Server), starten die Dienste über `./run.sh` und beenden sie nach Abschluss der Aufgabe über `./run.sh stop`. Der direkte Aufruf von `uvicorn` oder `npm run dev` zum Start von Diensten ist nicht zulässig.

## Verbotene Abkürzungen

Agenten dürfen **nicht**:

- Commits mit `--no-verify` oder vergleichbaren Mechanismen an den Hooks vorbei erzeugen.
- Lint- oder Type-Fehler durch `# noqa`, `# type: ignore` oder das Absenken von `mypy`/`ruff`-Regeln in `pyproject.toml` "beheben", ohne dass eine inhaltliche Begründung im Commit/PR dokumentiert ist. Regel-Ausnahmen sind auf Zeilenebene mit Begründungskommentar zulässig, nicht als globale Config-Änderung ohne Rücksprache.
- Tests löschen oder deaktivieren (`skip`), um eine rote CI grün zu bekommen.
- Externe Datenquellen (GraphHopper, Open-Meteo, DATEX II, Tesla-Ladepunktdaten) in Unit-Tests live ansprechen — dafür existieren Fixtures (siehe `docs/03-modulspezifikationen.md`).
- Systemweite Suchen wie `find / …`, `find ~ …` oder vergleichbare Scans über das gesamte Dateisystem/Home-Verzeichnis. Suchen sind auf das Repo-Verzeichnis (oder explizit benannte, enge Pfade) zu beschränken — z. B. `glob`/`grep`-Tools mit repo-relativem Pfad statt eines ungezielten `find /`.

## Vorgehen pro Aufgabe

1. Zuständiges Modul aus `docs/03-modulspezifikationen.md` bzw. den Detailplänen unter `docs/plans/` identifizieren; Aufgabe nicht modulübergreifend beginnen, wenn sie sich auf ein Modul eingrenzen lässt.
2. Bestehende Schnittstellen (`models.py` des Moduls) lesen, bevor neue Datenstrukturen eingeführt werden — Duplikate von Datenmodellen vermeiden.
3. Test zuerst schreiben oder zumindest vor der Implementierung festlegen, anhand welcher Testfälle die Änderung verifiziert wird.
4. Implementierung.
5. `uv run hk check --all` und relevante Tests lokal ausführen, bevor ein Commit vorgeschlagen wird.
6. Commit-Nachricht beschreibt **was** und **warum**, nicht nur **was** (z. B. nicht nur "add wind module", sondern kurz die Berechnungsannahme benennen).

## Sub-Agenten

Wo sinnvoll werden Sub-Agenten eingesetzt, um unabhängige Teilaufgaben zu parallelisieren — z. B. Recherche über mehrere Module hinweg, unabhängige Bugfixes in getrennten Dateien, oder das parallele Einholen von Kontext, während der Hauptagent an der eigentlichen Implementierung weiterarbeitet. Voraussetzung: Die Teilaufgaben sind wirklich unabhängig (keine gemeinsam bearbeiteten Dateien, keine sequentielle Abhängigkeit), und ihre Ergebnisse werden vor der Übernahme geprüft statt ungesehen gemergt.

## Modulgrenzen

- Kein Modul greift auf interne Implementierungsdetails eines anderen Moduls zu — nur auf dessen `models.py`-Datenstrukturen und öffentliche Funktionen/Klassen.
- Ausnahme: `tripplanner.geo` ist ein abhängigkeitsfreies Geo-Primitiv (kein Business-Modul) und darf von jedem Modul importiert werden (siehe `docs/07-implementierungsplan.md`, Abschnitt 6.2).
- Externe Datenquellen (HTTP-Clients, Dateisystemzugriffe) werden hinter einem Provider-Interface gekapselt (siehe z. B. `WeatherProvider`, `ChargingStationProvider` in `docs/03-modulspezifikationen.md`), damit sie in Tests ersetzbar sind und die Datenquelle bei Bedarf austauschbar bleibt.
- Neue externe Abhängigkeiten (Bibliotheken, APIs) werden nicht ohne Bezug zu einem der in `docs/01-projektspezifikation.md` festgelegten Architekturentscheidungen eingeführt.

## Typannotationen und Docstrings

- Jede öffentliche Funktion/Methode hat vollständige Typannotationen (durch `mypy --strict` erzwungen) und einen Docstring im projektweit einheitlichen Stil (Google-Style, siehe `docs/04-repo-tooling-setup.md`).
- Pydantic-Modelle sind die einzige zulässige Form für Datenstrukturen, die Modulgrenzen überqueren.

## Sprache in Code, Kommentaren und Dokumentation

- Neuer Code, neue Kommentare und neue Dokumentation (Docstrings, README-Abschnitte, `docs/`-Dateien, Commit-Nachrichten für Code-Inhalte) werden **immer auf Englisch** verfasst — unabhängig von der Sprache dieser AGENTS.md-Datei oder bestehender Altbestände im Repo.
- Wird bestehender Code, ein bestehender Kommentar oder ein bestehender Dokumentationsabschnitt bearbeitet und ist der betroffene Teil noch auf Deutsch, fragt der Agent aktiv beim Nutzer nach, ob dieser Teil im Zuge der Änderung ins Englische übersetzt werden soll, statt ihn stillschweigend auf Deutsch zu belassen oder weiter auf Deutsch zu ergänzen.

## Koordinatenkonvention

Alle `Coordinate`-Tupel im Projekt sind `(lat, lon)`. Ausnahmen nur an den drei in `docs/07-implementierungsplan.md`, Abschnitt 3, dokumentierten externen Grenzen (GraphHopper-Request, rasterio-Pixel-Lookup, MapLibre/GeoJSON-Rendering).

## Bei Unsicherheit

Wenn eine Anforderung mehrdeutig ist, trifft der Agent eine begründete, dokumentierte Annahme (Kommentar im Code + Erwähnung im PR-Text) statt die Aufgabe unbearbeitet zu lassen — außer die Mehrdeutigkeit betrifft eine der offenen Fragen in `docs/06-offene-punkte-widersprueche.md`; diese sind bereits in `docs/07-implementierungsplan.md`, Abschnitt 7, verbindlich entschieden.
