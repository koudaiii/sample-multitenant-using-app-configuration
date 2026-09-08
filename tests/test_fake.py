"""The fake store must mimic App Configuration's query semantics closely
enough that the three patterns are exercised for real."""

import json

import pytest

from mtappconfig.fake import (
    SNAPSHOT_REFERENCE_CONTENT_TYPE,
    FakeAppConfigurationStore,
    FakeSetting,
)
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
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_snapshot_reference_uses_the_official_content_type_and_json_value():
    store = FakeAppConfigurationStore()
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")

    setting = store._settings[("tenant-a/RolloutSnapshot", None)]

    assert SNAPSHOT_REFERENCE_CONTENT_TYPE == (
        'application/json; profile="https://azconfig.io/mime-profiles/snapshot-ref"; charset=utf-8'
    )
    assert setting.content_type == SNAPSHOT_REFERENCE_CONTENT_TYPE
    assert json.loads(setting.value) == {"snapshot_name": "snap-1"}


def test_set_many_preserves_snapshot_reference_content_type():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_many(
        [
            FakeSetting(
                key="tenant-a/RolloutSnapshot",
                value=json.dumps({"snapshot_name": "snap-1"}),
                content_type=SNAPSHOT_REFERENCE_CONTENT_TYPE,
            )
        ]
    )

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


@pytest.mark.parametrize(
    "value",
    [
        "{",
        "[]",
        '"snap-1"',
        "{}",
        '{"snapshot_name": null}',
        '{"snapshot_name": 1}',
    ],
)
def test_malformed_snapshot_reference_values_are_silently_skipped(value):
    store = FakeAppConfigurationStore()
    store.set("tenant-a/LogLevel", "Warning")
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    store.set_many(
        [
            FakeSetting(
                key="tenant-a/RolloutSnapshot",
                value=value,
                content_type=SNAPSHOT_REFERENCE_CONTENT_TYPE,
            )
        ]
    )

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Warning"
    }


def test_create_snapshot_copies_values_so_later_mutation_has_no_effect():
    store = FakeAppConfigurationStore()
    source_values = {"LogLevel": "Debug"}
    store.create_snapshot("snap-1", source_values)
    source_values["LogLevel"] = "Trace"
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_create_snapshot_rejects_an_existing_name_without_replacing_contents():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})

    with pytest.raises(ValueError, match="snapshot 'snap-1' already exists"):
        store.create_snapshot("snap-1", {"LogLevel": "Trace"})

    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")
    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_unresolved_snapshot_reference_is_silently_skipped():
    store = FakeAppConfigurationStore()
    store.set("tenant-a/LogLevel", "Warning")
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "missing-snapshot")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Warning"
    }


def test_expired_snapshot_reference_is_silently_skipped():
    clock = FakeClock()
    store = FakeAppConfigurationStore(clock=clock)
    store.create_snapshot("snap-1", {"LogLevel": "Debug"}, retention_seconds=10.0)
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")
    clock.advance(11.0)

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {}


def test_snapshot_reference_wins_when_its_key_sorts_after_the_direct_key():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    # "RolloutSnapshot" (R) sorts lexicographically after "LogLevel" (L), so
    # the reference wins even though it is inserted before the direct key.
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "snap-1")
    store.set("tenant-a/LogLevel", "Warning")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Debug"
    }


def test_a_direct_key_wins_when_its_name_sorts_after_the_reference_key():
    store = FakeAppConfigurationStore()
    store.create_snapshot("snap-1", {"LogLevel": "Debug"})
    # "EarlySnapshot" (E) sorts lexicographically before "LogLevel" (L), so
    # the direct key wins even though the reference is inserted after it.
    store.set("tenant-a/LogLevel", "Warning")
    store.set_snapshot_reference("tenant-a/EarlySnapshot", "snap-1")

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "LogLevel": "Warning"
    }


def test_a_snapshot_containing_a_foreign_key_merges_it_in_unfiltered():
    """The reference key's own prefix scopes which reference gets picked up
    by a given key_filter, but resolving that reference does NOT filter the
    snapshot's contents against the same key_filter. This matches the real
    service's documented behavior (a resolved snapshot's keys are merged as
    they are) and is deliberately not hidden by re-filtering here — building
    a snapshot with only the intended keys is the caller's responsibility."""
    store = FakeAppConfigurationStore()
    store.create_snapshot(
        "tenant-a-snapshot-built-wrong",
        {"LogLevel": "Debug", "tenant-b/DatabaseName": "db-tenant-b"},
    )
    store.set_snapshot_reference("tenant-a/RolloutSnapshot", "tenant-a-snapshot-built-wrong")

    selected = store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"])

    assert selected["tenant-b/DatabaseName"] == "db-tenant-b"


def test_a_plain_setting_that_looks_like_a_reference_key_is_not_treated_as_one():
    """A plain `.set()` call (not `set_snapshot_reference()`) never gets the
    reference content type, so select() must treat its value as a literal
    string, not as a snapshot name to resolve."""
    store = FakeAppConfigurationStore()
    store.set("tenant-a/RolloutSnapshot", "not-a-reference", label=None)

    assert store.select(key_filter="tenant-a/*", trim_prefixes=["tenant-a/"]) == {
        "RolloutSnapshot": "not-a-reference"
    }
