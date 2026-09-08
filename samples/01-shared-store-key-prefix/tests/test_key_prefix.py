"""Shared store, tenant settings behind a `<tenant-id>/` key prefix."""

import runpy
from pathlib import Path

import pytest

from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app
from seed_key_prefix import build_store
from source_key_prefix import SHARED_PREFIX, KeyPrefixSource


class _GuardedStore:
    def __init__(self):
        self.select_calls = []

    def select(self, key_filter="*", label_filter=None, trim_prefixes=()):
        self.select_calls.append(
            {
                "key_filter": key_filter,
                "label_filter": label_filter,
                "trim_prefixes": tuple(trim_prefixes),
            }
        )
        return {}

    def close(self, key_filter="*", label_filter=None, trim_prefixes=()):
        pass

    def ping(self):
        pass


@pytest.fixture
def store():
    return build_store()


@pytest.fixture
def source(store):
    return KeyPrefixSource(store)


def test_the_shared_prefix_can_never_be_a_tenant_id():
    """The namespaces are kept disjoint by construction, not by convention:
    a tenant id cannot start with an underscore, so no tenant can ever claim
    the shared namespace and silently overwrite every other tenant's globals."""
    from mtappconfig.tenants import TENANT_ID_PATTERN

    assert TENANT_ID_PATTERN.match(SHARED_PREFIX.rstrip("/")) is None


def test_seeded_store_uses_tenant_prefixed_keys(store):
    keys = store.select(key_filter="*").keys()

    assert "tenant-a/LogLevel" in keys
    assert f"{SHARED_PREFIX}App:Version" in keys


def test_resolves_the_merged_config_for_each_tenant(source):
    for tenant in TENANTS:
        assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_the_prefix_is_trimmed_so_the_app_sees_stable_key_names(source):
    values = source.load("tenant-a").values

    assert "LogLevel" in values
    assert not any(key.startswith("tenant-a/") for key in values)


def test_one_tenant_never_sees_another_tenants_values(source):
    values = source.load("tenant-a").values

    assert values["DatabaseName"] == "db-tenant-a"
    assert "db-tenant-b" not in values.values()


def test_tenant_settings_override_shared_settings(source):
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"


def test_refresh_picks_up_a_changed_value(store, source):
    config = source.load("tenant-a")
    store.set("tenant-a/LogLevel", "Error")

    assert config.refresh() is True
    assert config.values["LogLevel"] == "Error"


def test_ping_propagates_an_unavailable_store(store, source):
    from mtappconfig.source import ConfigStoreUnavailableError

    store.unavailable = True

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
    assert payload["pattern"] == "shared-store-key-prefix"


def test_app_module_uses_the_fake_store_when_endpoint_is_unset(monkeypatch):
    from mtappconfig import azure_source

    monkeypatch.delenv("APPCONFIG_ENDPOINT", raising=False)
    monkeypatch.setattr(
        azure_source,
        "AzureAppConfigurationStore",
        lambda endpoint: pytest.fail(f"unexpected Azure store for {endpoint}"),
    )

    module_globals = runpy.run_path(Path(__file__).resolve().parents[1] / "app.py")
    client = module_globals["app"].test_client()

    payload = client.get("/t/tenant-a/api/config").get_json()

    assert payload["values"] == expected_config("tenant-a")
    assert payload["pattern"] == "shared-store-key-prefix"


def test_app_module_uses_the_azure_store_when_endpoint_is_set(monkeypatch):
    from mtappconfig import azure_source

    constructed_endpoints = []

    def build_azure_store(endpoint):
        constructed_endpoints.append(endpoint)
        return build_store()

    monkeypatch.setenv("APPCONFIG_ENDPOINT", "https://example.azconfig.io")
    monkeypatch.setattr(
        azure_source,
        "AzureAppConfigurationStore",
        build_azure_store,
    )

    module_globals = runpy.run_path(Path(__file__).resolve().parents[1] / "app.py")
    client = module_globals["app"].test_client()

    payload = client.get("/t/tenant-a/api/config").get_json()

    assert constructed_endpoints == ["https://example.azconfig.io"]
    assert payload["values"] == expected_config("tenant-a")
    assert payload["pattern"] == "shared-store-key-prefix"


@pytest.mark.parametrize(
    "path",
    [
        "/t/*/api/config",
        "/t/tenant-a%0A/api/config",
        "/t/tenant-zzz/api/config",
        "/t/tenant-a%2Fapi/config",
    ],
)
def test_http_rejects_tenant_boundary_attacks_before_building_key_filters(path):
    store = _GuardedStore()
    app = create_app(
        source=KeyPrefixSource(store),
        registry=TenantRegistry(TENANTS),
        pattern_name="shared-store-key-prefix",
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get(path)

    assert response.status_code == 404
    assert store.select_calls == []
