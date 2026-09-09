"""Tenant identifier validation is the security boundary of these samples."""

import pytest

from mtappconfig.tenants import Tenant, TenantRegistry, UnknownTenantError


@pytest.fixture
def registry():
    return TenantRegistry(
        [
            Tenant(tenant_id="tenant-a", display_name="Tenant A"),
            Tenant(tenant_id="tenant-b", display_name="Tenant B"),
        ]
    )


def test_resolves_a_registered_tenant(registry):
    assert registry.resolve("tenant-a").display_name == "Tenant A"


def test_lists_registered_ids(registry):
    assert registry.ids() == ["tenant-a", "tenant-b"]


def test_len_and_iteration(registry):
    assert len(registry) == 2
    assert [t.tenant_id for t in registry] == ["tenant-a", "tenant-b"]


@pytest.mark.parametrize(
    "hostile",
    [
        "*",                # would match every tenant's keys behind a prefix filter
        "tenant-a/*",
        "tenant-a/../tenant-b",
        "../tenant-b",
        "tenant-a/LogLevel",
        "TENANT-A",         # case is significant
        "tenant a",
        "",
        "ab",               # shorter than the 3 character minimum
        "a" * 33,           # longer than the 32 character maximum
        "-tenant-a",        # must not start with a hyphen
        "tenant-a-",        # must not end with a hyphen
        "tenant-a\n",       # a trailing newline must not slip past the anchor
    ],
)
def test_rejects_malformed_ids(registry, hostile):
    with pytest.raises(UnknownTenantError):
        registry.resolve(hostile)


def test_rejects_well_formed_but_unregistered_id(registry):
    with pytest.raises(UnknownTenantError):
        registry.resolve("tenant-c")


def test_registry_rejects_a_malformed_id_at_construction():
    with pytest.raises(ValueError):
        TenantRegistry([Tenant(tenant_id="not valid", display_name="x")])


@pytest.mark.parametrize("display_name", ["Tenant A", "Different Tenant A"])
def test_registry_rejects_duplicate_ids_even_from_a_generator(display_name):
    tenants = (
        tenant
        for tenant in [
            Tenant(tenant_id="tenant-a", display_name="Tenant A"),
            Tenant(tenant_id="tenant-a", display_name=display_name),
        ]
    )

    with pytest.raises(ValueError, match="duplicate tenant id in registry: 'tenant-a'"):
        TenantRegistry(tenants)
