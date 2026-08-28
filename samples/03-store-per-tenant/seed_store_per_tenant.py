"""Lay the canonical sample data out the way this pattern expects it."""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS


def build_stores() -> tuple[FakeAppConfigurationStore, dict[str, FakeAppConfigurationStore]]:
    shared = FakeAppConfigurationStore(name="shared-store")
    for key, value in SHARED_SETTINGS.items():
        shared.set(key, value)

    tenant_stores: dict[str, FakeAppConfigurationStore] = {}
    for tenant_id, settings in TENANT_SETTINGS.items():
        store = FakeAppConfigurationStore(name=f"store-{tenant_id}")
        for key, value in settings.items():
            store.set(key, value)
        tenant_stores[tenant_id] = store

    return shared, tenant_stores
