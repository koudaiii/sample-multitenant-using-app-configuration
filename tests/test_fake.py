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
