"""HTTP-Client fuer die oeffentliche Tesla Locations-API via echtem Safari.

Steuert die bereits laufende Safari-Instanz des Nutzers per AppleScript
(``osascript``), um eine URL in einem Hintergrund-Tab zu laden und deren
Roh-Body auszulesen. Anders als die CDP-basierten nodriver-Clients
(``nodriver.py``, ``human_flow.py``) stiehlt dieser Ansatz nie den
Tastatur-Fokus (kein ``activate``-Kommando, kein neues Fenster - Safari
haengt den Tab an das bereits vorhandene vorderste Fenster an) und laeuft
nicht in einem Docker-Container, dessen Netzwerk-Traffic ueber eine von
Akamai geblockte IP der VM (statt der echten Netzwerk-Identitaet des Mac)
geroutet wuerde.

Empirisch verifiziert (siehe Session-Notizen): identisches Vorgehen umgeht
den Akamai-WAF sowohl fuer die JSON-Locations-API als auch fuer die
Next.js-Preisseite zuverlaessig, waehrend ``frontmost``-Anwendung und
Fenster-Anzahl waehrend des gesamten Vorgangs unveraendert bleiben.

Voraussetzungen auf dem Host:
- Safari muss bereits laufen (wird nicht automatisch gestartet).
- In Safari > Einstellungen > Erweitert: "Menu Entwickler in der Menueleiste
  anzeigen" aktiviert, und im Entwickler-Menue "Apple-Events erlauben"
  angehakt (noetig fuer ``do JavaScript ... in document``).
- macOS fragt beim allerersten Aufruf nach Automatisierungs-Berechtigung
  fuer den Prozess, der ``osascript`` startet (Terminal/ghostty/Python).
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from .common import (
    DEBUG_BODY_PREVIEW_CHARS,
    WAF_RETRY_MAX_ATTEMPTS,
    CurlError,
    TeslaJsonEndpointsMixin,
    _debug_log,
    is_waf_block,
    waf_retry_delay_s,
)

_FIND_DOC_TIMEOUT_S: float = 5.0
"""Max. Wartezeit, bis der neu erzeugte Tab unter seiner Ziel-URL in
``documents`` auffindbar ist (die URL des Dokuments aktualisiert sich nicht
synchron mit ``make new document``)."""

_LOAD_TIMEOUT_S: float = 20.0
"""Max. Wartezeit auf ``document.readyState == "complete"`` im neuen Tab."""

_POLL_INTERVAL_S: float = 0.25
"""Intervall zwischen den Polling-Versuchen (Tab-Suche und Ladezustand)."""

_OSASCRIPT_TIMEOUT_S: float = 45.0
"""Hartes Timeout fuer den gesamten ``osascript``-Subprozess (Sicherheitsnetz
falls Safari/AppleScript haengen bleibt)."""

_CLEANUP_TIMEOUT_S: float = 10.0
"""Timeout fuer den Rueckfall-``osascript``-Aufruf (``_build_cleanup_script``),
der nach einem gekillten Fetch-Versuch verwaiste Safari-Tabs schliesst. Kurz
gehalten, da dies nur ein Aufraeum-Versuch ist und selbst nicht haengen
bleiben soll."""


def _escape_applescript_string(value: str) -> str:
    """Escaped ``value`` fuer die Einbettung in ein AppleScript-String-Literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_fetch_script(url: str) -> str:
    """Baut das AppleScript, das ``url`` in einem Hintergrund-Tab laedt und liest.

    Erzeugt einen neuen Tab im bereits vordersten Safari-Fenster (kein
    ``activate`` -> kein Fokus-Diebstahl), wartet bis die Ziel-URL im
    Dokument sichtbar ist, pollt ``document.readyState`` bis ``"complete"``
    und liest anschliessend den Roh-Body via ``source of``. Der Tab wird in
    jedem Fall (Erfolg wie Fehler) wieder geschlossen: ``createdDoc``
    speichert die von ``make new document`` direkt zurueckgelieferte
    Objekt-Referenz, damit auch dann noch ein gueltiges Handle zum
    Schliessen existiert, wenn der Tab per URL-Abgleich innerhalb von
    ``find_attempts`` nicht wiedergefunden wird (sonst bliebe der Tab
    verwaist offen, siehe ``newDoc is missing value``-Zweig).
    """
    escaped_url = _escape_applescript_string(url)
    find_attempts = max(1, round(_FIND_DOC_TIMEOUT_S / _POLL_INTERVAL_S))
    load_attempts = max(1, round(_LOAD_TIMEOUT_S / _POLL_INTERVAL_S))
    return f"""
set targetURL to "{escaped_url}"
set newDoc to missing value
set pageSource to ""

tell application "Safari"
    set createdDoc to make new document with properties {{URL:targetURL}}
end tell

try
    tell application "Safari"
        repeat {find_attempts} times
            delay {_POLL_INTERVAL_S}
            repeat with doc in documents
                if (URL of doc) is targetURL then
                    set newDoc to doc
                    exit repeat
                end if
            end repeat
            if newDoc is not missing value then exit repeat
        end repeat

        if newDoc is missing value then
            set newDoc to createdDoc
            error "Tab fuer Ziel-URL nicht gefunden (Timeout)"
        end if

        repeat {load_attempts} times
            delay {_POLL_INTERVAL_S}
            try
                if (do JavaScript "document.readyState" in newDoc) is "complete" then
                    exit repeat
                end if
            end try
        end repeat

        set pageSource to source of newDoc
    end tell
on error errMsg
    tell application "Safari"
        if newDoc is not missing value then
            try
                close newDoc
            end try
        end if
    end tell
    error errMsg
end try

tell application "Safari"
    try
        close newDoc
    end try
end tell

return pageSource
""".strip()


