"""HTTP-Client für die supercharge.info REST-API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .common import _debug_log


class SuperchargeInfoClient:
    """HTTP-Client für die supercharge.info REST-API.

    Die API ist öffentlich, benötigt keinen API-Key und blockiert keine
    einfachen HTTP-Clients (kein WAF). Dokumentierte Endpunkte:
    - /service/supercharge/allSites   -> vollstaendiger Datensatz
    - /service/supercharge/databaseInfo -> Aenderungs-Timestamp
    - /service/supercharge/allChanges  -> Delta-Aenderungen
    """

    BASE_URL: str = "https://supercharge.info/service/supercharge"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        debug_log: Path | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            client: Optional httpx.AsyncClient instance. If None, a new client
                with 30s timeout is created.
            debug_log: Optional file path for request/response debug logging.
        """
        if client is None:
            self._client: httpx.AsyncClient = httpx.AsyncClient(timeout=30.0)
            self._owns_client: bool = True
        else:
            self._client = client
            self._owns_client = False
        self._debug_log = debug_log

    async def _log_response(self, response: httpx.Response, label: str) -> None:
        """Loggt eine HTTP-Antwort fuer Debug-Zwecke."""
        if self._debug_log is None:
            return
        body = response.text[:2000]
        _debug_log(
            self._debug_log,
            f"[{label}] {response.request.method} "
            f"{response.url} -> {response.status_code}\n"
            f"  Response headers: {dict(response.headers)}\n"
            f"  Body ({len(response.content)} bytes): {body}",
            label="HTTP",
        )

    async def fetch_all_sites(self) -> list[dict[str, Any]]:
        """Fetch all supercharger sites.

        GET {BASE_URL}/allSites

        Returns:
            List of site dictionaries.

        Raises:
            httpx.HTTPStatusError: If response status is not 200.
        """
        response = await self._client.get(f"{self.BASE_URL}/allSites")
        await self._log_response(response, "allSites")
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def fetch_database_info(self) -> dict[str, Any]:
        """Fetch database information including last modification timestamp.

        GET {BASE_URL}/databaseInfo

        Returns:
            Dictionary with lastModified (ms timestamp) and lastModifiedString.
        """
        response = await self._client.get(f"{self.BASE_URL}/databaseInfo")
        await self._log_response(response, "databaseInfo")
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def fetch_all_changes(self) -> list[dict[str, Any]]:
        """Fetch all changes since last sync.

        GET {BASE_URL}/allChanges

        Returns:
            List of change dictionaries.
        """
        response = await self._client.get(f"{self.BASE_URL}/allChanges")
        await self._log_response(response, "allChanges")
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()