"""Provider-Implementierungen für construction-Daten.

Enthält:
- ConstructionProviderConfig: Konfiguration für externe APIs
- ConstructionProviderImpl: Orchestriert DE/DK/SE. DK/SE werden hier direkt
  über DATEX II Feeds implementiert; DE wird an einen injizierbaren
  `ConstructionProvider` delegiert (Default: `DatexIIGermanyConstructionProvider`
  in `providers_de_datexii.py`; `AutobahnConstructionProvider` in
  `providers_de_autobahn.py` bleibt für ein Revert verfügbar).
- FakeConstructionProvider: Fake-Provider für Tests
"""

from __future__ import annotations

from pydantic import BaseModel


class ConstructionProviderConfig(BaseModel):
    """Konfiguration für den ConstructionProviderImpl (DK/SE DATEX-II-Pfad).

    DE benötigt keine Konfiguration hier (siehe `de_provider`-Parameter von
    `ConstructionProviderImpl.__init__`). DK nutzt OAuth2 client_credentials
    Flow über Azure AD (`dk_client_id`/`dk_secret`/`dk_tenant_id`), SE einen
    Trafikverket `authenticationkey` (`tv_api_key`).
    """

    dk_client_id: str | None = None
    dk_secret: str | None = None
    dk_tenant_id: str | None = None
    """Azure AD tenant ID for DK OAuth2 client_credentials flow.

    Obtained from the Dataudveksleren portal when associating the service
    account with the roadworks dataset.  Must be present (non-empty) for
    DK credential checks to pass.
    """
    dk_download_url: str | None = None
    """Per-dataset download URL for the DK DateX2 endpoint.

    Obtained from the Dataudveksleren portal.  Defaults to the standard
    DATEXII_ENDPOINTS[Land.DK] value when unset, since this repo has no
    verified alternative endpoint.
    """
    tv_api_key: str | None = None
    timeout_seconds: float = 30.0
    cache_ttl_seconds: float = 3600.0
    """TTL in seconds for the persistent cache."""
    cache_dir: str | None = None
    """Directory for the SQLite-backed cache database. Defaults to ``.cache``.

    If ``None``, the cache database is placed in the project's default cache
    directory (``.cache/construction_cache.sqlite``).
    """
