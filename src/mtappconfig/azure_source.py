"""Binding to the real Azure App Configuration provider.

This module presents the same surface as `FakeAppConfigurationStore`, so the
three pattern implementations run unchanged against a real store.

Every azure import happens inside a function. The base install deliberately
omits the App Configuration SDK, and a module-scope import would break the
whole application for anyone who has not installed the `azure` extra.
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from .observability import get_logger
from .source import ConfigStoreUnavailableError

_INSTALL_HINT = "install the Azure SDK: uv pip install -r requirements-azure.txt"

# App Configuration represents "no label" with a null character. Passing None
# through to the provider would mean "any label", which is not the same thing.
_NULL_LABEL = "\0"

# A key filter no real setting matches, used only by the reachability probe.
_PROBE_KEY_FILTER = "mtappconfig-probe-matches-nothing"

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


def _create_transport(**kwargs):
    from azure.core.pipeline.transport import RequestsTransport

    return RequestsTransport(**kwargs)


@dataclass
class _ProviderResources:
    provider: object
    transport: object

    def close(self) -> None:
        try:
            self.provider.close()
        finally:
            # RequestsTransport.close is idempotent. Also close it if the
            # provider fails before reaching its client/transport cleanup.
            self.transport.close()


class AzureAppConfigurationStore:
    """One real App Configuration store, behind the fake store's interface."""

    def __init__(
        self,
        endpoint: str,
        *,
        refresh_interval_seconds: float = 30.0,
        startup_timeout_seconds: int = 100,
        probe_timeout_seconds: int = 5,
    ) -> None:
        if (
            not isinstance(endpoint, str)
            or endpoint != endpoint.strip()
            or urlsplit(endpoint).scheme != "https"
            or not urlsplit(endpoint).netloc
        ):
            raise ValueError("App Configuration endpoint must be a non-empty HTTPS URL")
        self.name = endpoint
        self._endpoint = endpoint
        self._refresh_interval = refresh_interval_seconds
        self._startup_timeout = startup_timeout_seconds
        # SDK startup timeouts are retry budgets checked between operations,
        # not deadlines that interrupt blocking credential or HTTP calls.
        self._probe_timeout = probe_timeout_seconds
        # One provider per distinct query. The provider holds the connection
        # and its own refresh bookkeeping, so it is worth keeping around until
        # the owning tenant cache entry expires or is evicted.
        self._providers: dict[tuple, _ProviderResources] = {}
        self._credential = None
        self._closed = False
        # Readiness and tenant requests may run concurrently. Shutdown must
        # not close the credential while either one is borrowing it.
        self._lock = threading.RLock()
        atexit.register(self.close_all)

    def ping(self) -> None:
        """Probe the store over a fresh connection.

        This deliberately does NOT reuse the providers cached by select().
        A cached provider's refresh() is a no-op inside the refresh interval
        and then supplies no evidence of current reachability. A fresh load
        must contact the service instead of treating that no-op as health.
        """
        with self._lock:
            self._ensure_open()
            try:
                probe = self._load_provider(_PROBE_KEY_FILTER, None, (), probe=True)
                probe.close()
            except AzureSdkNotInstalledError:
                raise
            except Exception as error:
                if not self._providers:
                    self._close_credential()
                raise ConfigStoreUnavailableError(
                    f"App Configuration store {self._endpoint!r} is unreachable: {error}"
                ) from error

    def close(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> None:
        """Drop and close the provider for this exact query, if one exists.

        TTL expiry and LRU eviction call this through TenantConfig.close. The
        next select for the same query must create a fresh provider rather than
        reusing one that may still hold stale values after failed refreshes.
        """
        with self._lock:
            cache_key = (key_filter, label_filter, tuple(trim_prefixes))
            resources = self._providers.pop(cache_key, None)
            if resources is not None:
                resources.close()

    def close_all(self) -> None:
        """End the store lifetime, closing all providers and its credential.

        Unlike close(query), this is terminal. Context-manager exit and
        normal process exit call it too; repeated calls are harmless.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            atexit.unregister(self.close_all)
            resources = list(self._providers.values())
            self._providers.clear()
            for resource in resources:
                self._safe_close(resource)
            self._close_credential()

    def __enter__(self) -> AzureAppConfigurationStore:
        self._ensure_open()
        return self

    def __exit__(self, *args) -> None:
        self.close_all()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("App Configuration store is closed")

    def _safe_close(self, resource) -> None:
        try:
            resource.close()
        except Exception:
            _logger.warning(
                "app configuration resource cleanup failed",
                extra={"event": "appconfig.close.failed"},
                exc_info=True,
            )

    def _close_credential(self) -> None:
        credential, self._credential = self._credential, None
        if credential is not None:
            self._safe_close(credential)

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> dict[str, str]:
        with self._lock:
            self._ensure_open()
            cache_key = (key_filter, label_filter, tuple(trim_prefixes))
            resources = self._providers.get(cache_key)
            if resources is None:
                try:
                    resources = self._load_provider(key_filter, label_filter, trim_prefixes)
                except AzureSdkNotInstalledError:
                    raise
                except Exception as error:
                    raise ConfigStoreUnavailableError(
                        f"could not load configuration from {self._endpoint!r}: {error}"
                    ) from error
                self._providers[cache_key] = resources
            else:
                # No callback on an interval no-op: do not infer a successful
                # service round-trip from refresh() returning normally.
                resources.provider.refresh()
            return dict(resources.provider)

    def _load_provider(
        self,
        key_filter: str,
        label_filter: str | None,
        trim_prefixes: Sequence[str],
        *,
        probe: bool = False,
    ) -> _ProviderResources:
        load, SettingSelector, DefaultAzureCredential = _import_sdk()
        transport = None
        try:
            if self._credential is None:
                self._credential = DefaultAzureCredential()
            transport_options = {
                "connection_timeout": 2 if probe else 5,
                "read_timeout": 2 if probe else 5,
            }
            transport = _create_transport(**transport_options)
            provider = load(
                endpoint=self._endpoint,
                # SDK 2.5.0 closes clients/transports, not this borrowed
                # credential. Only the store owns and closes the credential.
                credential=self._credential,
                transport=transport,
                selects=[
                    SettingSelector(
                        key_filter=key_filter,
                        label_filter=_NULL_LABEL if label_filter is None else label_filter,
                    )
                ],
                trim_prefixes=list(trim_prefixes),
                refresh_enabled=not probe,
                refresh_interval=self._refresh_interval,
                startup_timeout=self._probe_timeout if probe else self._startup_timeout,
                **transport_options,
                # Supported azure-core policy options, also forwarded by
                # provider 2.5.0. Neither timeout nor backoff_max interrupts
                # credential acquisition, HTTP reads or Retry-After sleeps.
                timeout=5 if probe else 30,
                retry_total=0 if probe else 2,
                retry_backoff_max=1,
                on_refresh_error=self._on_refresh_error,
            )
            return _ProviderResources(provider, transport)
        except BaseException:
            # SDK 2.5.0 load() does not return/close its provider on failure.
            # Retaining the supplied transport lets us close its HTTP session.
            if transport is not None:
                self._safe_close(transport)
            if not self._providers:
                self._close_credential()
            raise

    def _on_refresh_error(self, error: Exception) -> None:
        """Let TenantConfigCache record one failure with the caller's tenant."""
        raise ConfigStoreUnavailableError(
            f"could not refresh configuration from {self._endpoint!r}: {error}"
        ) from error
