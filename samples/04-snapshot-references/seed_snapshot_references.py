"""Lay the canonical sample data out like sample 01, then add two snapshots
and two reference keys so the rollout and fallback paths are both live.

tenant-a's reference key points at the *new* snapshot: the store already
looks like a rollout in progress. tenant-b's reference key points at a
snapshot that doesn't exist, so tenant-b demonstrates the fallback path.
"""

from __future__ import annotations

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS

from source_snapshot_references import SHARED_PREFIX

TENANT_A_PREVIOUS_SNAPSHOT = "tenant-a-2026-08-01"
TENANT_A_ROLLOUT_SNAPSHOT = "tenant-a-2026-09-01"
TENANT_B_MISSING_SNAPSHOT = "tenant-b-missing"


def build_store() -> FakeAppConfigurationStore:
    store = FakeAppConfigurationStore(name="shared-store")

    for key, value in SHARED_SETTINGS.items():
        store.set(f"{SHARED_PREFIX}{key}", value)
    for tenant_id, settings in TENANT_SETTINGS.items():
        for key, value in settings.items():
            store.set(f"{tenant_id}/{key}", value)

    # Match the live CLI's tenant-a/* snapshot filter, including raw prefixes.
    previous = {f"tenant-a/{key}": value for key, value in TENANT_SETTINGS["tenant-a"].items()}
    store.create_snapshot(TENANT_A_PREVIOUS_SNAPSHOT, previous)
    # The rollout snapshot also sets DisplayName, which tenant-a already has
    # set directly above — this is the sample's key-collision demonstration.
    # The reference key below is named "RolloutSnapshot", which sorts *after*
    # "DatabaseName", "DisplayName", "Features:BetaDashboard", and "LogLevel"
    # in lexicographic order. The real store (and this fake, after the fix
    # for the lexicographic-order rule) resolves same-name-key conflicts by
    # lexicographic order of the key name, not by write order — so the
    # snapshot's DisplayName and LogLevel win the merge because
    # "RolloutSnapshot" sorts after those key names, not because it was
    # written last (see FakeAppConfigurationStore.select in
    # src/mtappconfig/fake.py).
    store.create_snapshot(
        TENANT_A_ROLLOUT_SNAPSHOT,
        {
            **previous,
            "tenant-a/LogLevel": "Debug",
            "tenant-a/Features:BetaDashboard": "true",
            "tenant-a/DisplayName": "Tenant A (rollout)",
        },
    )
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", TENANT_A_ROLLOUT_SNAPSHOT)

    # tenant-b's reference names a snapshot that was never created, so every
    # resolution falls back to tenant-b's directly set values.
    store.set_snapshot_reference("tenant-b/RolloutSnapshot", TENANT_B_MISSING_SNAPSHOT)

    return store
