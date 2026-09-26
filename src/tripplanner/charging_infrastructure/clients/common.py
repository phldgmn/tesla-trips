"""Common foundations: debug log, CurlError, TeslaClient protocol."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable
from urllib.parse import quote

_logger = logging.getLogger(__name__)

DEBUG_BODY_PREVIEW_CHARS: int = 200
"""Maximum response-body characters included in scrape debug lines."""

def default_debug_log_path() -> Path:
    """Default file for ``--debug`` scrape logs: ``$TRIPPLANNER_CACHE_DIR/logs/``.

    Lives under the git-ignored cache directory so raw WAF tokens and request
    URLs never end up in the working tree root.
    """
    log_dir = Path(os.environ.get("TRIPPLANNER_CACHE_DIR", ".cache")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "charger_debug.log"

def _debug_log(log_path: Path | None, msg: str, label: str = "DEBUG") -> None:
    """Emits a detailed scrape debug line via the standard logger and, optionally, a file.

    Always logged at DEBUG level through the ``tripplanner`` logger namespace
    (see `trip_input.app._configure_logging`, `TRIPPLANNER_LOG_LEVEL`), so
    Tesla crawling/scraping triggered from the frontend (which never sets
    `log_path`) is still fully visible whenever DEBUG logging is enabled -
    not only when a CLI caller explicitly opts into a debug log file.

    Args:
        log_path: Optional additional file path to append the line to.
        msg: The message
        label: Label for the line (e.g. "CURL", "HTTP", "JSON")
    """
    _logger.debug("[%s] %s", label, msg)
    if log_path is None:
        return
    timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{label}] {msg}\n")
    except OSError:
        pass

_WAF_BLOCK_MARKERS: tuple[str, ...] = ("Access Denied", "errors.edgesuite.net")
"""Substrings identifying an Akamai edge WAF block page.

Tesla's WAF sometimes returns this block page with HTTP 200 (not 403/429) -
e.g. for ``get-locations``, which is otherwise expected to return JSON. A
bare status-code check therefore misses these blocks; callers must inspect
the body via ``is_waf_block`` before treating a 200 response as success.
"""

WAF_RETRY_MAX_ATTEMPTS: int = 4
"""Total number of attempts on WAF block/rate-limit before giving up
(verified pattern: new browser/session fingerprint per attempt bypasses
Akamai more reliably than a simple retry)."""

WAF_RETRY_BASE_DELAY_S: float = 1.5
"""Base delay (seconds) for exponential backoff between retry attempts
(see ``waf_retry_delay_s``)."""

def is_waf_block(body: str) -> bool:
    """True if ``body`` looks like an Akamai WAF block page.

    Args:
        body: Response body (text)

    Returns:
        True if a known block marker is found in the body.
    """
    return any(marker in body for marker in _WAF_BLOCK_MARKERS)

def waf_retry_delay_s(attempt: int) -> float:
    """Exponential backoff delay before retry attempt ``attempt``.

    Args:
        attempt: 1-indexed number of the just-failed attempt.

    Returns:
        Delay in seconds before the next attempt.
    """
    return float(WAF_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))
