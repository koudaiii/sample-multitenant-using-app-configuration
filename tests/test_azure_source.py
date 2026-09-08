"""The real-provider binding, isolated so the rest of the suite never needs it."""

import importlib.util

import pytest

from mtappconfig import azure_source
from mtappconfig.azure_source import AzureAppConfigurationStore, AzureSdkNotInstalledError
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

    def __init__(self, values=None):
        super().__init__(values or {"LogLevel": "Warning"})
        self.refresh_calls = 0
        self.closed = False

    def refresh(self):
        self.refresh_calls += 1

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

    def load(self, **kwargs):
        self.load_calls.append(kwargs)
        if self.fail_load:
            raise RuntimeError("cannot reach store")
        provider = _FakeProvider()
        self.providers.append(provider)
        return provider

    def selector(self, **kwargs):
        return kwargs

    def credential(self):
        return "credential"

    def install(self, monkeypatch):
        monkeypatch.setattr(
            azure_source,
            "_import_sdk",
            lambda: (self.load, self.selector, self.credential),
        )
        return self


@pytest.fixture
def sdk(monkeypatch):
    return _FakeSdk().install(monkeypatch)


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
    """A cached provider's refresh() is a no-op inside the refresh interval and
    does not raise on failure, so probing through the cache would report a dead
    store as healthy forever after one success."""
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


def test_ping_uses_a_short_timeout_so_readiness_fails_fast(sdk):
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io", startup_timeout_seconds=100, probe_timeout_seconds=5
    )

    store.ping()

    assert sdk.load_calls[0]["startup_timeout"] == 5


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


@pytest.mark.live
def test_reads_from_a_real_store():
    """Run with: uv pip install -r requirements-azure.txt && uv run pytest --run-live

    Requires APPCONFIG_ENDPOINT and a signed-in identity holding the
    App Configuration Data Reader role on that store.
    """
    import os

    endpoint = os.environ["APPCONFIG_ENDPOINT"]
    store = AzureAppConfigurationStore(endpoint)

    store.ping()
    assert isinstance(store.select(key_filter="*"), dict)