def _build_cleanup_script(url: str) -> str:
    """AppleScript, das alle Safari-Tabs mit URL ``url`` schliesst (best effort).

    Rueckfallebene fuer den Fall, dass ein Fetch-Versuch per Timeout
    gekillt wurde (siehe ``_run_applescript``), bevor das Haupt-Skript
    (``_build_fetch_script``) seinen eigenen ``close``-Schritt erreichen
    konnte - verhindert verwaiste Safari-Tabs/-Fenster nach einem
    haengenden ``osascript``-Prozess.
    """
    escaped_url = _escape_applescript_string(url)
    return f"""
tell application "Safari"
    repeat with doc in documents
        try
            if (URL of doc) is "{escaped_url}" then
                close doc
            end if
        end try
    end repeat
end tell
""".strip()


class SafariTeslaClient(TeslaJsonEndpointsMixin):
    """HTTP-Client fuer die oeffentliche Tesla Locations-API via echtem Safari.

    Nutzt die bereits laufende, per Nutzer authentifizierte Safari-Instanz
    (AppleScript/``osascript``), um URLs in einem Hintergrund-Tab zu laden.
    Kein eigener Browser-Prozess, kein Fokus-Diebstahl, keine Container-
    Netzwerk-Isolation - siehe Modul-Docstring fuer Details.

    Die drei oeffentlichen Endpunkte (fetch_locations, fetch_location_details,
    fetch_pricing_html) sowie deren Orchestrierung (fetch_all_supercharger_details)
    sind in ``TeslaJsonEndpointsMixin`` geteilt; diese Klasse liefert nur den
    Safari-Transport (``_fetch``).

    Usage:
        client = SafariTeslaClient()
        details = await client.fetch_all_supercharger_details("DE")
        await client.close()
    """

    CurlError = CurlError
    """Alias auf den modulweiten ``CurlError`` (siehe ``common.py``)."""

    def __init__(
        self,
        rate_limit_delay_s: float = 0.5,
        debug_log: Path | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            rate_limit_delay_s: Delay in seconds between detail requests.
            debug_log: Optional file path for debug logging (requests,
                responses, errors).
        """
        self._delay: float = rate_limit_delay_s
        self._debug_log: Path | None = debug_log
        # Serialisiert konkurrierende _fetch-Aufrufe: die Tab-Suche geschieht
        # per URL-Abgleich ueber alle offenen Safari-Dokumente, was bei
        # zeitgleichen Requests auf dieselbe URL zu Verwechslungen fuehren
        # koennte.
        self._lock: asyncio.Lock = asyncio.Lock()

    async def _log_request(self, url: str) -> None:
        """Loggt eine Anfrage fuer Debug-Zwecke."""
        _debug_log(self._debug_log, f"GET {url}", label="SAFARI")

    async def _run_applescript(self, script: str, *, cleanup_url: str | None = None) -> str:
        """Fuehrt ``script`` per ``osascript`` aus und liefert dessen Stdout.

        Args:
            script: Auszufuehrendes AppleScript.
            cleanup_url: Wenn gesetzt, wird bei einem ``osascript``-Timeout
                (der Prozess wird gekillt, bevor ``script`` seinen eigenen
                ``close``-Schritt erreichen konnte) versucht, verwaiste
                Safari-Tabs fuer diese URL per Rueckfall-Skript zu schliessen
                (siehe ``_build_cleanup_script``). Best effort, unterdrueckt
                eigene Fehler.

        Raises:
            CurlError: Bei Timeout, Nicht-Null-Exitcode (AppleScript-Fehler,
                z.B. Safari nicht erreichbar oder Apple-Events nicht erlaubt)
                oder wenn ``osascript`` selbst nicht gestartet werden kann.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            raise self.CurlError(f"osascript konnte nicht gestartet werden: {e}") from e

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_OSASCRIPT_TIMEOUT_S
            )
        except TimeoutError as e:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            if cleanup_url is not None:
                await self._cleanup_orphaned_tab(cleanup_url)
            raise self.CurlError(f"Safari-Automation Timeout nach {_OSASCRIPT_TIMEOUT_S}s") from e

        if proc.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise self.CurlError(
                "Safari-Automation fehlgeschlagen (laeuft Safari? sind Apple-Events "
                f"erlaubt?): {message[:300]}"
            )

        return stdout.decode("utf-8", errors="replace")

    async def _cleanup_orphaned_tab(self, url: str) -> None:
        """Schliesst best effort alle Safari-Tabs mit URL ``url``.

        Rueckfallebene, die ausschliesslich nach einem ``osascript``-Timeout
        greift (siehe ``_run_applescript``): dort wurde der ``osascript``-
        Prozess gekillt, bevor das eigentliche Fetch-Skript seinen eigenen
        ``close newDoc``-Schritt erreichen konnte, sodass der von ``make new
        document`` erzeugte Tab sonst verwaist im Safari-Fenster des Nutzers
        haengen bliebe. Fehler hier werden nur geloggt, nie propagiert, damit
        ein fehlgeschlagener Aufraeum-Versuch nie den urspruenglichen Fehler
        (den Timeout selbst) verdeckt.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                _build_cleanup_script(url),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=_CLEANUP_TIMEOUT_S)
        except Exception as e:
            _debug_log(
                self._debug_log,
                f"Safari-Tab-Cleanup fehlgeschlagen fuer {url}: {e}",
                label="SAFARI",
            )

    async def _fetch_once(self, url: str) -> str:
        """Ein einzelner Versuch: Tab oeffnen, laden, Body lesen, schliessen."""
        async with self._lock:
            await self._log_request(url)
            return await self._run_applescript(_build_fetch_script(url), cleanup_url=url)

    async def _fetch(self, url: str) -> str:
        """Fuehrt GET aus und liefert den Response-Body als Text.

        Wiederholt bei Akamai-WAF-Bloecken, leeren Antworten oder
        Automation-Fehlern bis zu ``WAF_RETRY_MAX_ATTEMPTS`` mal mit
        exponentiellem Backoff (siehe ``waf_retry_delay_s``).

        Args:
            url: Vollstaendige URL mit Query-Parametern

        Returns:
            Response-Body als Text

        Raises:
            CurlError: Bei anhaltenden WAF-Bloecken, leeren Antworten oder
                Safari-Automation-Fehlern
        """
        last_error = self.CurlError("kein Versuch unternommen")
        for attempt in range(1, WAF_RETRY_MAX_ATTEMPTS + 1):
            try:
                body = await self._fetch_once(url)
            except self.CurlError as e:
                last_error = e
                body = None

            if body is not None:
                _debug_log(
                    self._debug_log,
                    f"SAFARI GET {url} -> {len(body)} bytes\n"
                    f"  Body ({len(body)} bytes): {body[:DEBUG_BODY_PREVIEW_CHARS]}",
                    label="SAFARI",
                )
                if is_waf_block(body):
                    last_error = self.CurlError("Tesla API: WAF-Block (Access Denied)")
                elif not body.strip():
                    last_error = self.CurlError("empty response")
                else:
                    return body

            if attempt < WAF_RETRY_MAX_ATTEMPTS:
                await asyncio.sleep(waf_retry_delay_s(attempt))

        raise last_error

    async def close(self) -> None:
        """No-op.

        Safari ist die eigenstaendige Nutzer-Instanz und wird von diesem
        Client weder gestartet noch beendet.
        """
        return None
