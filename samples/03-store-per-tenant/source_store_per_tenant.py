"""Pattern 3: one App Configuration store per tenant.

Access permissions on App Configuration are granted at the store level, so
separating tenants into separate stores is what makes separate permissions —
and separate customer-managed keys — possible. The guidance names exactly
those two situations as the reasons to choose this pattern.

Global settings still live in one shared store, so that a change to a global
value is made in one place.
"""

from __future__ import annotations

from collections.abc import Mapping

from mtappconfig.source import ConfigStoreUnavailableError, TenantConfig
from mtappconfig.tenants import TenantRegistry


class StorePerTenantSource:
    name = "store-per-tenant"

    def __init__(self, shared_store, tenant_stores: Mapping[str, object]) -> None:
        self._shared_store = shared_store
        self._tenant_stores = dict(tenant_stores)

    def validate_coverage(self, registry: TenantRegistry) -> None:
        """Fail at startup if any registered tenant has no store.

        Without this, a misconfigured deployment looks healthy until the first
        request for the affected tenant arrives.
        """
        missing = [t.tenant_id for t in registry if t.tenant_id not in self._tenant_stores]
        if missing:
            raise ValueError(f"no configuration store for tenants: {', '.join(missing)}")

    def load(self, tenant_id: str) -> TenantConfig:
        store = self._store_for(tenant_id)

        def reload() -> dict[str, str]:
            shared = self._shared_store.select(key_filter="*")
            # No prefix and no label are needed: the store itself is the
            # boundary, which is what makes the isolation strong here.
            tenant = store.select(key_filter="*")
            return {**shared, **tenant}

        def close() -> None:
            store.close(key_filter="*")

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload, close=close)

    def ping(self) -> None:
        """Readiness covers the shared store only.

        Every tenant needs the shared store, so losing it is a deployment-wide
        problem. A single tenant's store is not: that tenant keeps being served
        from cache, and failing readiness here would pull this instance out of
        the load balancer for every *other* tenant too — the opposite of the
        isolation this pattern exists to provide.
        """
        self._shared_store.ping()

    def _store_for(self, tenant_id: str):
        try:
            return self._tenant_stores[tenant_id]
        except KeyError:
            raise ConfigStoreUnavailableError(
                f"no configuration store for tenant {tenant_id!r}"
            ) from None
