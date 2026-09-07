"""Lay the canonical sample data out the way this pattern expects it."""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS

from source_key_prefix import SHARED_PREFIX


def build_store() -> FakeAppConfigurationStore:
    store = FakeAppConfigurationStore(name="shared-store")
    for key, value in SHARED_SETTINGS.items():
        store.set(f"{SHARED_PREFIX}{key}", value)
    for tenant_id, settings in TENANT_SETTINGS.items():
        for key, value in settings.items():
            store.set(f"{tenant_id}/{key}", value)
    return store
