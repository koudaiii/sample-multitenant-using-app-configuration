"""All three isolation models must resolve to identical results.

The samples store the same settings in three different layouts. If they did
not agree, the comparison the repository is built around would be meaningless,
and the shared core would be hiding a difference rather than isolating one.
"""

import pytest

from mtappconfig.cache import TenantConfigCache
from mtappconfig.sampledata import TENANTS, expected_config
from mtappconfig.tenants import TenantRegistry
from mtappconfig.webapp import create_app
from seed_key_prefix import build_store as build_key_prefix_store
from seed_label import build_store as build_label_store
from seed_store_per_tenant import build_stores as build_per_tenant_stores
from source_key_prefix import KeyPrefixSource
from source_label import LabelSource
from source_store_per_tenant import StorePerTenantSource


def _make_key_prefix_source():
    return KeyPrefixSource(build_key_prefix_store())


def _make_label_source():
    return LabelSource(build_label_store())


def _make_store_per_tenant_source():
    shared, tenant_stores = build_per_tenant_stores()
    return StorePerTenantSource(shared, tenant_stores)


SOURCE_FACTORIES = [
    _make_key_prefix_source,
    _make_label_source,
    _make_store_per_tenant_source,
]

SOURCE_IDS = ["key-prefix", "label", "store-per-tenant"]


@pytest.fixture(params=SOURCE_FACTORIES, ids=SOURCE_IDS)
def source(request):
    return request.param()


@pytest.mark.parametrize("tenant", TENANTS, ids=lambda t: t.tenant_id)
def test_every_pattern_resolves_the_expected_config(source, tenant):
    assert source.load(tenant.tenant_id).values == expected_config(tenant.tenant_id)


def test_every_pattern_isolates_tenants(source):
    a = source.load("tenant-a").values
    b = source.load("tenant-b").values

    assert a["DatabaseName"] == "db-tenant-a"
    assert b["DatabaseName"] == "db-tenant-b"
    assert "db-tenant-b" not in a.values()
    assert "db-tenant-a" not in b.values()


def test_every_pattern_merges_shared_settings_underneath_tenant_settings(source):
    assert source.load("tenant-a").values["App:SupportEmail"] == "support@contoso.example"
    assert source.load("tenant-b").values["App:SupportEmail"] == "vip@contoso.example"
    assert source.load("tenant-a").values["App:Version"] == "1.4.2"


def test_all_three_patterns_agree_with_each_other():
    resolved = [
        {tenant.tenant_id: factory().load(tenant.tenant_id).values for tenant in TENANTS}
        for factory in SOURCE_FACTORIES
    ]

    first, *rest = resolved
    for other in rest:
        assert other == first


def test_every_pattern_rejects_an_unregistered_tenant_over_http(source):
    app = create_app(
        source=source,
        registry=TenantRegistry(TENANTS),
        pattern_name=source.name,
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    assert client.get("/t/tenant-zzz/api/config").status_code == 404
    assert client.get("/t/*/api/config").status_code == 404


def test_every_pattern_works_with_the_shared_cache(source):
    cache = TenantConfigCache(source)

    first = cache.get("tenant-a").values
    second = cache.get("tenant-a").values

    assert first == second == expected_config("tenant-a")
    assert cache.stats.misses == 1
    assert cache.stats.hits == 1
