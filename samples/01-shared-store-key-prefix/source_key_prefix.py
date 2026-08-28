"""Pattern 1: one shared store, tenants separated by a key prefix.

This is the arrangement the guidance recommends by default. Every tenant's
settings live in the same store under a `<tenant-id>/` prefix, and the prefix
is trimmed on load so the application always sees the same key names.
"""

from __future__ import annotations

from mtappconfig.source import TenantConfig

# Global settings live under their own prefix so they never collide with a
# tenant prefix. Keeping them in one place is the guidance's point about
# shared settings: one value, one place to update.
SHARED_PREFIX = "shared/"


class KeyPrefixSource:
    name = "shared-store-key-prefix"

    def __init__(self, store) -> None:
        self._store = store

    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            shared = self._store.select(
                key_filter=f"{SHARED_PREFIX}*",
                trim_prefixes=[SHARED_PREFIX],
            )
            # The tenant id reaching this line has already been validated by
            # TenantRegistry.resolve. An unvalidated id here would let a caller
            # pass "*" and read every tenant's settings at once.
            tenant = self._store.select(
                key_filter=f"{tenant_id}/*",
                trim_prefixes=[f"{tenant_id}/"],
            )
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)

    def ping(self) -> None:
        self._store.ping()
