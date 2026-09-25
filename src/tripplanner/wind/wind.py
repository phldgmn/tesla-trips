"""calculation von Wind components entlang einer Route.

Diese Module implementiert die trigonometrische Projektion der Windvektoren
auf die heading (Bearing) eines Route-segments.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from tripplanner.routing.models import RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents


def _degrees_to_radians(deg: float) -> float:
    """Konvertiert Grad in Bogenmaß."""
    return deg * math.pi / 180.0


def compute_wind_components(weather: WeatherSample, segment: RouteSegment) -> WindComponents:
    """Calculatet Wind components für ein einzelnes segment.

    Die Wind components werden mittels Vektorprojektion berechnet:
      The wind vector is shifted by 180° (wind_direction_deg = direction the wind comes from).
    - Die Projektion auf die Bearing-Richtung ergibt den headwind/Rückenwind.
    - Die Projektion auf die senkrechte Richtung ergibt den crosswind.

    Args:
        weather: weatherdaten für den timestamp des segments.
        segment: Route-segment mit Bearing (heading am segmentanfang).

    Returns:
        WindComponents mit segment_index, gegenwind_ms und seitenwind_ms.
    """
    # wind_direction_deg als Vektorrichtung (180° versetzt, da wind_direction_deg
    # in der Meteorologie die Richtung angibt, aus der der Wind kommt)
    wind_dir_vector = (weather.wind_direction_deg + 180.0) % 360.0

    # Bearing des segments (Pflichtfeld, von routing bereits berechnet)
    bearing = segment.bearing_deg

    # Winkelunterschied zwischen Windvektor und Bearing
    delta_theta = bearing - wind_dir_vector
    delta_theta_rad = _degrees_to_radians(delta_theta)

    # Projektion: headwind = cos, crosswind = sin
    v_long = weather.wind_speed_ms * math.cos(delta_theta_rad)
    v_side = weather.wind_speed_ms * math.sin(delta_theta_rad)

    return WindComponents(
        segment_index=segment.segment_index,
        gegenwind_ms=v_long,
        seitenwind_ms=v_side,
    )


def compute_wind_components_for_route(
    weather_samples: Sequence[WeatherSample],
    segments: Sequence[RouteSegment],
) -> list[WindComponents]:
    """Calculatet Wind components für alle segmente entlang der Route.

    Args:
        weather_samples: weatherdaten je weatherabfragepunkt. Muss gleiche Laenge
            wie segments haben.
        segments: Route-segmente (muss gleiche Laenge wie weather_samples haben).

    Returns:
        Liste von WindComponents (segment_index passt zu segment.segment_index).

    Raises:
        ValueError: Wenn weather_samples und segments unterschiedliche Laengen haben.
    """
    if len(weather_samples) != len(segments):
        raise ValueError(
            f"weather_samples ({len(weather_samples)}) and segments ({len(segments)}) "
            "must be the same length."
        )

    return [compute_wind_components(w, s) for w, s in zip(weather_samples, segments, strict=True)]
