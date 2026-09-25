"""Discretization functions for SoC and time.

Hilfsfunktionen zur Umrechnung zwischen kontinuierlichen Werten und
diskreten Buckets für A*/Dijkstra-Suche im Zustandsraum.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Default-Diskretisierungsparameter
SOC_STEP_PCT_DEFAULT: float = 1.0
"""Standard step size for SoC discretization in percentage (1%)."""

TIME_STEP_MIN_DEFAULT: int = 15
"""Standard step size for time discretization in minutes (15 min)."""


def soc_to_bucket(soc_pct: float, soc_step_pct: float = SOC_STEP_PCT_DEFAULT) -> int:
    """Konvertiert kontinuierlichen SoC-Wert in diskreten Bucket-Index.

    Args:
        soc_pct: SoC-Wert in Prozent (0.0-100.0).
        soc_step_pct: Schrittweite für Diskretisierung in Prozent.

    Returns:
        Bucket-Index (0 = 0%, 100 = 100% bei 1%-Schritten).

    Example:
        >>> soc_to_bucket(50.5)  # mit default 1%
        50
        >>> soc_to_bucket(50.5, soc_step_pct=0.5)
        101
    """
    max_soc_pct = 100.0
    if soc_pct < 0.0 or soc_pct > max_soc_pct:
        raise ValueError(f"SoC muss im Bereich [0, 100] liegen, ist aber {soc_pct}")
    if soc_step_pct <= 0.0:
        raise ValueError(f"soc_step_pct muss positiv sein, ist aber {soc_step_pct}")

    return round(soc_pct / soc_step_pct)


def bucket_to_soc(bucket: int, soc_step_pct: float = SOC_STEP_PCT_DEFAULT) -> float:
    """Konvertiert Bucket-Index in mittleren kontinuierlichen SoC-Wert.

    Args:
        bucket: Bucket-Index.
        soc_step_pct: Schrittweite für Diskretisierung in Prozent.

    Returns:
        Mittlerer SoC-Wert des Buckets in Prozent.

    Example:
        >>> bucket_to_soc(50)  # mit default 1%
        50.0
        >>> bucket_to_soc(101, soc_step_pct=0.5)
        50.5
    """
    if bucket < 0:
        raise ValueError(f"Bucket-Index muss nicht-negativ sein, ist aber {bucket}")
    if soc_step_pct <= 0.0:
        raise ValueError(f"soc_step_pct muss positiv sein, ist aber {soc_step_pct}")

    return bucket * soc_step_pct


def time_to_bucket(
    timestamp: datetime,
    base_time: datetime,
    time_step_min: int = TIME_STEP_MIN_DEFAULT,
) -> int:
    """Konvertiert datetime-timestamp in diskreten time-Bucket-Index.

    Die time wird relativ zur Startzeit berechnet (Rundung auf volle Viertelstunde).

    Args:
        timestamp: timestamp als datetime-Objekt.
        base_time: Basiszeit (Startzeit der Reise).
        time_step_min: step size for time discretization in minutes.

    Returns:
        time-Bucket-Index (0 = Startzeit, 1 = Startzeit + 15 min, etc.).

    Example:
        >>> start = datetime(2025, 1, 1, 8, 0, 0)
        >>> time_to_bucket(datetime(2025, 1, 1, 8, 17, 0), start)
        1
        >>> time_to_bucket(datetime(2025, 1, 1, 8, 45, 0), start)
        3
    """
    if time_step_min <= 0:
        raise ValueError(f"time_step_min muss positiv sein, ist aber {time_step_min}")

    # calculate Zeitdifferenz zur Basiszeit
    delta = timestamp - base_time
    delta_minutes = int(delta.total_seconds() / 60)

    # Runde auf volle time_step_min
    time_step_minutes = time_step_min
    rounded_minutes = round(delta_minutes / time_step_minutes) * time_step_minutes

    # Konvertiere zu Bucket (1 Bucket = time_step_min Minuten)
    return rounded_minutes // time_step_minutes


def bucket_to_time(
    bucket: int, base_time: datetime, time_step_min: int = TIME_STEP_MIN_DEFAULT
) -> datetime:
    """Konvertiert time-Bucket-Index in datetime-timestamp.

    Args:
        bucket: time-Bucket-Index.
        base_time: Basiszeit (Startzeit der Reise).
        time_step_min: step size for time discretization in minutes.

    Returns:
        timestamp als datetime-Objekt.

    Example:
        >>> base = datetime(2025, 1, 1, 8, 0, 0)
        >>> bucket_to_time(1, base)
        datetime.datetime(2025, 1, 1, 8, 15)
        >>> bucket_to_time(3, base)
        datetime.datetime(2025, 1, 1, 9, 0)
    """
    if bucket < 0:
        raise ValueError(f"Bucket-Index muss nicht-negativ sein, ist aber {bucket}")
    if time_step_min <= 0:
        raise ValueError(f"time_step_min muss positiv sein, ist aber {time_step_min}")

    delta = timedelta(minutes=bucket * time_step_min)
    return base_time + delta


def create_state_node(
    segment_index: int,
    soc_pct: float,
    timestamp: datetime,
    soc_step_pct: float = SOC_STEP_PCT_DEFAULT,
) -> tuple[int, int, int]:
    """Create a StateNode tuple for NetworkX graphs.

    Args:
        segment_index: Index des Route-segments.
        soc_pct: SoC-Wert in Prozent.
        timestamp: timestamp als datetime.
        soc_step_pct: Schrittweite für SoC-Diskretisierung.

    Returns:
        Tuple (segment_index, soc_bucket, time_bucket).
    """
    soc_bucket = soc_to_bucket(soc_pct, soc_step_pct)
    time_bucket = time_to_bucket(timestamp, timestamp, TIME_STEP_MIN_DEFAULT)
    return (segment_index, soc_bucket, time_bucket)


def get_all_soc_buckets(soc_step_pct: float = SOC_STEP_PCT_DEFAULT) -> list[int]:
    """Create list of all possible SoC buckets (0 to 100).

    Args:
        soc_step_pct: Schrittweite für Diskretisierung in Prozent.

    Returns:
        Liste aller Bucket-Indizes.

    Example:
        >>> get_all_soc_buckets(20)
        [0, 1, 2, 3, 4, 5]
        >>> get_all_soc_buckets(1)
        [0, 1, 2, ..., 100]
    """
    num_buckets = round(100.0 / soc_step_pct) + 1
    return list(range(num_buckets))


def get_all_time_buckets_for_duration(
    duration_s: float,
    time_step_min: int = TIME_STEP_MIN_DEFAULT,
) -> list[int]:
    """Create list of all time buckets for a given duration.

    Args:
        duration_s: duration in Sekunden.
        time_step_min: step size for time discretization in minutes.

    Returns:
        Liste aller time-Bucket-Indizes.
    """
    duration_min = duration_s / 60.0
    num_buckets = round(duration_min / time_step_min) + 1
    return list(range(num_buckets))
