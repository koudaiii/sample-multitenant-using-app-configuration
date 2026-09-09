"""The single abstraction every pattern implements."""

from itertools import cycle

import pytest

from mtappconfig.source import TenantConfig
from mtappconfig import source


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


def test_merging_values_preserves_each_read_failure_and_tenant_precedence():
    first, second = RuntimeError("shared"), RuntimeError("tenant")
    merged = source.merge_config_values(
        source.ConfigValues({"LogLevel": "Warning"}, refresh_errors=(first,)),
        source.ConfigValues({"LogLevel": "Debug"}, refresh_errors=(second,)),
    )

    assert merged == {"LogLevel": "Debug"}
    assert merged.refresh_errors == (first, second)


def test_cold_config_exposes_read_errors_without_putting_them_in_values():
    error = RuntimeError("shared unavailable")
    config = TenantConfig(
        tenant_id="tenant-a",
        values=source.ConfigValues({"LogLevel": "Warning"}, refresh_errors=(error,)),
    )

    assert config.values == {"LogLevel": "Warning"}
    assert type(config.values) is dict
    assert config.refresh_errors == (error,)


def test_refresh_signal_preserves_last_good_values_and_does_not_linger_after_a_noop():
    error = RuntimeError("shared unavailable")
    reads = iter([
        source.ConfigValues({"LogLevel": "Debug"}, refresh_errors=(error,)),
        source.ConfigValues({"LogLevel": "Warning"}),
        source.ConfigValues({"LogLevel": "Debug"}),
    ])
    config = TenantConfig(tenant_id="tenant-a", values={"LogLevel": "Warning"}, reload=lambda: next(reads))

    assert config.refresh() is False
    assert config.values == {"LogLevel": "Warning"}
    assert config.refresh_errors == (error,)
    assert config.refresh() is False
    assert config.refresh_errors == ()
    assert config.refresh() is True
    assert config.values == {"LogLevel": "Debug"}
    assert config.refresh_errors == ()


@pytest.mark.parametrize("pattern", ["prefix", "label", "store", "snapshot"])
def test_every_python_source_preserves_the_explicit_refresh_channel(pattern):
    from source_key_prefix import KeyPrefixSource
    from source_label import LabelSource
    from source_store_per_tenant import StorePerTenantSource
    from source_snapshot_references import SnapshotReferenceSource

    error = RuntimeError("shared refresh failure")

    class Store:
        reads = cycle([
            source.ConfigValues({"App:Version": "old"}, refresh_errors=(error,)),
            {"LogLevel": "Warning"},
        ])

        def select(self, **kwargs):
            return next(self.reads)

    store = Store()
    constructors = {
        "prefix": KeyPrefixSource,
        "label": LabelSource,
        "snapshot": SnapshotReferenceSource,
        "store": lambda shared: StorePerTenantSource(shared, {"tenant-a": store}),
    }
    config = constructors[pattern](store).load("tenant-a")

    assert config.values == {"App:Version": "old", "LogLevel": "Warning"}
    assert config.refresh_errors == (error,)
    assert config.refresh() is False
    assert config.refresh_errors == (error,)
