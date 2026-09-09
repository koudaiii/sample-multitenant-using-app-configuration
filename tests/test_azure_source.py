"""The real-provider binding, isolated so the rest of the suite never needs it."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import threading

import pytest

from mtappconfig import azure_source
from mtappconfig.azure_source import AzureAppConfigurationStore, AzureSdkNotInstalledError
from mtappconfig.cache import TenantConfigCache
from mtappconfig.source import ConfigStoreUnavailableError

try:
    azure_sdk_installed = importlib.util.find_spec("azure.appconfiguration.provider") is not None
except (ModuleNotFoundError, ValueError):
    azure_sdk_installed = False


def test_module_imports_without_the_azure_extra():
    """Nothing here may import azure at module scope: the base install has no
    App Configuration SDK, and a module-scope import would break every test."""
    assert azure_source.AzureAppConfigurationStore is not None


def test_constructing_a_store_does_not_need_the_sdk():
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    assert store.name == "https://example.azconfig.io"


@pytest.mark.parametrize(
    "endpoint",
    ["", " ", "example.azconfig.io", "http://example.azconfig.io"],
)
def test_constructing_a_store_rejects_an_invalid_endpoint(endpoint):
    with pytest.raises(ValueError, match="HTTPS URL"):
        AzureAppConfigurationStore(endpoint)


@pytest.mark.skipif(azure_sdk_installed, reason="the Azure SDK is installed")
def test_selecting_without_the_sdk_explains_how_to_install_it():
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    with pytest.raises(AzureSdkNotInstalledError, match="requirements-azure.txt"):
        store.select(key_filter="*")


class _FakeProvider(dict):
    """Stands in for AzureAppConfigurationProvider, which is a Mapping."""

    def __init__(self, values=None, *, sdk=None, **kwargs):
        super().__init__({"LogLevel": "Warning"} if values is None else values)
        self.refresh_calls = 0
        self.refresh_attempts = 0
        self.refresh_error = None
        self.next_values = None
        self.closed = False
        self.close_calls = 0
        self.fail_close = False
        self.sdk = sdk
        self.options = kwargs
        self.next_refresh_at = sdk.now + kwargs.get("refresh_interval", 30) if sdk else 0
        self.client_backoff_until = 0.0
        self.error_callback_calls = 0

    def _report_error(self, error):
        self.error_callback_calls += 1
        self.next_refresh_at = self.sdk.now + min(30, self.options.get("refresh_interval", 30))
        self.options["on_refresh_error"](error)

    def refresh(self):
        self.refresh_calls += 1
        # SDK 2.5.0 checks active clients before the refresh interval. With
        # every client backed off, even an immediate repeat reports failure.
        if self.sdk.now < self.client_backoff_until:
            self._report_error(RuntimeError("no active App Configuration clients"))
            return
        if self.sdk.now < self.next_refresh_at:
            return
        self.next_refresh_at = self.sdk.now + self.options.get("refresh_interval", 30)
        self.refresh_attempts += 1
        self.options["credential"].get_token("scope")
        if self.refresh_error:
            self.client_backoff_until = self.sdk.now + min(30, self.options.get("refresh_interval", 30))
            self._report_error(self.refresh_error)
            return
        if self.next_values is not None:
            self.clear()
            self.update(self.next_values)

    def close(self):
        self.closed = True
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("provider close failed")
        if transport := self.options.get("transport"):
            transport.close()


class _FakeCredential:
    def __init__(self):
        self.close_calls = 0

    def get_token(self, *scopes, **kwargs):
        assert self.close_calls == 0, "a provider tried to use a closed shared credential"
        return object()

    def close(self):
        self.close_calls += 1


class _FakeTransport:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSdk:
    """A stand-in injected at the _import_sdk seam.

    This exercises the adapter's own logic — provider caching, probing and
    error wrapping. It makes no claim about how the real service behaves;
    the real-Azure path stays unverified in this environment.
    """

    def __init__(self, fail_load=False):
        self.load_calls = []
        self.fail_load = fail_load
        self.providers = []
        self.credentials = []
        self.transports = []
        self.now = 0.0
        self.fail_provider_close = False
        self.values_by_query = {}

    def load(self, **kwargs):
        self.load_calls.append(kwargs)
        kwargs["credential"].get_token("scope")
        selector = kwargs["selects"][0]
        query = (selector["key_filter"], selector["label_filter"])
        provider = _FakeProvider(self.values_by_query.get(query), sdk=self, **kwargs)
        provider.fail_close = self.fail_provider_close
        self.providers.append(provider)
        # SDK 2.5.0 can allocate clients and then raise without returning or
        # closing the provider. The adapter must retain the HTTP transport.
        if self.fail_load:
            raise RuntimeError("cannot reach store")
        return provider

    def selector(self, **kwargs):
        return kwargs

    def credential(self):
        credential = _FakeCredential()
        self.credentials.append(credential)
        return credential

    def transport(self, **kwargs):
        transport = _FakeTransport(**kwargs)
        self.transports.append(transport)
        return transport

    def install(self, monkeypatch):
        monkeypatch.setattr(
            azure_source,
            "_import_sdk",
            lambda: (self.load, self.selector, self.credential),
        )
        monkeypatch.setattr(azure_source, "_create_transport", self.transport, raising=False)
        return self


@pytest.fixture
def sdk(monkeypatch):
    return _FakeSdk().install(monkeypatch)


@pytest.mark.parametrize(
    ("label_filter", "expected_label"),
    [(None, "\0"), ("tenant-a", "tenant-a")],
)
def test_select_forwards_the_main_provider_load_contract(
    sdk, label_filter, expected_label
):
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io",
        refresh_interval_seconds=17.0,
        startup_timeout_seconds=43,
    )

    store.select(
        key_filter="tenant-a/*",
        label_filter=label_filter,
        trim_prefixes=["tenant-a/"],
    )

    assert len(sdk.load_calls) == 1
    load_call = sdk.load_calls[0]
    assert load_call["endpoint"] == "https://example.azconfig.io"
    assert isinstance(load_call["credential"], _FakeCredential)
    assert load_call["selects"] == [
        {
            "key_filter": "tenant-a/*",
            "label_filter": expected_label,
        }
    ]
    assert load_call["trim_prefixes"] == ["tenant-a/"]
    assert load_call["refresh_enabled"] is True
    assert load_call["refresh_interval"] == 17.0
    assert load_call["startup_timeout"] == 43
    assert load_call["connection_timeout"] == 5
    assert load_call["read_timeout"] == 5
    assert load_call["timeout"] == 30
    assert load_call["retry_total"] == 2
    assert load_call["retry_backoff_max"] == 1
    assert load_call["transport"] is sdk.transports[0]
    assert sdk.transports[0].options == {"connection_timeout": 5, "read_timeout": 5}


def test_select_loads_once_per_distinct_query(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.select(key_filter="tenant-a/*")
    store.select(key_filter="tenant-a/*")

    assert len(sdk.load_calls) == 1
    assert sdk.providers[0].refresh_calls == 1, "the second read refreshes"


def test_select_loads_separately_for_a_different_query(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.select(key_filter="tenant-a/*")
    store.select(key_filter="tenant-b/*")

    assert len(sdk.load_calls) == 2


def test_ping_never_reuses_a_cached_provider(sdk):
    """A refresh interval no-op cannot establish current store reachability."""
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="tenant-a/*")

    store.ping()
    store.ping()

    assert len(sdk.load_calls) == 3, "each ping opens its own connection"
    assert sdk.providers[0].refresh_calls == 0, "the probe must not touch the cache"


def test_ping_closes_its_probe(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.ping()

    assert sdk.providers[0].closed is True


def test_ping_forwards_short_retry_and_transport_budgets_not_a_latency_ceiling(sdk):
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io", startup_timeout_seconds=100, probe_timeout_seconds=5
    )

    store.ping()

    assert sdk.load_calls[0]["startup_timeout"] == 5
    assert sdk.load_calls[0]["connection_timeout"] == 2
    assert sdk.load_calls[0]["read_timeout"] == 2
    assert sdk.load_calls[0]["timeout"] == 5
    assert sdk.load_calls[0]["retry_total"] == 0
    assert sdk.load_calls[0]["retry_backoff_max"] == 1
    assert sdk.transports[0].options == {"connection_timeout": 2, "read_timeout": 2}
    assert sdk.transports[0].closed


def test_ping_reports_an_unreachable_store(monkeypatch):
    _FakeSdk(fail_load=True).install(monkeypatch)
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    with pytest.raises(ConfigStoreUnavailableError, match="unreachable"):
        store.ping()


def test_select_wraps_a_load_failure(monkeypatch):
    _FakeSdk(fail_load=True).install(monkeypatch)
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    with pytest.raises(ConfigStoreUnavailableError, match="could not load"):
        store.select(key_filter="*")


def test_close_drops_and_closes_only_the_matching_provider(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="tenant-a/*")
    store.select(key_filter="_shared/*")

    store.close(key_filter="tenant-a/*")

    assert sdk.providers[0].closed is True
    assert sdk.providers[1].closed is False, "the shared provider must not be touched"

    store.select(key_filter="tenant-a/*")
    assert len(sdk.load_calls) == 3


def test_close_on_a_query_with_no_provider_is_a_no_op(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.close(key_filter="tenant-a/*")


def test_one_owned_credential_is_reused_across_queries_and_probes(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="_shared/*")
    store.select(key_filter="tenant-a/*")
    store.ping()
    store.ping()

    assert len(sdk.credentials) == 1
    assert all(call["credential"] is sdk.credentials[0] for call in sdk.load_calls)
    assert sdk.credentials[0].close_calls == 0
    assert all(provider.closed for provider in sdk.providers[2:])

    store.close_all()
    assert sdk.credentials[0].close_calls == 1


def test_query_close_does_not_close_the_credential_or_other_providers(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select()
    store.select(key_filter="tenant-b/*")

    store.close()
    store.close()
    sdk.now = 30
    store.select(key_filter="tenant-b/*")
    store.select()

    assert len(sdk.credentials) == 1
    assert sdk.credentials[0].close_calls == 0
    assert sdk.providers[0].close_calls == 1
    assert sdk.transports[0].closed
    assert not sdk.providers[1].closed


def test_full_close_releases_every_provider_and_credential_exactly_once(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="_shared/*")
    store.select(key_filter="tenant-a/*")
    store.select(key_filter="tenant-b/*")

    store.close_all()
    store.close_all()
    store.close(key_filter="tenant-a/*")

    assert [provider.close_calls for provider in sdk.providers] == [1, 1, 1]
    assert all(transport.closed for transport in sdk.transports)
    assert [credential.close_calls for credential in sdk.credentials] == [1]
    for operation in (store.select, store.ping):
        with pytest.raises(RuntimeError, match="closed"):
            operation()
    assert len(sdk.load_calls) == 3


def test_closing_an_unused_store_does_not_create_credentials_or_import_the_sdk(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.close_all()

    assert sdk.credentials == []
    assert sdk.load_calls == []
    assert sdk.transports == []


def test_empty_provider_mappings_are_still_closed(sdk):
    sdk.values_by_query[("*", "\0")] = {}
    sdk.values_by_query[(azure_source._PROBE_KEY_FILTER, "\0")] = {}
    with AzureAppConfigurationStore("https://example.azconfig.io") as store:
        assert store.select() == {}
        store.ping()

    assert [provider.close_calls for provider in sdk.providers] == [1, 1]
    assert all(transport.closed for transport in sdk.transports)
    assert sdk.credentials[0].close_calls == 1


def test_full_close_continues_when_one_provider_cannot_close(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="tenant-a/*")
    store.select(key_filter="tenant-b/*")
    sdk.providers[0].fail_close = True

    store.close_all()
    store.close_all()

    assert [provider.close_calls for provider in sdk.providers] == [1, 1]
    assert all(transport.closed for transport in sdk.transports)
    assert sdk.credentials[0].close_calls == 1


def test_store_context_manager_closes_resources_when_the_body_fails(sdk):
    with pytest.raises(RuntimeError, match="caller failed"):
        with AzureAppConfigurationStore("https://example.azconfig.io") as store:
            store.select()
            raise RuntimeError("caller failed")

    assert sdk.providers[0].closed
    assert sdk.transports[0].closed
    assert sdk.credentials[0].close_calls == 1


@pytest.mark.parametrize("operation", ["select", "ping"])
def test_failed_load_closes_unreturned_provider_transport_and_unused_credential(sdk, operation):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    sdk.fail_load = True

    with pytest.raises(ConfigStoreUnavailableError, match="cannot reach store"):
        getattr(store, operation)()

    assert sdk.transports[0].closed
    assert sdk.credentials[0].close_calls == 1
    sdk.fail_load = False
    store.select()
    assert len(sdk.credentials) == 2
    assert sdk.credentials[1].close_calls == 0
    store.close_all()
    assert [credential.close_calls for credential in sdk.credentials] == [1, 1]


@pytest.mark.parametrize("operation", ["select", "ping"])
def test_failed_load_does_not_close_credentials_still_used_by_a_cached_provider(sdk, operation):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="_shared/*")
    sdk.fail_load = True

    with pytest.raises(ConfigStoreUnavailableError):
        getattr(store, operation)()

    assert sdk.transports[-1].closed
    assert len(sdk.credentials) == 1
    assert sdk.credentials[0].close_calls == 0
    assert not sdk.providers[0].closed
    sdk.now = 30
    assert store.select(key_filter="_shared/*") == {"LogLevel": "Warning"}


def test_failed_probe_close_still_releases_transport_and_unused_credential(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    sdk.fail_provider_close = True

    with pytest.raises(ConfigStoreUnavailableError, match="provider close failed"):
        store.ping()

    assert sdk.transports[0].closed
    assert sdk.credentials[0].close_calls == 1


def test_sdk_callback_failure_is_an_explicit_read_signal_including_client_backoff(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="tenant-a/*")
    error = RuntimeError("refresh is unavailable")
    sdk.providers[0].refresh_error = error
    sdk.now = 29

    selection = store.select(key_filter="tenant-a/*")
    assert selection == {"LogLevel": "Warning"}
    assert selection.refresh_errors == ()
    assert sdk.providers[0].refresh_attempts == 0
    sdk.now = 30
    selection = store.select(key_filter="tenant-a/*")
    assert selection == {"LogLevel": "Warning"}
    assert selection.refresh_errors[0].__cause__ is error
    sdk.now = 31
    repeated = store.select(key_filter="tenant-a/*")
    assert "no active" in str(repeated.refresh_errors[0])
    assert sdk.providers[0].refresh_attempts == 1
    assert sdk.providers[0].error_callback_calls == 2

    sdk.providers[0].refresh_error = None
    sdk.now = 60
    assert store.select(key_filter="tenant-a/*").refresh_errors == ()


def test_sdk_callback_failure_reaches_cache_stats_and_logs_with_tenant_context(sdk, caplog):
    from source_key_prefix import KeyPrefixSource

    store = AzureAppConfigurationStore("https://example.azconfig.io")
    cache = TenantConfigCache(KeyPrefixSource(store), clock=lambda: sdk.now)
    config = cache.get("tenant-a")
    cache.get("tenant-b")
    sdk.providers[1].refresh_error = RuntimeError("tenant-a refresh is unavailable")
    sdk.providers[2].next_values = {"LogLevel": "Debug"}
    sdk.now = 30

    assert cache.get("tenant-a") is config
    assert config.values == {"LogLevel": "Warning"}
    assert cache.stats.refresh_failures == 1
    assert cache.get("tenant-b").values == {"LogLevel": "Debug"}
    (record,) = [record for record in caplog.records if getattr(record, "event", None) == "config.refresh.failed"]
    assert record.tenant_id == "tenant-a"
    assert "tenant-a refresh is unavailable" in str(record.exc_info[1])
    assert not any(getattr(record, "event", None) == "appconfig.refresh.failed" for record in caplog.records)

    sdk.now = 59
    cache.get("tenant-a")
    assert sdk.providers[1].refresh_attempts == 1
    sdk.now = 60
    cache.get("tenant-a")
    assert sdk.providers[1].refresh_attempts == 2
    assert cache.stats.refresh_failures == 2


def test_shared_backoff_preserves_warm_cold_and_expired_tenant_reads(sdk, caplog):
    from source_key_prefix import KeyPrefixSource

    sdk.values_by_query = {
        ("_shared/*", "\0"): {"App:Version": "old-shared"},
        ("tenant-a/*", "\0"): {"LogLevel": "Warning"},
        ("tenant-b/*", "\0"): {"LogLevel": "Debug"},
    }
    with AzureAppConfigurationStore("https://example.azconfig.io") as store:
        cache = TenantConfigCache(KeyPrefixSource(store), clock=lambda: sdk.now, ttl_seconds=32)
        original = cache.get("tenant-a")
        shared, tenant_a = sdk.providers
        shared.refresh_error = RuntimeError("shared AzureError")
        tenant_a.next_values = {"LogLevel": "Error"}
        sdk.now = 30

        assert cache.get("tenant-a") is original
        assert original.values == {"App:Version": "old-shared", "LogLevel": "Warning"}
        assert cache.stats.refresh_failures == 1

        sdk.now = 31
        assert cache.get("tenant-b").values == {"App:Version": "old-shared", "LogLevel": "Debug"}
        assert cache.stats.refresh_failures == 2
        assert shared.refresh_attempts == 1
        assert shared.error_callback_calls == 2

        sdk.values_by_query[("tenant-a/*", "\0")] = {"LogLevel": "Critical"}
        sdk.now = 32
        replacement = cache.get("tenant-a")
        assert replacement is not original
        assert replacement.values == {"App:Version": "old-shared", "LogLevel": "Critical"}
        assert tenant_a.closed
        assert not shared.closed
        assert cache.stats.expirations == 1
        assert cache.stats.misses == 3
        assert cache.stats.refresh_failures == 3
        assert shared.refresh_attempts == 1
        assert shared.error_callback_calls == 3

        failures = [record for record in caplog.records if getattr(record, "event", None) == "config.refresh.failed"]
        assert [record.tenant_id for record in failures] == ["tenant-a", "tenant-b", "tenant-a"]
        assert "shared AzureError" in str(failures[0].exc_info[1])
        assert all("no active" in str(record.exc_info[1]) for record in failures[1:])

        cache.get("tenant-a")
        assert cache.stats.refresh_failures == 3, "a cached hit must not replay its load-time signal"
        shared.refresh_error = None
        shared.next_values = {"App:Version": "recovered-shared"}
        sdk.now = 62
        assert cache.get("tenant-a").values == {"App:Version": "recovered-shared", "LogLevel": "Critical"}
        assert cache.stats.refresh_failures == 3


def test_ping_does_not_wait_for_unrelated_provider_load(sdk, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    load = sdk.load

    def blocking_load(**kwargs):
        if kwargs["selects"][0]["key_filter"] == "tenant-a/*":
            entered.set()
            assert release.wait(5)
        return load(**kwargs)

    monkeypatch.setattr(sdk, "load", blocking_load)
    with AzureAppConfigurationStore("https://example.azconfig.io") as store:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reading = pool.submit(store.select, key_filter="tenant-a/*")
            try:
                assert entered.wait(2)
                pool.submit(store.ping).result(timeout=1)
                assert not reading.done()
                assert sdk.credentials[0].close_calls == 0
            finally:
                release.set()
            assert reading.result(timeout=2) == {"LogLevel": "Warning"}


@pytest.mark.parametrize("phase", ["credential", "load", "refresh"])
def test_full_close_waits_for_borrowed_or_constructing_provider(sdk, monkeypatch, phase):
    entered, release = threading.Event(), threading.Event()
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    if phase == "credential":
        operation = sdk.credential

        def blocked():
            entered.set()
            assert release.wait(5)
            return operation()

        monkeypatch.setattr(sdk, "credential", blocked)
    elif phase == "load":
        operation = sdk.load

        def blocked(**kwargs):
            entered.set()
            assert release.wait(5)
            return operation(**kwargs)

        monkeypatch.setattr(sdk, "load", blocked)
    else:
        store.select()
        sdk.now = 30
        operation = sdk.providers[0].refresh

        def blocked():
            entered.set()
            assert release.wait(5)
            return operation()

        monkeypatch.setattr(sdk.providers[0], "refresh", blocked)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(store.select)
        try:
            assert entered.wait(2)
            closing = pool.submit(store.close_all)
            with pytest.raises(TimeoutError):
                closing.result(timeout=0.1)
            if phase == "credential":
                assert sdk.credentials == []
                assert sdk.transports == []
            else:
                assert sdk.credentials[0].close_calls == 0
                assert not sdk.transports[0].closed
        finally:
            release.set()
        reading.result(timeout=2)
        closing.result(timeout=2)
    assert sdk.credentials[0].close_calls == 1
    assert all(provider.closed for provider in sdk.providers)
    assert all(transport.closed for transport in sdk.transports)


@pytest.mark.parametrize("blocked_operation", ["select", "ping"])
def test_failed_load_preserves_a_credential_borrowed_by_an_unpublished_operation(
    sdk, monkeypatch, blocked_operation
):
    entered, release = threading.Event(), threading.Event()
    load = sdk.load

    def controlled_load(**kwargs):
        is_probe = kwargs["selects"][0]["key_filter"] == azure_source._PROBE_KEY_FILTER
        if is_probe == (blocked_operation == "ping"):
            entered.set()
            assert release.wait(5)
            return load(**kwargs)
        load(**kwargs)
        raise RuntimeError("other operation failed")

    monkeypatch.setattr(sdk, "load", controlled_load)
    with AzureAppConfigurationStore("https://example.azconfig.io") as store:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(getattr(store, blocked_operation))
            try:
                assert entered.wait(2)
                other = store.ping if blocked_operation == "select" else store.select
                with pytest.raises(ConfigStoreUnavailableError, match="other operation failed"):
                    pool.submit(other).result(timeout=1)
                assert len(sdk.credentials) == 1
                assert sdk.credentials[0].close_calls == 0
            finally:
                release.set()
            pending.result(timeout=2)
    assert sdk.credentials[0].close_calls == 1
    assert all(transport.closed for transport in sdk.transports)


def test_query_close_waits_for_its_borrowers_without_blocking_another_query(sdk, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with AzureAppConfigurationStore("https://example.azconfig.io") as store:
        store.select(key_filter="tenant-a/*")
        store.select(key_filter="tenant-b/*")
        provider_a, provider_b = sdk.providers
        refresh = provider_a.refresh

        def blocked_refresh():
            entered.set()
            assert release.wait(5)
            refresh()

        monkeypatch.setattr(provider_a, "refresh", blocked_refresh)
        with ThreadPoolExecutor(max_workers=3) as pool:
            reading = pool.submit(store.select, key_filter="tenant-a/*")
            try:
                assert entered.wait(2)
                closing = pool.submit(store.close, key_filter="tenant-a/*")
                with pytest.raises(TimeoutError):
                    closing.result(timeout=0.1)
                assert not provider_a.closed
                assert pool.submit(store.select, key_filter="tenant-b/*").result(timeout=1) == {"LogLevel": "Warning"}
                assert sdk.credentials[0].close_calls == 0
            finally:
                release.set()
            reading.result(timeout=2)
            closing.result(timeout=2)
        assert provider_a.closed
        assert not provider_b.closed


def test_concurrent_reads_for_one_query_share_one_initial_load(sdk, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    load = sdk.load

    def blocked_load(**kwargs):
        entered.set()
        assert release.wait(5)
        return load(**kwargs)

    monkeypatch.setattr(sdk, "load", blocked_load)
    with AzureAppConfigurationStore("https://example.azconfig.io") as store:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(store.select)
            try:
                assert entered.wait(2)
                second = pool.submit(store.select)
                with pytest.raises(TimeoutError):
                    second.result(timeout=0.1)
            finally:
                release.set()
            assert first.result(timeout=2) == second.result(timeout=2) == {"LogLevel": "Warning"}
        assert len(sdk.load_calls) == 1
        assert len(sdk.credentials) == 1


def test_overlapping_failure_cleanup_closes_each_unused_credential(sdk, monkeypatch):
    closing, release = threading.Event(), threading.Event()
    factory = sdk.credential
    sdk.fail_load = True

    def credential():
        result = factory()
        if len(sdk.credentials) == 1:
            close = result.close

            def blocked_close():
                closing.set()
                assert release.wait(5)
                close()

            result.close = blocked_close
        return result

    monkeypatch.setattr(sdk, "credential", credential)
    with AzureAppConfigurationStore("https://example.azconfig.io") as store:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(store.select, key_filter="tenant-a/*")
            try:
                assert closing.wait(2)
                second = pool.submit(store.select, key_filter="tenant-b/*")
                with pytest.raises(ConfigStoreUnavailableError):
                    second.result(timeout=1)
                assert len(sdk.credentials) == 2
                assert sdk.credentials[1].close_calls == 0
            finally:
                release.set()
            with pytest.raises(ConfigStoreUnavailableError):
                first.result(timeout=2)
        assert [item.close_calls for item in sdk.credentials] == [1, 1]


def test_timeout_documentation_does_not_promise_a_hard_latency_ceiling():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    for phrase in (
        "startup_timeout",
        "probe_timeout_seconds",
        "connection_timeout",
        "read_timeout",
        "retry_total",
        "retry_backoff_max",
        "Retry-After",
        "ハードなレイテンシ上限ではありません",
    ):
        assert phrase in readme
    for relative_path in (
        "README.md",
        "samples/03-store-per-tenant/README.md",
        "docs/superpowers/specs/2026-09-06-provider-lifecycle-and-staleness-design.md",
        "docs/superpowers/plans/2026-09-06-provider-lifecycle-and-staleness-plan.md",
    ):
        text = (root / relative_path).read_text()
        assert "最大100秒" not in text
        assert "load returns or reaches its startup timeout" not in text
        assert "load が完了するか startup timeout に達するまで" not in text


@pytest.mark.live
def test_reads_from_a_real_store():
    """Run with: uv pip install -r requirements-azure.txt && uv run pytest --run-live

    Requires APPCONFIG_ENDPOINT and a signed-in identity holding the
    App Configuration Data Reader role on that store.
    """
    import os

    endpoint = os.environ["APPCONFIG_ENDPOINT"]
    with AzureAppConfigurationStore(endpoint) as store:
        store.ping()
        assert isinstance(store.select(key_filter="*"), dict)
