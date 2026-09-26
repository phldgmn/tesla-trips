"""provider implementations for construction data.

Enthaelt:
- `ConstructionProviderConfig`: configuration for external APIs
- ConstructionproviderImpl: Orchestriert DE/DK/SE. DK/SE werden hier direkt
  implemented via DATEX II feeds; DE is delegated to an injectable
  `Constructionprovider` delegiert (Default: `DatexIIGermanyConstructionprovider`
  in `providers_de_datexii.py`; `AutobahnConstructionprovider` in
  ``providers_de_autobahn.py`` remains available for a revert).
- `FakeConstructionProvider`: Fake provider for tests
"""

from __future__ import annotations

from pydantic import BaseModel


class ConstructionProviderConfig(BaseModel):
    """Configuration for the `ConstructionProviderImpl` (DK/SE DATEX-II path).

    DE requires no configuration here (see the ``de_provider`` parameter of
    `ConstructionproviderImpl.__init__`). DK nutzt OAuth2 client_credentials
    flow via Azure AD (`dk_client_id`/`dk_secret`/`dk_tenant_id`), SE a
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
