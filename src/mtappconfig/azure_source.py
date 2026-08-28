"""Binding to the real Azure App Configuration provider.

This module presents the same surface as `FakeAppConfigurationStore`, so the
three pattern implementations run unchanged against a real store.

Every azure import happens inside a function. The base install deliberately
omits the App Configuration SDK, and a module-scope import would break the
whole application for anyone who has not installed the `azure` extra.
"""

from __future__ import annotations

from collections.abc import Sequence

from .observability import get_logger
from .source import ConfigStoreUnavailableError

_INSTALL_HINT = "install the Azure SDK: uv pip install -r requirements-azure.txt"

# App Configuration represents "no label" with a null character. Passing None
# through to the provider would mean "any label", which is not the same thing.
_NULL_LABEL = "\0"

_logger = get_logger(__name__)


class AzureSdkNotInstalledError(RuntimeError):
    """The azure extra is not installed in this environment."""


def _import_sdk():
    try:
        from azure.appconfiguration.provider import SettingSelector, load
        from azure.identity import DefaultAzureCredential
    except ImportError as error:
        raise AzureSdkNotInstalledError(
            f"Azure App Configuration SDK is not available; {_INSTALL_HINT}"
        ) from error
    return load, SettingSelector, DefaultAzureCredential


class AzureAppConfigurationStore:
    """One real App Configuration store, behind the fake store's interface."""

    def __init__(
        self,
        endpoint: str,
        *,
        refresh_interval_seconds: float = 30.0,
        startup_timeout_seconds: int = 100,
    ) -> None:
        self.name = endpoint
        self._endpoint = endpoint
        self._refresh_interval = refresh_interval_seconds
        self._startup_timeout = startup_timeout_seconds
        # One provider per distinct query. The provider holds the connection
        # and its own refresh bookkeeping, so it is worth keeping around.
        self._providers: dict[tuple, object] = {}

    def ping(self) -> None:
        try:
            self.select(key_filter="ping-probe-that-matches-nothing")
        except AzureSdkNotInstalledError:
            raise
        except Exception as error:
            raise ConfigStoreUnavailableError(
                f"App Configuration store {self._endpoint!r} is unreachable: {error}"
            ) from error

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> dict[str, str]:
        cache_key = (key_filter, label_filter, tuple(trim_prefixes))
        provider = self._providers.get(cache_key)
        if provider is None:
            provider = self._create_provider(key_filter, label_filter, trim_prefixes)
            self._providers[cache_key] = provider
        else:
            # Activity-driven refresh: a no-op until refresh_interval elapses,
            # so calling this on every request costs nothing most of the time.
            provider.refresh()
        return dict(provider)

    def _create_provider(
        self,
        key_filter: str,
        label_filter: str | None,
        trim_prefixes: Sequence[str],
    ):
        load, SettingSelector, DefaultAzureCredential = _import_sdk()
        try:
            return load(
                endpoint=self._endpoint,
                # Entra ID only. No connection strings, no access keys.
                credential=DefaultAzureCredential(),
                selects=[
                    SettingSelector(
                        key_filter=key_filter,
                        label_filter=_NULL_LABEL if label_filter is None else label_filter,
                    )
                ],
                trim_prefixes=list(trim_prefixes),
                refresh_enabled=True,
                refresh_interval=self._refresh_interval,
                # Reliability: retry a slow or briefly unavailable store on
                # startup rather than failing immediately.
                startup_timeout=self._startup_timeout,
                on_refresh_error=self._on_refresh_error,
            )
        except AzureSdkNotInstalledError:
            raise
        except Exception as error:
            raise ConfigStoreUnavailableError(
                f"could not load configuration from {self._endpoint!r}: {error}"
            ) from error

    def _on_refresh_error(self, error: Exception) -> None:
        """Log and swallow: the cache keeps serving the last known values."""
        _logger.warning(
            "app configuration refresh failed",
            extra={"event": "appconfig.refresh.failed"},
            exc_info=error,
        )
