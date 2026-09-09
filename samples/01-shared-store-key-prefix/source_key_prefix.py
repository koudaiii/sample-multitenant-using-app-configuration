"""Pattern 1: one shared store, tenants separated by a key prefix.

This is the arrangement the guidance recommends by default. Every tenant's
settings live in the same store under a `<tenant-id>/` prefix, and the prefix
is trimmed on load so the application always sees the same key names.
"""

from __future__ import annotations

from mtappconfig.source import TenantConfig, merge_config_values

# Global settings live under their own prefix. The leading underscore is
# load-bearing: a tenant id must match \A[a-z0-9][a-z0-9-]{1,30}[a-z0-9]\Z,
# so it can never begin with "_" and can never collide with this namespace.
# A prefix like "shared/" would be a valid tenant id, and registering a tenant
# named "shared" would silently overwrite every tenant's global settings.
# Keeping global settings in one place is the guidance's point about shared
# settings: one value, one place to update.
SHARED_PREFIX = "_shared/"


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
            return merge_config_values(shared, tenant)

        def close() -> None:
            self._store.close(
                key_filter=f"{tenant_id}/*",
                trim_prefixes=[f"{tenant_id}/"],
            )

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload, close=close)

    def ping(self) -> None:
        self._store.ping()
