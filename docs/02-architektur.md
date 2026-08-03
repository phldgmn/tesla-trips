# Systemarchitektur

## Überblick

Das System besteht aus zwei klar getrennten Phasen, die von unabhängigen, austauschbaren Modulen gespeist werden:

```
Phase 1 — Straßenrouting (GraphHopper)
   Start, Ziel, Zwischenstopps  →  Straßenroute(n) (Geometrie + Segmente)

Phase 2 — Energie-/Lade-Optimierung (eigene Logik)
   Route + Höhenprofil + Wetter + Baustellen + Ladeinfrastruktur + Abfahrtszeit
        →  optimierter Ladeplan + Gesamtsimulation
```

**Wichtig:** Phase 1 kennt keine Energie-/Ladeinformationen. Phase 2 verändert die Straßenroute nicht (kein Energie-optimales Rerouting in der aktuellen Umsetzung) — sie plant ausschließlich Ladehalte und Geschwindigkeitsprofile auf der von GraphHopper festgelegten Route. Diese Aufteilung ist eine bewusste Vereinfachung; siehe `06-offene-punkte-widersprueche.md` für die Diskussion der Konsequenzen.

## Komponentendiagramm (textuell)

```
                     ┌────────────────────┐
                     │  Trip Input         │
                     │  Start, Ziel,        │
                     │  Zwischenstopps,      │
                     │  Abfahrtszeit          │
                     └─────────┬────────────┘
                               │
                     ┌─────────▼────────────┐
                     │  Routing-Modul        │
                     │  (GraphHopper-Client) │
                     └─────────┬────────────┘
                               │ Route + Segmente
              ┌────────────────┼─────────────────┐
              │                │                 │
    ┌─────────▼───────┐ ┌──────▼───────┐ ┌───────▼────────┐
    │ Höhenmodell       │ │ ETA-Schätzer │ │ Baustellen-Modul│
    │ (DEM-Lookup)       │ │ (iterativ)   │ │ (DATEX II)      │
    └─────────┬───────┘ └──────┬───────┘ └───────┬────────┘
              │                │                 │
              │       ┌────────▼─────────┐       │
              │       │  Wetter-Modul     │       │
              │       │  (Open-Meteo)     │       │
              │       └────────┬─────────┘       │
              │                │                 │
              └────────┬───────┴────────┬────────┘
                       │                │
              ┌────────▼────────────────▼────────┐
              │      Energieverbrauchs-Modul       │
              │  (physikalisches Modell, modular)  │
              └────────────────┬───────────────────┘
                                │ Energiebedarf je Segment
              ┌─────────────────▼──────────────────┐
              │  Ladeinfrastruktur-Modul (Tesla)    │
              │  + Ladekurven-Modul                  │
              └─────────────────┬──────────────────┘
                                │
              ┌─────────────────▼──────────────────┐
              │  Optimierungs-Modul                  │
              │  (Zustandsraum-Suche, OR-Tools/       │
              │   NetworkX)                            │
              └─────────────────┬──────────────────┘
                                │ Ladeplan + Gesamtsimulation
              ┌─────────────────▼──────────────────┐
              │  Visualisierung (MapLibre/Leaflet)  │
              └─────────────────────────────────────┘
```

## Iterative Zeit-/Wetterauflösung

Problem: Die erwartete Durchfahrtszeit an einem Segment hängt vom bisherigen Energie- und Ladeverlauf ab, der wiederum vom Wetter an früheren Segmenten abhängt. Es besteht also eine Abhängigkeit zwischen ETA-Berechnung und Wetterabfrage.

**Lösung — zweistufige Berechnung:**

1. **Grobe Vorwärtsschätzung:** ETA je Segment auf Basis einer Referenzgeschwindigkeit (z. B. Tempolimit oder historischer Durchschnitt) ohne Energie-/Ladeeinfluss.
2. **Wetterabfrage** zu diesen groben ETAs (Auflösung: alle 20–50 km bzw. 20–30 Minuten Fahrzeit).
3. **Energie- und Ladeplanberechnung** unter Einbeziehung des abgefragten Wetters.
4. **ETA-Aktualisierung** je Segment auf Basis der tatsächlich berechneten Fahr- und Ladezeiten.
5. **Konvergenzprüfung:** Weicht die aktualisierte ETA an einem Wetterabfragepunkt um mehr als einen konfigurierbaren Schwellwert (z. B. 30 Minuten) von der ursprünglich abgefragten Zeit ab, wird die Wetterabfrage für die betroffenen Punkte wiederholt und Schritt 3–4 erneut ausgeführt.
6. In der Praxis genügt für Reisen im Tagesbereich in aller Regel eine einzige Nachiteration; die maximale Iterationszahl sollte dennoch als Sicherheitsgrenze konfigurierbar sein.

## Zwischenstopps als Constraint

Zwischenstopps werden dem Routing-Modul als geordnete Pflicht-Waypoints übergeben (GraphHopper unterstützt mehrere Wegpunkte nativ). Für die Optimierungsschicht werden sie als zusätzliche Knoten im Zustandsgraphen mit folgenden Eigenschaften modelliert:

- Position (fix, durch Route vorgegeben)
- optionale Mindestaufenthaltsdauer (Constraint: Ankunftszeit + Aufenthaltsdauer ≤ Abfahrt zum nächsten Abschnitt)
- optionale Kombinierbarkeit mit einem Ladevorgang, falls am selben Standort ein Tesla Supercharger existiert

## Technologie-Stack

| Ebene | Technologie |
|---|---|
| Backend / Optimierungslogik | Python (uv als Paket-/Runtime-Manager) |
| Routing-Engine | GraphHopper (Java, als lokaler Dienst/Container betrieben, per HTTP-API angesprochen) |
| Kartendaten-Pipeline | OSM-Extrakte (Osmium/osmconvert), lokal vorgehalten |
| Geodaten/Höhen | Copernicus DEM/SRTM-Kacheln, lokal vorgehalten, Zugriff via rasterio |
| Persistenz | Lokale Datenbank für Ladeinfrastruktur, Segmente, Cache (z. B. SQLite/DuckDB für Prototyp; PostGIS als spätere Option bei Bedarf) |
| Frontend/Visualisierung | TypeScript, MapLibre GL JS oder Leaflet |
| Optimierung | OR-Tools und/oder NetworkX (austauschbar hinter gemeinsamer Schnittstelle) |

## Modulgrenzen und Schnittstellen

Jedes Modul kommuniziert ausschließlich über klar typisierte Datenstrukturen (z. B. Pydantic-Modelle in Python), nie über implizite globale Zustände. Details je Modul siehe `03-modulspezifikationen.md`.

Grundprinzip: Jedes Modul muss isoliert testbar sein (Unit-Tests ohne Netzwerkzugriff, externe Datenquellen werden über Interfaces gemockt).
