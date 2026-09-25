"""Custom-Model-Bauwerkzeuge für GraphHopper-Routing."""

from tripplanner.trip_input.models import FerryExclusion, TripRequest


def ferry_exclusion_to_geojson_feature(ausschluss: FerryExclusion) -> dict[str, object]:
    """Baut ein rechteckiges GeoJSON `Polygon`-Feature aus einer gepufferten Bounding Box.

    GeoJSON-Koordinaten sind `[lon, lat]` (Umwandlung von der projektweiten
    `(lat, lon)`-Konvention an dieser externen Serialisierungsgrenze - eine der
    drei dokumentierten GeoJSON-Konversionsstellen des Projekts).
    """
    sw_lat, sw_lon = ausschluss.bbox_sw
    no_lat, no_lon = ausschluss.bbox_ne
    ring = [
        [sw_lon, sw_lat],
        [no_lon, sw_lat],
        [no_lon, no_lat],
        [sw_lon, no_lat],
        [sw_lon, sw_lat],
    ]
    return {
        "type": "Feature",
        "properties": {"name": ausschluss.name},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def build_custom_model(use_custom_model: bool, anfrage: TripRequest) -> dict[str, object] | None:
    """Baut das optionale GraphHopper `custom_model` aus Tempolimit-, Faehr- und Autobahnpraeferenz.

    Gibt `None` zurück, wenn weder `use_custom_model` (Tempolimit-Profil) noch
    Faehrvermeidung (`anfrage.avoid_all_ferries`/`anfrage.avoided_ferries`)
    noch Autobahnpraeferenz (`anfrage.highway_preference`) angefordert wurde -
    identisch zum bisherigen Verhalten ohne benutzerdefiniertes model (kein
    custom_model-Feld im GraphHopper-Request).
    """
    priority: list[dict[str, object]] = []
    speed: list[dict[str, object]] | None = None
    distance_influence: float | None = None

    if use_custom_model:
        speed = [
            {"if": "road_class == MOTORWAY", "limit_to": 130},
            {"if": "true", "limit_to": 100},
        ]
        priority.append({"if": "road_class == MOTORWAY", "multiply_by": 1.0})
        distance_influence = 0.0

    if anfrage.avoid_all_ferries:
        priority.append({"if": "road_environment == FERRY", "multiply_by": 0.0})

    autobahn_multiplier = {"low": 1.1, "medium": 1.2, "high": 1.3}.get(anfrage.highway_preference)
    if autobahn_multiplier is not None:
        priority.append({"if": "road_class == MOTORWAY", "multiply_by": autobahn_multiplier})

    areas: dict[str, object] = {}
    for index, ausschluss in enumerate(anfrage.avoided_ferries):
        area_id = f"faehre_{index}"
        areas[area_id] = ferry_exclusion_to_geojson_feature(ausschluss)
        priority.append({"if": f"in_{area_id} && road_environment == FERRY", "multiply_by": 0.0})

    if not priority and speed is None:
        return None

    custom_model: dict[str, object] = {}
    if speed is not None:
        custom_model["speed"] = speed
    if priority:
        custom_model["priority"] = priority
    if areas:
        custom_model["areas"] = areas
    if distance_influence is not None:
        custom_model["distance_influence"] = distance_influence

    return custom_model
