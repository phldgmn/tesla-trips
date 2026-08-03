# AGENTS.md — Leitlinien für KI-Coding-Agenten

Diese Datei liegt im Repo-Root und wird von KI-Coding-Agenten vor Beginn jeder Aufgabe gelesen.

## Grundprinzip

Code gilt nur dann als fertig, wenn **alle** der folgenden Punkte erfüllt sind — nicht als Checkliste zum Abhaken nach dem Schreiben, sondern als Definition of Done:

1. `uv run hk check --all` läuft ohne Fehler durch (Linting, Formatting, Type-Checking).
2. Für jede neue Funktionalität existieren Tests, die vor der Änderung fehlschlagen und danach erfolgreich sind.
3. `uv run pytest -m "not integration"` läuft vollständig grün.
4. Die Coverage-Schwelle aus `04-repo-tooling-setup.md` wird nicht unterschritten.
5. Keine neue Abhängigkeit zwischen Modulen außer über die in `models.py` definierten Schnittstellen (siehe `03-modulspezifikationen.md`).

## Verbotene Abkürzungen

Agenten dürfen **nicht**:

- Commits mit `--no-verify` oder vergleichbaren Mechanismen an den Hooks vorbei erzeugen.
- Lint- oder Type-Fehler durch `# noqa`, `# type: ignore` oder das Absenken von `mypy`/`ruff`-Regeln in `pyproject.toml` "beheben", ohne dass eine inhaltliche Begründung im Commit/PR dokumentiert ist. Regel-Ausnahmen sind auf Zeilenebene mit Begründungskommentar zulässig, nicht als globale Config-Änderung ohne Rücksprache.
- Tests löschen oder deaktivieren (`skip`), um eine rote CI grün zu bekommen.
- Externe Datenquellen (GraphHopper, Open-Meteo, DATEX II, Tesla-Ladepunktdaten) in Unit-Tests live ansprechen — dafür existieren Fixtures (siehe `03-modulspezifikationen.md`).

## Vorgehen pro Aufgabe

1. Zuständiges Modul aus `03-modulspezifikationen.md` identifizieren; Aufgabe nicht modulübergreifend beginnen, wenn sie sich auf ein Modul eingrenzen lässt.
2. Bestehende Schnittstellen (`models.py` des Moduls) lesen, bevor neue Datenstrukturen eingeführt werden — Duplikate von Datenmodellen vermeiden.
3. Test zuerst schreiben oder zumindest vor der Implementierung festlegen, anhand welcher Testfälle die Änderung verifiziert wird.
4. Implementierung.
5. `uv run hk check --all` und relevante Tests lokal ausführen, bevor ein Commit vorgeschlagen wird.
6. Commit-Nachricht beschreibt **was** und **warum**, nicht nur **was** (z. B. nicht nur "add wind module", sondern kurz die Berechnungsannahme benennen).

## Modulgrenzen

- Kein Modul greift auf interne Implementierungsdetails eines anderen Moduls zu — nur auf dessen `models.py`-Datenstrukturen und öffentliche Funktionen/Klassen.
- Externe Datenquellen (HTTP-Clients, Dateisystemzugriffe) werden hinter einem Provider-Interface gekapselt (siehe z. B. `WeatherProvider`, `ChargingStationProvider` in `03-modulspezifikationen.md`), damit sie in Tests ersetzbar sind und die Datenquelle bei Bedarf austauschbar bleibt.
- Neue externe Abhängigkeiten (Bibliotheken, APIs) werden nicht ohne Bezug zu einem der in `01-projektspezifikation.md` festgelegten Architekturentscheidungen eingeführt.

## Typannotationen und Docstrings

- Jede öffentliche Funktion/Methode hat vollständige Typannotationen (durch `mypy --strict` erzwungen) und einen Docstring im projektweit einheitlichen Stil (siehe `04-repo-tooling-setup.md`).
- Pydantic-Modelle sind die einzige zulässige Form für Datenstrukturen, die Modulgrenzen überqueren.

## Bei Unsicherheit

Wenn eine Anforderung mehrdeutig ist (z. B. konkreter Schwellwert für die ETA-Neuiteration, konkrete Tesla-Ladepunkt-Datenquelle — siehe `06-offene-punkte-widersprueche.md`), trifft der Agent eine begründete, dokumentierte Annahme (Kommentar im Code + Erwähnung im PR-Text) statt die Aufgabe unbearbeitet zu lassen — außer die Mehrdeutigkeit betrifft eine der offenen Fragen in `06-offene-punkte-widersprueche.md`; diese werden vor Beginn der jeweiligen Modul-Implementierung mit dem Projektverantwortlichen geklärt.
