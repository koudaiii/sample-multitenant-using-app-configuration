"""The real-provider binding, isolated so the rest of the suite never needs it."""

import importlib.util

import pytest

from mtappconfig import azure_source
from mtappconfig.azure_source import AzureAppConfigurationStore, AzureSdkNotInstalledError

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


@pytest.mark.skipif(azure_sdk_installed, reason="the Azure SDK is installed")
def test_selecting_without_the_sdk_explains_how_to_install_it():
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    with pytest.raises(AzureSdkNotInstalledError, match="requirements-azure.txt"):
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
