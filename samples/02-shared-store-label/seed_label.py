"""Lay the canonical sample data out the way this pattern expects it."""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS


def build_store() -> FakeAppConfigurationStore:
    store = FakeAppConfigurationStore(name="shared-store")
    # Shared settings carry no label at all.
    for key, value in SHARED_SETTINGS.items():
        store.set(key, value)
    # Tenant settings use the same key names, told apart only by their label.
    for tenant_id, settings in TENANT_SETTINGS.items():
        for key, value in settings.items():
            store.set(key, value, label=tenant_id)
    return store
