#!/usr/bin/env bash
# scripts/build_basemap_tiles.sh — Baut den lokalen Vektor-Basemap-Tileset
# (PMTiles) aus demselben DE+DK+SE-OSM-Extrakt, den auch GraphHopper nutzt
# (siehe scripts/prepare_osm_extract.sh, data/de-dk-se.osm.pbf).
#
# Nutzt Planetiler (https://github.com/onthegomap/planetiler) mit dem
# eingebauten OpenMapTiles-Schema (kein Profil-Argument nötig — das ist der
# Default), damit das Ergebnis mit dem bestehenden MapLibre-Style
# ("liberty", OpenMapTiles-Schema) kompatibel ist. Ergebnis:
# data/tiles/basemap.pmtiles, ausgeliefert per `pmtiles serve`
# (docker-compose.yml, Service "tiles").
#
# Idempotent: ein bereits vorhandenes Ergebnis wird übersprungen. `--force`
# erzwingt einen erneuten Build (z. B. nach einem OSM-Extrakt-Update).
#
# Voraussetzung: Docker (OrbStack) läuft; wird von run.sh sichergestellt,
# bevor dieses Skript aufgerufen wird.

set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$0")/..")"

DATA_DIR="data"
OSM_EXTRACT="$DATA_DIR/de-dk-se.osm.pbf"
TILES_DIR="$DATA_DIR/tiles"
OUTPUT="$TILES_DIR/basemap.pmtiles"
PLANETILER_IMAGE="ghcr.io/onthegomap/planetiler:latest"
# Faustregel laut Planetiler-README: 5-10x der .osm.pbf-Größe an freiem
# Diskspace, mindestens 1GB zusätzlich. de-dk-se.osm.pbf ist ~5.7GB groß,
# daher hier mit Sicherheitsmarge nach oben (10x + Puffer) geprüft.
MIN_FREE_GB=30
JAVA_HEAP="${PLANETILER_JAVA_HEAP:--Xmx8g}"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if ! command -v docker >/dev/null 2>&1; then
  echo "docker nicht gefunden. OrbStack (oder Docker Desktop) installieren/starten." >&2
  exit 1
fi

if [[ ! -f "$OSM_EXTRACT" ]]; then
  echo "BASEMAP-TILES: OSM-Extrakt fehlt ($OSM_EXTRACT) — zuerst ./scripts/prepare_osm_extract.sh ausführen." >&2
  exit 1
fi

if [[ -f "$OUTPUT" && "$FORCE" -eq 0 ]]; then
  echo "BASEMAP-TILES: $OUTPUT bereits vorhanden, überspringe Build."
  exit 0
fi

mkdir -p "$TILES_DIR"

# Freien Diskspace auf dem Filesystem von $DATA_DIR prüfen (macOS `df -g`
# liefert Blöcke à 1GB direkt, kein awk-Umrechnen nötig).
free_gb="$(df -g "$DATA_DIR" | awk 'NR==2 {print $4}')"
if [[ "$free_gb" -lt "$MIN_FREE_GB" ]]; then
  echo "BASEMAP-TILES: nur ${free_gb}GB frei, mindestens ${MIN_FREE_GB}GB empfohlen" \
    "(Planetiler braucht ca. 5-10x der Eingabegröße als Scratch-Space)." >&2
  echo "BASEMAP-TILES: Abbruch. Diskspace freigeben oder MIN_FREE_GB in diesem Skript anpassen." >&2
  exit 1
fi

echo "BASEMAP-TILES: baue $OUTPUT aus $OSM_EXTRACT (OpenMapTiles-Schema, kann je nach" \
  "Hardware 15-60+ Minuten dauern, erster Lauf lädt zusätzlich ~1GB Hintergrunddaten" \
  "[Wasserflächen, Natural Earth]) …"
docker run --rm \
  -e JAVA_TOOL_OPTIONS="$JAVA_HEAP" \
  -v "$(pwd)/$DATA_DIR":/data \
  "$PLANETILER_IMAGE" \
  --osm-path="/data/de-dk-se.osm.pbf" \
  --output="/data/tiles/basemap.pmtiles" \
  --download \
  --force

echo "BASEMAP-TILES: fertig ($(du -h "$OUTPUT" | cut -f1))."
