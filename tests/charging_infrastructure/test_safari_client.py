"""Tests für ``SafariTeslaClient`` (AppleScript/osascript-Transport)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tripplanner.charging_infrastructure.client import CurlError, SafariTeslaClient
from tripplanner.charging_infrastructure.clients.safari import (
    _build_fetch_script,
    _escape_applescript_string,
)


class _FakeProcess:
    """Minimaler ``asyncio.subprocess.Process``-Stub fuer Unit-Tests.

    Liefert konfigurierbare (stdout, stderr, returncode)-Tripel pro Aufruf,
    ohne einen echten ``osascript``-Subprozess zu starten.
    """

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            # Haengt bewusst per nie-aufloesendem Future statt asyncio.sleep,
            # damit ein in denselben Tests gemocktes asyncio.sleep (fuer den
            # WAF-Retry-Backoff) diese Simulation nicht aushebelt.
            await asyncio.get_running_loop().create_future()
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        self.waited = True


def _patch_subprocess(process: _FakeProcess) -> AsyncMock:
    """Patcht ``asyncio.create_subprocess_exec`` zum Liefern von ``process``."""
    return AsyncMock(return_value=process)


class TestFetchScriptBuilder:
    """Tests für die AppleScript-Vorlage (kein echtes Safari noetig)."""

    def test_escapes_backslash_and_quote(self) -> None:
        assert _escape_applescript_string('a"b\\c') == 'a\\"b\\\\c'

    def test_build_fetch_script_embeds_url_and_no_activate(self) -> None:
        script = _build_fetch_script("https://www.tesla.com/api/findus/get-locations?country=SE")
        assert 'set targetURL to "https://www.tesla.com/api/findus/get-locations?country=SE"' in (
            script
        )
        # Kein "activate"-Kommando -> kein Fokus-Diebstahl.
        assert "activate" not in script
        assert "make new document" in script
        assert "close newDoc" in script


class TestSafariTeslaClientFetch:
    """Tests für ``_fetch``/``_run_applescript`` mit gemocktem Subprozess."""

    @pytest.mark.asyncio
    async def test_fetch_returns_stdout_on_success(self) -> None:
        process = _FakeProcess(stdout=b'{"data": {"data": []}}', returncode=0)
        client = SafariTeslaClient()
        with patch("asyncio.create_subprocess_exec", _patch_subprocess(process)):
            body = await client._fetch("https://www.tesla.com/api/findus/get-locations")
        assert body == '{"data": {"data": []}}'

    @pytest.mark.asyncio
    async def test_fetch_json_parses_locations(self) -> None:
        payload: dict[str, Any] = {"data": {"data": [{"uuid": "1"}]}}
        process = _FakeProcess(stdout=json.dumps(payload).encode(), returncode=0)
        client = SafariTeslaClient()
        with patch("asyncio.create_subprocess_exec", _patch_subprocess(process)):
            locations = await client.fetch_locations("SE")
        assert locations == [{"uuid": "1"}]

    @pytest.mark.asyncio
    async def test_fetch_pricing_html_returns_raw_body(self) -> None:
        html = "<html>__NEXT_DATA__</html>"
        process = _FakeProcess(stdout=html.encode(), returncode=0)
        client = SafariTeslaClient()
        with patch("asyncio.create_subprocess_exec", _patch_subprocess(process)):
            body = await client.fetch_pricing_html("berlinsupercharger")
        assert body == html

    @pytest.mark.asyncio
    async def test_waf_block_retries_then_raises(self) -> None:
        process = _FakeProcess(stdout=b"Access Denied", returncode=0)
        client = SafariTeslaClient()
        with (
            patch("asyncio.create_subprocess_exec", _patch_subprocess(process)) as exec_mock,
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(CurlError, match="WAF-Block"),
        ):
            await client._fetch("https://www.tesla.com/api/findus/get-locations")
        assert exec_mock.call_count == 4  # WAF_RETRY_MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_empty_response_retries_then_raises(self) -> None:
        process = _FakeProcess(stdout=b"   ", returncode=0)
        client = SafariTeslaClient()
        with (
            patch("asyncio.create_subprocess_exec", _patch_subprocess(process)),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(CurlError, match="empty response"),
        ):
            await client._fetch("https://www.tesla.com/api/findus/get-locations")

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_curl_error_with_stderr(self) -> None:
        process = _FakeProcess(stdout=b"", stderr=b"Safari nicht erreichbar", returncode=1)
        client = SafariTeslaClient()
        with (
            patch("asyncio.create_subprocess_exec", _patch_subprocess(process)),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(CurlError, match="Safari nicht erreichbar"),
        ):
            await client._fetch("https://www.tesla.com/api/findus/get-locations")

    @pytest.mark.asyncio
    async def test_timeout_kills_process_and_raises(self) -> None:
        process = _FakeProcess(hang=True)
        client = SafariTeslaClient()
        with (
            patch("asyncio.create_subprocess_exec", _patch_subprocess(process)),
            patch(
                "tripplanner.charging_infrastructure.clients.safari._OSASCRIPT_TIMEOUT_S",
                0.01,
            ),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(CurlError, match="Timeout"),
        ):
            await client._fetch("https://www.tesla.com/api/findus/get-locations")
        assert process.killed

    @pytest.mark.asyncio
    async def test_osascript_not_found_raises_curl_error(self) -> None:
        client = SafariTeslaClient()
        with (
            patch(
                "asyncio.create_subprocess_exec",
                AsyncMock(side_effect=OSError("no such file")),
            ),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(CurlError, match="osascript konnte nicht gestartet werden"),
        ):
            await client._fetch("https://www.tesla.com/api/findus/get-locations")


class TestSafariTeslaClientLifecycle:
    """Tests fuer Konstruktion und Cleanup."""

    def test_construction_defaults(self) -> None:
        client = SafariTeslaClient()
        assert client._delay == 0.5
        assert client._debug_log is None

    def test_construction_respects_custom_args(self) -> None:
        client = SafariTeslaClient(rate_limit_delay_s=1.5)
        assert client._delay == 1.5

    @pytest.mark.asyncio
    async def test_close_is_noop_and_leaves_safari_running(self) -> None:
        client = SafariTeslaClient()
        with patch("asyncio.create_subprocess_exec") as exec_mock:
            await client.close()
        exec_mock.assert_not_called()
