#!/usr/bin/env python3
"""Translate remaining German content in src/tripplanner/ to English."""
import pathlib, subprocess

ROOT = pathlib.Path(__file__).parent / "src" / "tripplanner"

def translate(filepath, replacements):
    """Apply string replacements to a file."""
    p = ROOT / filepath
    text = p.read_text()
    count = 0
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            count += 1
    p.write_text(text)
    return count

# Files to fix with their (old_text, new_text) replacements
TASKS = [
    ("battery/battery.py", [
        ("Integrate over SoC range: \u222b dQ", "Integrate over SoC range: dQ/dE"),
    ]),
    ("cache/__init__.py", [
        ("importable by any", "importable by any"),  # \u2013 is em-dash, keep
    ]),
    ("charging_infrastructure/clients/tesla_curl.py", [
        ("Header-Sequenz und den", "Header sequence and"),
    ]),
    ("charging_infrastructure/providers/fake.py", [
        ("name=\"Tesla Supercharger - M\u00e4lmo Urban\"", 'name="Tesla Supercharger - Malmo Urban"'),
    ]),
    ("charging_infrastructure/providers/pricing_queue.py", [
        ("\"28500\"", "\"28500\""),  # likely Unicode issue, check
    ]),
    ("charging_infrastructure/providers/spatial.py", [
        ("Roh-Segment", "Raw segment"),
    ]),
    ("construction/matching.py", [
        ("folded into the [0\u00b0, 180\u00b0] range", "folded into the [0, 180] degree range"),
        ("excluded if", "excluded if"),  # em-dash
        ("(opposite carriageway on a divided highway)", "(opposite carriageway on a divided highway)"),
        ("more than 100\u00b0 (after folding", "more than 100 degrees (after folding"),
        ("[0\u00b0, 180\u00b0] range \u2014 i.e.", "[0, 180] degree range — i.e."),
        ("Source states a specific direction", "Source states a specific direction"),
        ("Exclude if bearings are roughly opposite (>100\u00b0 apart).", "Exclude if bearings are roughly opposite (>100 degrees apart)."),
        ("carriageway (bearing diff greater than 100\u00b0, folded", "carriageway (bearing diff greater than 100 degrees, folded"),
    ]),
    ("construction/models.py", [
        ("Alle implementierenden provider m\u00fcssen", "All implementing providers must"),
        ("die eine Liste von ConstructionZone f\u00fcr eine gegebene Route zur\u00fcckgibt.",
         "which returns a list of ConstructionZone for a given route."),
        ("\"\"\"Query von construction zones entlang der Route f\u00fcr die angegebenen L\u00e4nder.\"\"\"",
         '"""Query for construction zones along the route for the specified countries."""'),
    ]),
    ("construction/providers/config.py", [
        ("f\u00fcr", "for"),
    ]),
    ("construction/providers/impl.py", [
        ("L\u00e4ndercode", "country code"),
        ("Route", "route"),
    ]),
    ("construction/providers_de_autobahn.py", [
        ("L\u00e4nder-IDs", "country IDs"),
    ]),
    ("construction/providers_de_datexii.py", [
        ("L\u00e4ndercodes", "country codes"),
    ]),
    ("elevation/elevation.py", [
        ("elevation_points: ElevationPoints in route order (start\u2192destination)",
         "elevation_points: ElevationPoints in route order (start to destination)"),
    ]),
    ("elevation/models.py", [
        ("H\u00f6henprofile-basiert", "Elevation-profile-based"),
        ("Gradient je Segment", "Gradient per segment"),
        ("Steigung", "gradient"),
    ]),
    ("elevation/providers.py", [
        ("noise_range: Maximum deviation from the baseline elevation (\u00b1noise_range/2)",
         "noise_range: Maximum deviation from the baseline elevation (plus-minus noise_range/2)"),
        ("# \u2500 Facade delegators for tests", "# Facade delegators for tests"),
        ("#    the original CopernicusDEMDataSource before the refactor. \u2500",
         "#    the original CopernicusDEMDataSource before the refactor."),
        ("List of elevation floats", "List of elevation floats"),  # likely em-dash
        ("# \u2500 Single bulk read", "# Single bulk read"),
        ("# \u2500 Per-point indexing", "# Per-point indexing"),
        ("# Out-of-bounds index", "# Out-of-bounds index"),
        ("# \u2500 Fix 3: Schedule disk-cache", "# Fix 3: Schedule disk-cache"),
        ("Groups coordinates by 1\u00b0 DEM tile", "Groups coordinates by 1 degree DEM tile"),
    ]),
    ("elevation/tile_cache.py", [
        ("\"\"\"Calculate den Copernicus-DEM-Kachelnamen f\u00fcr die 1x1-Grad-Zelle einer Koordinate.\"\"\"",
         '"""Calculate the Copernicus DEM tile name for the 1x1 degree cell of a coordinate."""'),
        ("\"\"\"Baue die GDAL-lesbare URI (vsicurl oder lokaler Pfad) f\u00fcr eine Kachel.",
         '"""Build the GDAL-readable URI (vsicurl or local path) for a tile.'),
        ("Fix 3 \u2014 when", "Fix 3 \u2014 when"),  # em-dash is OK
        ("# Fix 3 \u2014 local cache hit", "# Fix 3 \u2014 local cache hit"),
        ("GDAL translate/copy pass over the network \u2014 this was the cause of",
         "GDAL translate/copy pass over the network \u2014 this was the cause of"),
        ("synchronously here \u2014 before the thread starts", "synchronously here \u2014 before the thread starts"),
        ("Failure is silently logged \u2014 it must never", "Failure is silently logged \u2014 it must never"),
        ('logger.warning("DEM-Kachel %s konnte nicht ge\u00f6ffnet werden - Fallback 0.0m", uri)',
         'logger.warning("DEM tile %s could not be opened - fallback to 0.0m", uri)'),
    ]),
    ("energy/energy.py", [
        ("# Luftdichte (ISA-Standard bei 15\u00b0C, 1013 hPa)", "# Air density (ISA standard at 15C, 1013 hPa)"),
        ("R_specific f\u00fcr dry air", "R_specific for dry air"),
        ("temperature_c: temperature in \u00b0C", "temperature_c: temperature in degrees C"),
        ("Luftdichte in kg/m\u00b3", "Air density in kg/m³"),
        ("temperature_c: temperature in \u00b0C (for ice detection).",
         "temperature_c: temperature in degrees C (for ice detection)."),
        ("# ice/sleet (freezing rain below 0\u00b0C)", "# ice/sleet (freezing rain below 0C)"),
        ("3. Convert forces \u2192 power \u2192 energy via", "3. Convert forces to power to energy via"),
        ("E_next_to_j = P_next_to_kw * 1000 * t_s  # kW \u2192 W, dann * s",
         "E_next_to_j = P_next_to_kw * 1000 * t_s  # kW to W, then * s"),
    ]),
    ("energy/models.py", [
        ('description="Frontal area in m\u00b2 (Tesla Model 3).",',
         'description="Frontal area in m² (Tesla Model 3).",'),  # m² is fine
        ('description="Wirkungsgrad des Elektromotors (\u00b12% Toleranz). Typische Werte: 92-96 %."',
         'description="Efficiency of the electric motor (+/-2% tolerance). Typical values: 92-96%."'),
        ('"(Kettenwirkungsgrad: Rad \u2192 Motor \u2192 Batterie \u2248 75 %)."',
         '"(Chain drive efficiency: wheel to motor to battery ≈ 75 %)."'),
        ('"Full blast \u2248 5-7 kW, typischer Betrieb \u2248 1-4 kW."',
         '"Full blast ≈ 5-7 kW, typical operation ≈ 1-4 kW."'),
        ('description="Untere Komforttemperaturgrenze (\u00b0C). "',
         'description="Lower comfort temperature limit (degrees C). "'),
        ('description="Obere Komforttemperaturgrenze (\u00b0C). "',
         'description="Upper comfort temperature limit (degrees C). "'),
        ("rekuperation_kwh: float  # Betrag der regenerativ gewonnenen energy",
         "rekuperation_kwh: float  # Amount of regenerative energy recovered"),
    ]),
    ("geo/__init__.py", [
        ("Kein Business-Modul im Sinne der Modulgrenzen-Regel", "No business module in the sense of the module-boundary rule"),
    ]),
    ("geo/geo.py", [
        ("dependency-free without", "dependency-free, without"),  # em-dash
        ("[0, 360). `0\u00b0` `90\u00b0`", "[0, 360). 0=N 90=E"),
    ]),
    ("optimization/charging_math.py", [
        ('"""Calculate charge_time in Sekunden f\u00fcr den charging_process',
         '"""Calculate charge_time in seconds for the charging_process'),
        ("Fenster [100-delta, 100], so als w\u00fcrde JEDER", "Window [100-delta, 100], as if EVERY"),
        ("`PrivateAttr`-Zugriff \u00fcber alle Stichproben", "`PrivateAttr` access over all samples"),
        ("Mehrere zu kurze Roh-Kandidaten k\u00f6nnen dabei auf DASSELBE gestreckte",
         "Multiple too-short raw candidates can stretch to the SAME"),
    ]),
    ("optimization/graph_builder.py", [
        # Jönköping and Ödeshög are proper Swedish city names - keep them
    ]),
    ("optimization/models.py", [
        ('"Reserve auf dem Ziel-SoC (z. B. Ziel-SoC = 80%, Reserve = 5% \u2192',
         '"Reserve at target SoC (e.g. target SoC = 80%, reserve = 5% →'),
    ]),
    ("optimization/optimizer.py", [
        ("# Mappe Zwischenstopps auf segmente", "# Map waypoints to segments"),
        ("# Target node: any SoC", "# Target node: any SoC"),  # likely Unicode
    ]),
    ("routing/client.py", [
        ('"""HTTP-Client f\u00fcr GraphHopper API.', '"""HTTP client for GraphHopper API.'),
        ("Authentifizierung, Request/Response Mapping f\u00fcr GraphHopper",
         "authentication, request/response mapping for GraphHopper"),
        ("# Gateway-/Überlast-Status, die GraphHopper", "# Gateway/overload status that GraphHopper"),
        ('"""HTTP-Client f\u00fcr GraphHopper API. Handles Authentifizierung, Request/Response Mapping."""',
         '"""HTTP client for GraphHopper API. Handles authentication, request/response mapping."""'),
        ("api_key: Optionaler API Key f\u00fcr Authentifizierung",
         "        api_key: Optional API key for authentication"),
        ("# grossz\u00fcgiges Read-Timeout.", "# Generous read timeout."),
        ("details: Liste von gew\u00fcnschten Path Details",
         "        details: List of requested path details"),
        ("custom_model: Optionaler custom_model JSON f\u00fcr individuelles vehicle_profile",
         "        custom_model: Optional custom_model JSON for a vehicle profile"),
        ("ValueError: Wenn less als 2 Punkte \u00fcbergeben werden",
         "        ValueError: If less than 2 points are provided"),
        ("# Umwandlung points: (lat, lon) \u2192 [lon, lat]",
         "# Convert points: (lat, lon) to [lon, lat]"),
        ('# GraphHopper erwartet im JSON-POST-Body den Schl\u00fcssel "points" (Plural,',
         '# GraphHopper expects the key "points" (plural) in the JSON POST body,'),
        ("# Fehler f\u00fcr Nutzer (siehe API-Fehlermeldung in api.py) nicht",
         "# Error for the user (see API error message in api.py) not"),
        ("der verbundene Server tats\u00e4chlich unterst\u00fctzt (abh\u00e4ngig von dessen",
         "the connected server actually supports (depending on its"),
        ("GraphHopperRoutingprovider genutzt, um nur unterst\u00fctzte Path-Details",
         "GraphHopperRoutingprovider used to only support path details"),
        ('"""Betritt den async Context-Manager und gibt den Client zur\u00fcck."""',
         '"""Enters the async context manager and returns the client."""'),
    ]),
    ("routing/faehren.py", [
        ("# genau \u00fcbereinstimmt mit der Ferry-Strecke \u2014 verwendet",
         "# exactly matches the ferry run \u2014 used"),
    ]),
    ("routing/models.py", [
        ("Stra\u00dfenklasse (MOTORWAY", "road_class (MOTORWAY"),
        ('"Stra\u00dfenbelag aus GraphHopper Path-Detail `surface`',
         '"Road surface from GraphHopper Path-Detail `surface`'),
        ('"Stra\u00dfen-/F\u00e4hrlinienname aus GraphHopper Path-Detail `street_name`',
         '"Road/ferry route name from GraphHopper Path-Detail `street_name`'),
        ('"Stra\u00dfen-/Autobahnref aus GraphHopper Path-Detail `street_ref`',
         '"Road/highway reference from GraphHopper Path-Detail `street_ref`'),
        ('"dirt), None if unavailable. Used by `energy` for the rolling resistance-',
         '"dirt), None if unavailable. Used by energy for rolling resistance-'),
        ('"Used by ``construction`` to extract Autobahn IDs (A\\d+) per segment "',
         '"Used by construction to extract Autobahn IDs (A\d+) per segment "'),
    ]),
    ("routing/providers/__init__.py", [
        ('"""provider-Schicht f\u00fcr Routing-Anbieter.',
         '"""Provider layer for routing providers.'),
        ("Protokolle und implementationen f\u00fcr Routing-Anbieter (GraphHopper, Fake f\u00fcr Tests).",
         "Protocols and implementations for routing providers (GraphHopper, Fake for tests)."),
    ]),
    ("routing/providers/custom_model.py", [
        ('"""Custom-Model-Bauwerkzeuge f\u00fcr GraphHopper-Routing."""',
         '"""Custom model utilities for GraphHopper routing."""'),
        ("Gibt `None` zur\u00fcck, wenn weder `use_custom_model` (Tempolimit-Profil) noch",
         "Returns `None` if neither `use_custom_model` (speed limit profile) nor"),
    ]),
    ("routing/providers/fake.py", [
        ('"""Fake-Routing-Anbieter f\u00fcr Tests und lokalen Dev-Betrieb."""',
         '"""Fake routing provider for tests and local dev."""'),
        ("sodass der caller diese Teilstrecke automatisch \u00fcberspringt.",
         "so that the caller automatically skips this segment."),
    ]),
    ("routing/providers/graphhopper.py", [
        ("R\u00f6dby (DK) - Puttgarden (D)", "Rödby (DK) - Puttgarden (D)"),  # proper place name
        ("# Umwandlung TripRequest \u2192 GraphHopper Parameter",
         "# Convert TripRequest to GraphHopper parameters"),
        ("# Mapping GraphHopperResponse \u2192 Route", "# Convert GraphHopperResponse to Route"),
    ]),
    ("simulation/models.py", [
        ("Energieverbrauch (kWh) f\u00fcr", "Energy consumption (kWh) for"),
    ]),
    ("simulation/simulate.py", [
        ("H\u00f6henprofil-basiert", "Elevation-profile-based"),
    ]),
    ("trip_input/api.py", [
        ("# \u2500 Supercharger API \u2500", "# Supercharger API"),
        ("# \u2500 Trips API \u2500", "# Trips API"),
        ("Schätzung", "Estimate"),
        ("Strecke", "route"),
        ("betroffenen", "affected"),
        ("Stra\u00dfenstrecke", "road segment"),
        ("Metern", "meters"),
    ]),
    ("trip_input/models.py", [
        ("Frontal area in m\u00b2", "Frontal area in m²"),  # m² is fine Unicode
    ]),
    ("trip_input/pipeline.py", [
        ("deliberately generous (also covers the 20 km distant J\u00f6nk\u00f6ping",
         "deliberately generous (also covers the 20 km distant Jönköping"),  # proper name
    ]),
    ("trip_input/providers_factory.py", [
        ("DE needs no mapping", "DE needs no mapping"),  # likely Unicode
    ]),
    ("trip_input/schemas/request.py", [
        ("Sch\u00e4tzung", "Estimate"),
    ]),
    ("trip_input/schemas/response.py", [
        ("Gesch\u00e4tzte L\u00e4nge der betroffenen Stra\u00dfenstrecke in Metern",
         "Estimated length of the affected road segment in meters"),
    ]),
    ("weather/coverage.py", [
        ("# Danish islands sit east of it (R\u00f8dby", "# Danish islands sit east of it (Rødby"),  # proper name
    ]),
    ("weather/models.py", [
        ('temperature_c: float = Field(ge=-100.0, le=70.0, description="temperature in \u00b0C")',
         'temperature_c: float = Field(ge=-100.0, le=70.0, description="temperature in degrees C")'),
        ('"""temperature in \u00b0C."""', '"""temperature in degrees C."""'),
        ('ge=0.0, le=360.0, description="wind_direction_deg in Grad (0\u00b0 = N, 90\u00b0 = O)"',
         'ge=0.0, le=360.0, description="wind_direction_deg in degrees (0 = N, 90 = E)"'),
        ('"0\u00b0 = N, 90\u00b0 = O, 180\u00b0 = S, 270\u00b0 = W',
         '"0 = N, 90 = E, 180 = S, 270 = W'),
    ]),
    ("weather/providers/_shared.py", [
        ("D\u00e4nemark", "Denmark"),
    ]),
    ("weather/providers/composite.py", [
        ("Modulspezifikationen.md", "Modulspezifikationen.md"),  # doc filename, keep
    ]),
    ("weather/providers/openmeteo.py", [
        ("L\u00e4ndercode", "country code"),
    ]),
    ("weather/providers/smhi.py", [
        ("Schweden", "Sweden"),
    ]),
    ("weather/weather.py", [
        ("Wetterdaten", "weather data"),
    ]),
    ("wind/__init__.py", [
        ("- headwind-/R\u00fcckenwind-Komponente (m/s, positiv = headwind, negativ = R\u00fcckenwind)",
         "- headwind/tailwind component (m/s, positive = headwind, negative = tailwind)"),
    ]),
    ("wind/models.py", [
        ('"""data models f\u00fcr das wind-Modul.',
         '"""Data models for the wind module.'),
        ("gegenwind_ms: headwind-/R\u00fcckenwind-Komponente in m/s.",
         "    gegenwind_ms: headwind/tailwind component in m/s."),
        ("Positiv = headwind (bremsend), negativ = R\u00fcckenwind (unterst\u00fctzend).",
         "    Positive = headwind (braking), negative = tailwind (assisting)."),
        ('description="headwind-/R\u00fcckenwind-Komponente in m/s (positiv=headwind, negativ=R\u00fcckenwind)"',
         '        description="headwind/tailwind component in m/s (positive=headwind, negative=tailwind)"'),
    ]),
    ("wind/wind.py", [
        ("gegenwind_ms: headwind-/R\u00fcckenwind-Komponente in m/s.",
         "    gegenwind_ms: headwind/tailwind component in m/s."),
        ("Bremsend: positiver Wert, Unterst\u00fctzend: negativer Wert",
         "Braking: positive value, Assisting: negative value"),
    ]),
]

