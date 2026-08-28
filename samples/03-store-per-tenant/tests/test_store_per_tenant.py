"""A dedicated store per tenant, plus one shared store for global settings."""

import pytest

from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.source import ConfigStoreUnavailableError
from mtappconfig.tenants import Tenant, TenantRegistry
from mtappconfig.webapp import create_app
from seed_store_per_tenant import build_stores
from source_store_per_tenant import StorePerTenantSource


@pytest.fixture
def stores():
    return build_stores()


@pytest.fixture
def source(stores):
    shared, tenant_stores = stores
    return StorePerTenantSource(shared, tenant_stores)


def test_each_tenant_gets_its_own_store(stores):
    _, tenant_stores = stores

    assert set(tenant_stores) == {"tenant-a", "tenant-b"}
    assert tenant_stores["tenant-a"] is not tenant_stores["tenant-b"]


def test_a_tenant_store_holds_only_that_tenants_settings(stores):
    _, tenant_stores = stores

    values = tenant_stores["tenant-a"].select(key_filter="*")

    assert values["DatabaseName"] == "db-tenant-a"
    assert "db-tenant-b" not in values.values()


def test_resolves_the_merged_config_for_each_tenant(source):
    for tenant in TENANTS:
        assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_tenant_settings_override_shared_settings(source):
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"


def test_refresh_picks_up_a_changed_value(stores, source):
    _, tenant_stores = stores
    config = source.load("tenant-a")
    tenant_stores["tenant-a"].set("LogLevel", "Error")

    assert config.refresh() is True
    assert config.values["LogLevel"] == "Error"


def test_a_tenant_without_a_store_is_reported_clearly(stores):
    shared, tenant_stores = stores
    source = StorePerTenantSource(shared, {"tenant-a": tenant_stores["tenant-a"]})

    with pytest.raises(ConfigStoreUnavailableError, match="tenant-b"):
        source.load("tenant-b")


def test_missing_stores_are_detected_at_startup_not_per_request(stores):
    """The spec requires a misconfiguration to surface before serving traffic."""
    shared, tenant_stores = stores
    source = StorePerTenantSource(shared, {"tenant-a": tenant_stores["tenant-a"]})
    registry = TenantRegistry(
        [Tenant("tenant-a", "Tenant A"), Tenant("tenant-b", "Tenant B")]
    )

    with pytest.raises(ValueError, match="tenant-b"):
        source.validate_coverage(registry)


def test_validate_coverage_passes_when_every_tenant_has_a_store(source):
    assert source.validate_coverage(TenantRegistry(TENANTS)) is None


def test_one_unavailable_tenant_store_fails_readiness(stores, source):
    _, tenant_stores = stores
    tenant_stores["tenant-b"].unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        source.ping()


def test_serves_over_http(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name=source.name,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    payload = client.get("/t/tenant-b/api/config").get_json()

    assert payload["values"] == expected_config("tenant-b")
    assert payload["pattern"] == "store-per-tenant"
