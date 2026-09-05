"""Sample 04 verifies PR1's claim (architecture-center-pr/report.md): a
tenant-scoped key that points at a snapshot reference lets a tenant's
configuration be rolled out or rolled back by repointing the reference,
with no application code change.

Each test below is named for the PR1 claim it backs, so the mapping in this
sample's README stays traceable to a specific test.
"""

from __future__ import annotations

import pytest

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry, UnknownTenantError
from mtappconfig.webapp import create_app

from seed_snapshot_references import (
    TENANT_A_PREVIOUS_SNAPSHOT,
    TENANT_A_ROLLOUT_SNAPSHOT,
    build_store,
)
from source_snapshot_references import SnapshotReferenceSource


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_rollout_serves_the_new_snapshot_values():
    """The store is seeded already mid-rollout: tenant-a's reference points
    at the new snapshot."""
    resolved = SnapshotReferenceSource(build_store()).load("tenant-a").values

    assert resolved["LogLevel"] == "Debug"
    assert resolved["Features:BetaDashboard"] == "true"
    assert resolved["DisplayName"] == "Tenant A (rollout)"
    # Settings the snapshot doesn't mention are untouched.
    assert resolved["DatabaseName"] == "db-tenant-a"
    assert resolved["App:SupportEmail"] == "support@contoso.example"


def test_repointing_the_reference_rolls_back_with_no_code_change():
    store = build_store()
    config = SnapshotReferenceSource(store).load("tenant-a")
    assert config.values["LogLevel"] == "Debug"

    store.set_snapshot_reference("tenant-a/ConfigSnapshot", TENANT_A_PREVIOUS_SNAPSHOT)
    changed = config.refresh()

    assert changed is True
    assert config.values["LogLevel"] == "Warning"
    assert config.values["Features:BetaDashboard"] == "false"
    # The previous snapshot never mentioned DisplayName, so tenant-a's
    # directly set value shows back through, unaffected by either snapshot.
    assert config.values["DisplayName"] == "Tenant A"


def test_an_unresolved_reference_falls_back_to_direct_values_without_error():
    """tenant-b's reference names a snapshot that was never created."""
    resolved = SnapshotReferenceSource(build_store()).load("tenant-b").values

    assert resolved == expected_config("tenant-b")


def test_an_expired_snapshot_falls_back_to_direct_values_without_error():
    clock = FakeClock()
    store = FakeAppConfigurationStore(clock=clock)
    store.set("tenant-a/DisplayName", "Tenant A")
    store.set("tenant-a/LogLevel", "Warning")
    store.create_snapshot("tenant-a-temp", {"LogLevel": "Debug"}, retention_seconds=60.0)
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "tenant-a-temp")
    source = SnapshotReferenceSource(store)

    assert source.load("tenant-a").values["LogLevel"] == "Debug"

    clock.advance(61.0)

    assert source.load("tenant-a").values["LogLevel"] == "Warning"


def test_the_snapshot_wins_the_merge_when_the_reference_is_set_last():
    resolved = SnapshotReferenceSource(build_store()).load("tenant-a").values

    # seed_snapshot_references.py sets tenant-a's direct DisplayName before
    # the reference key, so the reference — processed later — wins.
    assert resolved["DisplayName"] == "Tenant A (rollout)"


def test_a_direct_key_set_after_the_reference_wins_the_merge():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")
    store.set("tenant-a/LogLevel", "Warning")

    resolved = SnapshotReferenceSource(store).load("tenant-a").values

    assert resolved["LogLevel"] == "Warning"


def test_tenant_isolation_is_preserved():
    source = SnapshotReferenceSource(build_store())

    tenant_b_values = source.load("tenant-b").values

    assert "Tenant A (rollout)" not in tenant_b_values.values()
    assert tenant_b_values["DatabaseName"] == "db-tenant-b"

    registry = TenantRegistry(TENANTS)
    with pytest.raises(UnknownTenantError):
        registry.resolve("*")


def test_http_config_endpoint_serves_rollout_and_fallback_without_500():
    app = create_app(
        source=SnapshotReferenceSource(build_store()),
        registry=TenantRegistry(TENANTS),
        pattern_name="snapshot-references",
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    resp_a = client.get("/t/tenant-a/api/config")
    assert resp_a.status_code == 200
    assert resp_a.get_json()["values"]["LogLevel"] == "Debug"

    resp_b = client.get("/t/tenant-b/api/config")
    assert resp_b.status_code == 200
    assert resp_b.get_json()["values"] == expected_config("tenant-b")
