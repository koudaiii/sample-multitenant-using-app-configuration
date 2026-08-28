"""Shared store, tenant settings separated by label."""

import pytest

from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app
from seed_label import build_store
from source_label import LabelSource


@pytest.fixture
def store():
    return build_store()


@pytest.fixture
def source(store):
    return LabelSource(store)


def test_seeded_store_uses_labels_not_prefixes(store):
    unlabelled = store.select(key_filter="*")
    labelled = store.select(key_filter="*", label_filter="tenant-a")

    assert "App:Version" in unlabelled
    assert "LogLevel" not in unlabelled, "tenant settings must carry a label"
    assert labelled["LogLevel"] == "Warning"


def test_keys_are_not_prefixed_in_this_pattern(store):
    assert not any("/" in key for key in store.select(key_filter="*", label_filter="*"))


def test_resolves_the_merged_config_for_each_tenant(source):
    for tenant in TENANTS:
        assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_one_tenant_never_sees_another_tenants_values(source):
    values = source.load("tenant-a").values

    assert values["DatabaseName"] == "db-tenant-a"
    assert "db-tenant-b" not in values.values()


def test_tenant_settings_override_shared_settings(source):
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"


def test_refresh_picks_up_a_changed_value(store, source):
    config = source.load("tenant-a")
    store.set("LogLevel", "Error", label="tenant-a")

    assert config.refresh() is True
    assert config.values["LogLevel"] == "Error"


def test_the_label_is_spent_on_tenancy(store, source):
    """Documents the trade-off: a 'preview' label cannot coexist with the
    tenant label on the same setting, so labels are no longer available for
    versioning or environments."""
    store.set("App:Version", "2.0.0-preview", label="preview")

    assert source.load("tenant-a").values["App:Version"] == "1.4.2"


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
    assert payload["pattern"] == "shared-store-label"
