"""Pattern 4: sample 01's key-prefix selection, plus a tenant-scoped snapshot
reference that lets a tenant's configuration be rolled out or rolled back by
repointing a key — no application code change, no redeployment.

The selection logic below is intentionally identical to sample 01's
KeyPrefixSource. That is the point of this sample: rollout control is a
store-side behaviour (which snapshot a reference key currently points at),
not an application-side one. The only thing that changed between "the old
configuration" and "the new configuration" is the store's state, seeded by
`seed_snapshot_references.py`.
"""

from __future__ import annotations

from mtappconfig.source import TenantConfig, merge_config_values

# See sample 01's source_key_prefix.py for why this prefix, and why a leading
# underscore, is load-bearing.
SHARED_PREFIX = "_shared/"


class SnapshotReferenceSource:
    name = "snapshot-references"

    def __init__(self, store) -> None:
        self._store = store

    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            shared = self._store.select(
                key_filter=f"{SHARED_PREFIX}*",
                trim_prefixes=[SHARED_PREFIX],
            )
            # tenant_id has already been validated by TenantRegistry.resolve
            # before it reaches here — see sample 01 for why that matters.
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
