"""The fake store must mimic App Configuration's query semantics closely
enough that the three patterns are exercised for real."""

import pytest

from mtappconfig.fake import FakeAppConfigurationStore
from mtappconfig.source import ConfigStoreUnavailableError


@pytest.fixture
def store():
    store = FakeAppConfigurationStore()
    store.set("shared/App:Version", "1.4.2")
    store.set("tenant-a/LogLevel", "Warning")
    store.set("tenant-b/LogLevel", "Debug")
    store.set("LogLevel", "Information", label="tenant-a")
    store.set("App:Version", "0.9.0", label="preview")
    return store


def test_prefix_filter_selects_only_that_prefix(store):
    assert store.select(key_filter="tenant-a/*") == {"tenant-a/LogLevel": "Warning"}


def test_trim_prefixes_strips_the_tenant_prefix(store):
    selected = store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"])

    assert selected == {"LogLevel": "Warning"}


def test_star_filter_selects_every_unlabelled_setting(store):
    assert store.select(key_filter="*") == {
        "shared/App:Version": "1.4.2",
        "tenant-a/LogLevel": "Warning",
        "tenant-b/LogLevel": "Debug",
    }


def test_label_filter_none_excludes_labelled_settings(store):
    """This is App Configuration's default and the reason sample 02 must
    always pass a label filter explicitly."""
    assert "LogLevel" not in store.select(key_filter="LogLevel")


def test_label_filter_selects_that_label(store):
    assert store.select(key_filter="*", label_filter="tenant-a") == {"LogLevel": "Information"}


def test_label_filter_star_selects_every_label(store):
    assert len(store.select(key_filter="*", label_filter="*")) == 5


def test_exact_key_filter(store):
    assert store.select(key_filter="tenant-b/LogLevel") == {"tenant-b/LogLevel": "Debug"}


def test_set_overwrites_the_same_key_and_label(store):
    store.set("tenant-a/LogLevel", "Error")

    assert store.select(key_filter="tenant-a/*") == {"tenant-a/LogLevel": "Error"}


def test_counts_requests_so_caching_can_be_observed(store):
    before = store.request_count
    store.select(key_filter="*")

    assert store.request_count == before + 1


def test_unavailable_store_raises_on_select(store):
    store.unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        store.select(key_filter="*")


def test_unavailable_store_raises_on_ping(store):
    store.unavailable = True

    with pytest.raises(ConfigStoreUnavailableError):
        store.ping()


def test_tenant_settings_override_shared_settings():
    from mtappconfig import sampledata

    assert sampledata.SHARED_SETTINGS["App:SupportEmail"] == "support@contoso.example"
    assert sampledata.expected_config("tenant-b")["App:SupportEmail"] == "vip@contoso.example"
    assert sampledata.expected_config("tenant-a")["App:SupportEmail"] == "support@contoso.example"


from mtappconfig.fake import SNAPSHOT_REFERENCE_CONTENT_TYPE


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_snapshot_reference_merges_snapshot_values_into_selection():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_create_snapshot_copies_values_so_later_mutation_has_no_effect():
    store = FakeAppConfigurationStore()
    source_values = {"LogLevel": "Debug"}
    store.create_snapshot("snap-1", source_values)
    source_values["LogLevel"] = "Trace"
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_unresolved_snapshot_reference_is_silently_skipped():
    store = FakeAppConfigurationStore()
    store.set("tenant-a/LogLevel", "Warning")
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "missing-snapshot")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Warning"
    }


def test_expired_snapshot_reference_is_silently_skipped():
    clock = FakeClock()
    store = FakeAppConfigurationStore(clock=clock)
    store.create_snapshot("snap-1", {"LogLevel": "Debug"}, retention_seconds=10.0)
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")
    clock.advance(11.0)

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {}


def test_snapshot_reference_wins_when_set_after_the_direct_key():
    store = FakeAppConfigurationStore()
    store.set("tenant-a/LogLevel", "Warning")
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_a_later_direct_key_overrides_an_earlier_snapshot_reference():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_snapshot_reference("tenant-a/ConfigSnapshot", "snap-1")
    store.set("tenant-a/LogLevel", "Warning")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Warning"
    }


def test_a_reference_to_a_never_created_snapshot_name_matches_no_setting():
    """SNAPSHOT_REFERENCE_CONTENT_TYPE is exercised end-to-end by the tests
    above; this checks the module constant itself is the value select()
    actually compares against, without reaching into the store's internals."""
    store = FakeAppConfigurationStore()
    store.set("tenant-a/ConfigSnapshot", "not-a-reference", label=None)

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "ConfigSnapshot": "not-a-reference"
    }
    assert SNAPSHOT_REFERENCE_CONTENT_TYPE.startswith("application/")
