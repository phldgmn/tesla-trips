#!/usr/bin/env bash
# scripts/prepare_osm_extract.sh — Baut den lokalen DE+DK+SE-OSM-Extrakt für GraphHopper.
#
# Lädt die vollständigen Geofabrik-Länderextrakte für Deutschland, Dänemark
# und Schweden herunter und führt sie mit `osmium merge` zu einer einzelnen
# Datei `data/de-dk-se.osm.pbf` zusammen. GraphHopper (siehe
# docker-compose.yml, `-i /data/de-dk-se.osm.pbf`) liest ausschließlich eine
# einzelne lokale Eingabedatei - daher der Merge-Schritt.
#
# Idempotent: bereits vorhandene Downloads/das Merge-Ergebnis werden
# übersprungen. `--force` erzwingt einen erneuten Download + Merge (z. B.
# nach einer abgebrochenen/beschädigten Datei).
#
# Voraussetzung: osmium-tool (`brew install osmium-tool`).

set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$0")/..")"

DATA_DIR="data"
SOURCES_DIR="$DATA_DIR/osm-sources"
OUTPUT="$DATA_DIR/de-dk-se.osm.pbf"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if ! command -v osmium >/dev/null 2>&1; then
  echo "osmium-tool nicht gefunden. Installation: brew install osmium-tool" >&2
  exit 1
fi

mkdir -p "$SOURCES_DIR"

# Reihenfolge der beiden Arrays muss übereinstimmen (kein assoziatives Array
# fuer Kompatibilitaet mit dem aelteren /bin/bash 3.2 unter macOS).
NAMES=(germany denmark sweden)
URLS=(
  "https://download.geofabrik.de/europe/germany-latest.osm.pbf"
  "https://download.geofabrik.de/europe/denmark-latest.osm.pbf"
  "https://download.geofabrik.de/europe/sweden-latest.osm.pbf"
)

for i in "${!NAMES[@]}"; do
  name="${NAMES[$i]}"
  url="${URLS[$i]}"
  file="$SOURCES_DIR/${name}-latest.osm.pbf"
  if [[ -f "$file" && "$FORCE" -eq 0 ]]; then
    echo "OSM-EXTRACT: $name bereits vorhanden ($file), überspringe Download."
    continue
  fi
  echo "OSM-EXTRACT: lade $name herunter … ($url)"
  curl -fL --progress-bar -o "${file}.tmp" "$url"
  mv "${file}.tmp" "$file"
done

if [[ -f "$OUTPUT" && "$FORCE" -eq 0 ]]; then
  echo "OSM-EXTRACT: $OUTPUT bereits vorhanden, überspringe Merge."
  exit 0
fi

echo "OSM-EXTRACT: merge Deutschland+Dänemark+Schweden nach $OUTPUT …"
osmium merge \
  "$SOURCES_DIR/germany-latest.osm.pbf" \
  "$SOURCES_DIR/denmark-latest.osm.pbf" \
  "$SOURCES_DIR/sweden-latest.osm.pbf" \
  -o "${OUTPUT}.tmp" -f pbf --overwrite
mv "${OUTPUT}.tmp" "$OUTPUT"
echo "OSM-EXTRACT: fertig ($(du -h "$OUTPUT" | cut -f1))."
