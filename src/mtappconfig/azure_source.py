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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .observability import get_logger
from .source import ConfigStoreUnavailableError, ConfigValues

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
    provider: object | None = None
    transport: object | None = None
    refresh_errors: list[Exception] = field(default_factory=list)
    io_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    borrowers: int = 0
    load_error: BaseException | None = None

    def close(self) -> None:
        provider, self.provider = self.provider, None
        transport, self.transport = self.transport, None
        try:
            if provider is not None:
                provider.close()
        finally:
            # RequestsTransport.close is idempotent. Also close it if the
            # provider fails before reaching its client/transport cleanup.
            if transport is not None:
                transport.close()


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
        self._disposed = False
        self._active_operations = 0
        self._cleanup_credential_when_idle = False
        self._credential_init_lock = threading.Lock()
        # This condition protects bookkeeping only. Network I/O has a query
        # lock (or a private probe), while leases keep shutdown from closing
        # resources that a reader or an unfinished load still owns.
        self._condition = threading.Condition()
        atexit.register(self.close_all)

    def ping(self) -> None:
        """Probe the store over a fresh connection.

        This deliberately does NOT reuse the providers cached by select().
        A cached refresh may be an interval no-op or report client backoff
        without making an HTTP request. Only a fresh load probes current
        reachability, independently of another query's in-flight I/O.
        """
        with self._borrow() as probe:
            try:
                try:
                    self._load_provider(probe, _PROBE_KEY_FILTER, None, (), probe=True)
                finally:
                    probe.close()
            except AzureSdkNotInstalledError:
                raise
            except Exception as error:
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
        with self._condition:
            if self._closed:
                return
            cache_key = (key_filter, label_filter, tuple(trim_prefixes))
            resources = self._providers.pop(cache_key, None)
            if resources is None:
                return
            self._active_operations += 1
        try:
            with self._condition:
                self._condition.wait_for(lambda: resources.borrowers == 0)
            resources.close()
        finally:
            self._finish_operation()

    def close_all(self) -> None:
        """End the store lifetime, closing all providers and its credential.

        Unlike close(query), this is terminal. Context-manager exit and
        normal process exit call it too; repeated calls are harmless.
        """
        with self._condition:
            if self._closed:
                self._condition.wait_for(lambda: self._disposed)
                return
            self._closed = True
            atexit.unregister(self.close_all)
            self._condition.wait_for(lambda: self._active_operations == 0)
            resources = list(self._providers.values())
            self._providers.clear()
            credential, self._credential = self._credential, None
        try:
            with ExitStack() as cleanup:
                if credential is not None:
                    cleanup.callback(self._safe_close, credential)
                for resource in resources:
                    cleanup.callback(self._safe_close, resource)
        finally:
            with self._condition:
                self._disposed = True
                self._condition.notify_all()

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

    @contextmanager
    def _borrow(self, cache_key: tuple | None = None):
        with self._condition:
            self._ensure_open()
            self._active_operations += 1
            if cache_key is None:
                resources = _ProviderResources()
            else:
                resources = self._providers.get(cache_key)
                if resources is None:
                    resources = _ProviderResources()
                    self._providers[cache_key] = resources
                resources.borrowers += 1
        try:
            yield resources
        except BaseException:
            with self._condition:
                self._cleanup_credential_when_idle = True
            raise
        finally:
            if cache_key is not None:
                with self._condition:
                    resources.borrowers -= 1
                    self._condition.notify_all()
            self._finish_operation()

    def _finish_operation(self) -> None:
        while True:
            with self._condition:
                if (
                    self._active_operations == 1
                    and self._cleanup_credential_when_idle
                    and not self._providers
                    and not self._closed
                ):
                    credential, self._credential = self._credential, None
                    self._cleanup_credential_when_idle = False
                else:
                    if self._active_operations == 1:
                        self._cleanup_credential_when_idle = False
                    self._active_operations -= 1
                    self._condition.notify_all()
                    return
            try:
                # Remain counted during cleanup, then recheck: another load
                # may have failed with a new credential while this one closed.
                if credential is not None:
                    self._safe_close(credential)
            except BaseException:
                with self._condition:
                    self._active_operations -= 1
                    self._condition.notify_all()
                raise

    def _get_credential(self, factory):
        with self._credential_init_lock:
            with self._condition:
                credential = self._credential
            if credential is None:
                credential = factory()
                with self._condition:
                    self._credential = credential
            return credential

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> ConfigValues:
        """Read values and explicit refresh errors; initial-load failures raise."""
        cache_key = (key_filter, label_filter, tuple(trim_prefixes))
        with self._borrow(cache_key) as resources:
            with resources.io_lock:
                if resources.load_error is not None:
                    raise resources.load_error
                if resources.provider is None:
                    try:
                        self._load_provider(resources, key_filter, label_filter, trim_prefixes)
                    except BaseException as error:
                        failure = error
                        if isinstance(error, Exception) and not isinstance(error, AzureSdkNotInstalledError):
                            failure = ConfigStoreUnavailableError(
                                f"could not load configuration from {self._endpoint!r}: {error}"
                            )
                            failure.__cause__ = error
                        resources.load_error = failure
                        with self._condition:
                            if self._providers.get(cache_key) is resources:
                                del self._providers[cache_key]
                        raise failure
                else:
                    resources.refresh_errors.clear()
                    try:
                        resources.provider.refresh()
                    except Exception as error:
                        self._on_refresh_error(resources.refresh_errors, error)
                return ConfigValues(dict(resources.provider), refresh_errors=resources.refresh_errors)

    def _load_provider(
        self,
        resources: _ProviderResources,
        key_filter: str,
        label_filter: str | None,
        trim_prefixes: Sequence[str],
        *,
        probe: bool = False,
    ) -> None:
        load, SettingSelector, DefaultAzureCredential = _import_sdk()
        transport = None
        try:
            credential = self._get_credential(DefaultAzureCredential)
            transport_options = {
                "connection_timeout": 2 if probe else 5,
                "read_timeout": 2 if probe else 5,
            }
            transport = _create_transport(**transport_options)
            resources.transport = transport
            resources.provider = load(
                endpoint=self._endpoint,
                # SDK 2.5.0 closes clients/transports, not this borrowed
                # credential. Only the store owns and closes the credential.
                credential=credential,
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
                on_refresh_error=lambda error: self._on_refresh_error(resources.refresh_errors, error),
            )
        except BaseException:
            # SDK 2.5.0 load() does not return/close its provider on failure.
            # Retaining the supplied transport lets us close its HTTP session.
            if transport is not None:
                self._safe_close(transport)
                resources.transport = None
            raise

    def _on_refresh_error(self, errors: list[Exception], error: Exception) -> None:
        """Report this read's failure without invalidating usable shared data."""
        failure = ConfigStoreUnavailableError(
            f"could not refresh configuration from {self._endpoint!r}: {error}"
        )
        failure.__cause__ = error
        errors.append(failure)