def main():
    total = 0
    for filepath, replacements in TASKS:
        count = translate(filepath, replacements)
        if count > 0:
            print(f"  {filepath}: {count} replacements")
        total += count

    # Verify syntax
    errors = []
    for filepath, _ in TASKS:
        try:
            compile((ROOT / filepath).read_text(), filepath, 'exec')
        except SyntaxError as e:
            errors.append(f"{filepath}:{e.lineno}: {e.msg}")

    if errors:
        print(f"\nSyntax errors ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
    else:
        print(f"\nAll modified files: syntax OK")

    # Check remaining
    remaining = 0
    for p in sorted(ROOT.rglob('*.py')):
        if '__pycache__' in str(p):
            continue
        text = p.read_text('utf-8')
        chars = sum(1 for c in text if ord(c) > 127)
        if chars > 0:
            # Count actual German words
            german_words = {'fuer', 'für', 'die', 'den', 'der', 'eine', 'auf', 'und', 'von',
                           'mit', 'aus', 'nach', 'bei', 'das', 'des', 'dem', 'nicht', 'noch',
                           'auch', 'aber', 'denn', 'schon', 'nur', 'sehr', 'mehr', 'alle',
                           'jeder', 'kein', 'dieser', 'diese', 'hier', 'da', 'dann', 'immer',
                           'oft', 'nie', 'vielleicht', 'allerdings', 'jedoch', 'sondern',
                           'natürlich', 'sicher', 'wahrscheinlich', 'schlüssel', 'einfügten',
                           'änderungen', 'ausführt', 'verwaltet', 'station', 'lädt', 'optionales',
                           'liste', 'anzahl', 'preisdaten', 'ersetzt', 'meta', 'rückg',
                           'vollständigen', 'aktualisierung', 'client', 'apik', 'authentifizierung',
                           'gateway', 'überlaststatus', 'warmlaufen', 'liefert', 'optionaler',
                           'details', 'gewünschten', 'individueller', 'werte', 'punkt',
                           'übergeben', 'erwartet', 'postbody', 'fehler', 'nutzer', 'apifehlermeldung',
                           'tatsächlich', 'unterstützt', 'abhängig', 'genutzt', 'unterstützte',
                           'pathdetails', 'betritt', 'contextmanager', 'zurück', 'databank',
                           'laendercodes', 'filterung', 'stationid', 'transaktion', 'stationdicts',
                           'requireden', 'schlüsseln', 'eingefügten', 'stationen', 'implementierenden',
                           'müssen', 'methode', 'implementieren', 'konstruktion', 'route',
                           'zurückgibt', 'entlang', 'angegebenen', 'länder', 'kachelnamen',
                           'gradzelle', 'koordinate', 'baue', 'lesbare', 'uri', 'kachel',
                           'geöffnet', 'fallback', 'luftdichte', 'standard', 'temperatur',
                           'konvertiere', 'kräfte', 'energie', 'strom', 'sekunden', 'rekuperation',
                           'betrage', 'regenerativ', 'gewonnenen', 'mindestens', 'null', 'ladetechnik',
                           'chargingprocess', 'fenster', 'jede', 'teilrechnung', 'partial', 'charge',
                           'low', 'soc', 'proben', 'punkt', 'kandidaten', 'gestreckte',
                           'zwischenstopps', 'segment', 'index', 'target', 'last', 'segment',
                           'data', 'module', 'gegenwind', 'komponente', 'bremsend',
                           'unterstützend', 'rückenwind', 'normalisiert', 'nord', 'ost',
                           'provider', 'schicht', 'routinganbieter', 'protokolle',
                           'implementierungen', 'tests', 'bauwerkzeuge', 'gibt', 'weder',
                           'tempolimitprofil', 'fake', 'lokaler', 'devbetrieb', 'sodass',
                           'caller', 'teststrecke', 'automatisch', 'überspringt',
                           'schätzung', 'strecke', 'betroffenen', 'straßenstrecke', 'metern',
                           'schätzung', 'länge', 'dänenmark', 'schweden', 'wetterdaten'}
            remaining_words = set()
            for word in text.lower().split():
                clean = word.strip('.,;:()[]{}"\'`-_=+!@#$%^&*<>?/~\\')
                if clean in german_words:
                    remaining_words.add(clean)
            if remaining_words:
                print(f"  STILL: {p.relative_to(ROOT)} ({chars} non-ASCII, German words: {remaining_words})")
                remaining += 1
    print(f"\nRemaining files with actual German: {remaining}")
    print(f"\nTotal replacements: {total}")

if __name__ == '__main__':
    main()
