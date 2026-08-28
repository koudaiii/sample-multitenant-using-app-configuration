"""The single abstraction every pattern implements."""

from mtappconfig.source import TenantConfig


def test_refresh_without_a_reload_hook_reports_no_change():
    config = TenantConfig(tenant_id="tenant-a", values={"LogLevel": "Warning"})

    assert config.refresh() is False
    assert config.values == {"LogLevel": "Warning"}


def test_refresh_reports_a_change_and_swaps_the_values():
    versions = iter([{"LogLevel": "Debug"}])
    config = TenantConfig(
        tenant_id="tenant-a",
        values={"LogLevel": "Warning"},
        reload=lambda: next(versions),
    )

    assert config.refresh() is True
    assert config.values == {"LogLevel": "Debug"}


def test_refresh_reports_no_change_when_the_values_are_identical():
    config = TenantConfig(
        tenant_id="tenant-a",
        values={"LogLevel": "Warning"},
        reload=lambda: {"LogLevel": "Warning"},
    )

    assert config.refresh() is False
