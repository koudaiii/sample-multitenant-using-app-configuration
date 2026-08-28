"""Pattern 2: one shared store, tenants separated by label.

The guidance recommends key prefixes over labels for tenancy, because a label
spent on the tenant id is no longer available for versioning or environments.
This pattern is worth choosing mainly when the application is deployed per
tenant, so that a deployment loads exactly one label.
"""

from __future__ import annotations

from mtappconfig.source import TenantConfig


class LabelSource:
    name = "shared-store-label"

    def __init__(self, store) -> None:
        self._store = store

    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            # A None label filter selects only unlabelled settings. That is the
            # service's default, and it is why the tenant read below must pass
            # its label explicitly: without it, no tenant setting is returned.
            shared = self._store.select(key_filter="*", label_filter=None)
            # tenant_id has already been validated by TenantRegistry.resolve.
            tenant = self._store.select(key_filter="*", label_filter=tenant_id)
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)

    def ping(self) -> None:
        self._store.ping()
