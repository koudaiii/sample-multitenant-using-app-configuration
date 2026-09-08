"""A dedicated store per tenant, plus one shared store for global settings."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.source import ConfigStoreUnavailableError
from mtappconfig.tenants import Tenant, TenantRegistry
from mtappconfig.webapp import create_app
from seed_store_per_tenant import build_stores
from source_store_per_tenant import StorePerTenantSource


APP_MODULE = Path(__file__).resolve().parents[1] / "app.py"


class _RecordingStore:
    def __init__(self):
        self.close_calls = []

    def select(self, key_filter="*", label_filter=None, trim_prefixes=()):
        return {}

    def close(self, key_filter="*", label_filter=None, trim_prefixes=()):
        self.close_calls.append(
            {
                "key_filter": key_filter,
                "label_filter": label_filter,
                "trim_prefixes": tuple(trim_prefixes),
            }
        )

    def ping(self):
        pass


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


def test_tenant_config_close_drops_only_its_dedicated_store_provider():
    shared = _RecordingStore()
    tenant = _RecordingStore()
    config = StorePerTenantSource(shared, {"tenant-a": tenant}).load("tenant-a")

    assert config.close is not None
    config.close()

    assert shared.close_calls == []
    assert tenant.close_calls == [
        {
            "key_filter": "*",
            "label_filter": None,
            "trim_prefixes": (),
        }
    ]


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


def test_one_unavailable_tenant_store_does_not_fail_readiness(stores, source):
    """Isolation has to cut both ways: one tenant's outage must not take the
    deployment down for everyone else. That tenant degrades via the cache."""
    _, tenant_stores = stores
    tenant_stores["tenant-b"].unavailable = True

    assert source.ping() is None


def test_an_unavailable_shared_store_fails_readiness(stores, source):
    """The shared store is needed by every tenant, so losing it really is a
    deployment-wide problem and readiness must reflect it."""
    shared, _ = stores
    shared.unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        source.ping()


def _load_app_module():
    spec = spec_from_file_location("store_per_tenant_app_under_test", APP_MODULE)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sample_app_uses_fake_stores_when_endpoints_are_unset(monkeypatch):
    from mtappconfig import azure_source

    monkeypatch.delenv("APPCONFIG_SHARED_ENDPOINT", raising=False)
    monkeypatch.delenv("APPCONFIG_ENDPOINTS", raising=False)
    monkeypatch.setattr(
        azure_source,
        "AzureAppConfigurationStore",
        lambda endpoint: pytest.fail(f"unexpected Azure store for {endpoint}"),
    )

    module = _load_app_module()

    module.app.config.update(TESTING=True)
    payload = module.app.test_client().get("/t/tenant-a/api/config").get_json()

    assert payload["values"] == expected_config("tenant-a")
    assert payload["pattern"] == "store-per-tenant"


def test_sample_app_uses_shared_and_per_tenant_azure_endpoints(monkeypatch):
    from mtappconfig import azure_source

    constructed = []

    class StubAzureStore:
        def __init__(self, endpoint):
            self.endpoint = endpoint
            constructed.append(self)

    monkeypatch.setenv("APPCONFIG_SHARED_ENDPOINT", "https://shared.azconfig.io")
    monkeypatch.setenv(
        "APPCONFIG_ENDPOINTS",
        '{"tenant-a":"https://a.azconfig.io","tenant-b":"https://b.azconfig.io"}',
    )
    monkeypatch.setattr(azure_source, "AzureAppConfigurationStore", StubAzureStore)

    module = _load_app_module()

    assert [store.endpoint for store in constructed] == [
        "https://shared.azconfig.io",
        "https://a.azconfig.io",
        "https://b.azconfig.io",
    ]
    assert module.shared_store is constructed[0]
    assert module.tenant_stores == {
        "tenant-a": constructed[1],
        "tenant-b": constructed[2],
    }


@pytest.mark.parametrize(
    ("shared_endpoint", "tenant_endpoints"),
    [
        ("https://shared.azconfig.io", None),
        (None, '{"tenant-a":"https://a.azconfig.io","tenant-b":"https://b.azconfig.io"}'),
    ],
)
def test_sample_app_requires_shared_and_tenant_endpoints_together(
    monkeypatch, shared_endpoint, tenant_endpoints
):
    if shared_endpoint is None:
        monkeypatch.delenv("APPCONFIG_SHARED_ENDPOINT", raising=False)
    else:
        monkeypatch.setenv("APPCONFIG_SHARED_ENDPOINT", shared_endpoint)
    if tenant_endpoints is None:
        monkeypatch.delenv("APPCONFIG_ENDPOINTS", raising=False)
    else:
        monkeypatch.setenv("APPCONFIG_ENDPOINTS", tenant_endpoints)

    with pytest.raises(
        ValueError,
        match="APPCONFIG_SHARED_ENDPOINT and APPCONFIG_ENDPOINTS must be set together",
    ):
        _load_app_module()


@pytest.mark.parametrize(
    ("shared_endpoint", "tenant_endpoints", "message"),
    [
        (
            "",
            '{"tenant-a":"https://a.azconfig.io","tenant-b":"https://b.azconfig.io"}',
            "APPCONFIG_SHARED_ENDPOINT must be a non-empty HTTPS URL",
        ),
        (
            "https://shared.azconfig.io",
            "",
            "APPCONFIG_ENDPOINTS must be a non-empty JSON object",
        ),
        (
            "",
            "",
            "APPCONFIG_SHARED_ENDPOINT must be a non-empty HTTPS URL",
        ),
    ],
)
def test_sample_app_rejects_empty_present_endpoint_variables(
    monkeypatch, shared_endpoint, tenant_endpoints, message
):
    monkeypatch.setenv("APPCONFIG_SHARED_ENDPOINT", shared_endpoint)
    monkeypatch.setenv("APPCONFIG_ENDPOINTS", tenant_endpoints)

    with pytest.raises(ValueError, match=message):
        _load_app_module()


@pytest.mark.parametrize(
    "tenant_endpoints",
    [
        "not-json",
        '["https://a.azconfig.io", "https://b.azconfig.io"]',
        '{"tenant-a": ""}',
        '{"tenant-a": 42}',
    ],
)
def test_sample_app_rejects_invalid_tenant_endpoint_json(monkeypatch, tenant_endpoints):
    monkeypatch.setenv("APPCONFIG_SHARED_ENDPOINT", "https://shared.azconfig.io")
    monkeypatch.setenv("APPCONFIG_ENDPOINTS", tenant_endpoints)

    with pytest.raises(
        ValueError,
        match="APPCONFIG_ENDPOINTS must be a JSON object mapping tenant IDs to HTTPS URLs",
    ):
        _load_app_module()


def test_sample_app_detects_a_missing_tenant_endpoint_at_startup(monkeypatch):
    from mtappconfig import azure_source

    monkeypatch.setenv("APPCONFIG_SHARED_ENDPOINT", "https://shared.azconfig.io")
    monkeypatch.setenv(
        "APPCONFIG_ENDPOINTS",
        '{"tenant-a":"https://a.azconfig.io"}',
    )
    monkeypatch.setattr(azure_source, "AzureAppConfigurationStore", lambda endpoint: object())

    with pytest.raises(ValueError, match="tenant-b"):
        _load_app_module()


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
