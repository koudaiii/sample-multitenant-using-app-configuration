# Provider Lifecycle and Staleness (R1/R2) Implementation Plan

> **Topic migration note (2026-09-08):** Provider lifecycle implementation
> moved from planned Topic 17 into Topic 11 / PR #15 so the real Azure provider
> is independently safe when introduced. This document preserves the original
> plan for provenance, but its `max_staleness_seconds` proposal was superseded
> by corrective commit `3585676` and must not be implemented. The final design
> uses `TenantConfig.close`: cache expiry/eviction closes and removes the exact
> tenant provider, and the next load creates a fresh provider. Sample 04 and
> root README review-table work remain outside Topic 11.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the independent review's R1 (SDK providers are never disposed when the outer LRU cache evicts a tenant) and R2 (TTL expiry doesn't force real staleness bounds — a permanently unreachable real store never surfaces as unavailable once a provider has been created).

**Architecture:** Add an optional `close` callback to `TenantConfig`; `TenantConfigCache` calls it when it evicts (LRU) or expires (TTL) an entry. Each sample's `source_*.py` wires `close` to ask the underlying store to drop only that tenant's own provider (never the shared one). `AzureAppConfigurationStore` gains a `close(...)` method (drops+closes one provider) and a `max_staleness_seconds` guard — provider-level "last successful refresh" tracking that raises `ConfigStoreUnavailableError` once a real store has been failing to refresh for too long, instead of silently returning arbitrarily old cached values forever. `FakeAppConfigurationStore` gets a matching no-op `close(...)` so every source can call it uniformly regardless of which store backs it.

**Tech Stack:** Python 3.14, pytest. No new dependencies. `tests/test_azure_source.py`'s existing `_import_sdk` seam (`_FakeSdk`/`_FakeProvider`) lets R1/R2 be tested without the real Azure SDK.

**Spec:** `docs/superpowers/specs/2026-09-06-provider-lifecycle-and-staleness-design.md`

## Global Constraints

- `TenantConfig.close` defaults to `None`. Every existing call site (`TenantConfig(tenant_id=..., values=..., reload=...)`) must keep working unchanged — `close` is keyword-only via the dataclass field, never positional.
- `FakeAppConfigurationStore.close(...)` is a true no-op: it must not raise, log, or otherwise have an observable effect, so every sample 01-04 test that runs against the fake continues to pass unmodified.
- `AzureAppConfigurationStore`'s `__init__` keeps `endpoint` as the only required, positional argument. `max_staleness_seconds` and `clock` are new keyword-only parameters with defaults — every existing call site (`AzureAppConfigurationStore(endpoint)` in each sample's `app.py`) must keep working unchanged.
- Do not modify `src/mtappconfig/webapp.py`, `src/mtappconfig/tenants.py`, `src/mtappconfig/observability.py`, or any sample's `app.py`/`main.bicep`/`seed_*.py`.
- Do not modify `docs/reviews/2026-09-06-independent-review.md` — it is a review record, not a living document. Reflect resolution status only in the root `README.md`'s "第三者レビューの指摘事項" table.
- `close()`'s failure must never break eviction/expiry: `TenantConfigCache` must catch and log any exception `entry.config.close()` raises, exactly the way it already catches and logs `entry.config.refresh()` failures.
- The default `max_staleness_seconds` is `refresh_interval_seconds * 3.0` when not explicitly passed.

---

### Task 1: `TenantConfig.close` + cache eviction/expiry wiring (TDD)

**Files:**
- Modify: `src/mtappconfig/source.py`
- Modify: `src/mtappconfig/fake.py`
- Modify: `src/mtappconfig/cache.py`
- Modify: `tests/test_cache.py`

**Interfaces:**
- Produces: `TenantConfig.close: Callable[[], None] | None` (new field, default `None`); `FakeAppConfigurationStore.close(key_filter: str = "*", label_filter: str | None = None, trim_prefixes: Sequence[str] = ()) -> None` (no-op); `TenantConfigCache._safe_close(entry: _Entry) -> None` (internal helper, calls `entry.config.close()` if set, catches and logs any exception).

- [ ] **Step 1: Add the `close` field to `TenantConfig`**

In `src/mtappconfig/source.py`, add one field to the `TenantConfig` dataclass (do not change `refresh()`):

```python
@dataclass
class TenantConfig:
    """One tenant's resolved settings, plus a way to reload them."""

    tenant_id: str
    values: Mapping[str, str]
    reload: Callable[[], Mapping[str, str]] | None = field(default=None, repr=False)
    close: Callable[[], None] | None = field(default=None, repr=False)

    def refresh(self) -> bool:
        """Reload from the store. Returns True when a value actually changed.

        Callers are expected to rate-limit this; see `TenantConfigCache`.
        """
        if self.reload is None:
            return False
        latest = self.reload()
        if latest == self.values:
            return False
        self.values = latest
        return True
```

- [ ] **Step 2: Add the no-op `close` to `FakeAppConfigurationStore`**

In `src/mtappconfig/fake.py`, add this method to `FakeAppConfigurationStore` (anywhere among its other public methods, e.g. right after `ping`):

```python
    def close(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> None:
        """No-op: the fake holds nothing that needs disposing.

        Exists so every source_*.py can call store.close(...) uniformly,
        without checking whether the store backing it is real or fake.
        """
```

- [ ] **Step 3: Write the failing tests in `tests/test_cache.py`**

Extend `RecordingSource` (add `fail_close` and `closes` tracking) and add four new tests. Replace the existing `RecordingSource` class with:

```python
class RecordingSource:
    """Counts loads and reloads so cache behaviour is directly observable."""

    name = "recording"

    def __init__(self, values=None, fail_reload=False, fail_close=False):
        self.values = values or {"LogLevel": "Warning"}
        self.loads = []
        self.reloads = 0
        self.closes = []
        self.fail_reload = fail_reload
        self.fail_close = fail_close

    def load(self, tenant_id):
        self.loads.append(tenant_id)

        def reload():
            self.reloads += 1
            if self.fail_reload:
                raise ConfigStoreUnavailableError("store is down")
            return self.values

        def close():
            self.closes.append(tenant_id)
            if self.fail_close:
                raise RuntimeError("close failed")

        return TenantConfig(tenant_id=tenant_id, values=dict(self.values), reload=reload, close=close)

    def ping(self):
        return None
```

Add these four tests (anywhere after `test_each_tenant_is_cached_separately`):

```python
def test_lru_eviction_closes_the_evicted_tenants_config(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, max_entries=2)
    cache.get("tenant-a")
    cache.get("tenant-b")
    cache.get("tenant-a")  # tenant-a is now the most recently used
    cache.get("tenant-c")  # evicts tenant-b

    assert source.closes == ["tenant-b"]


def test_ttl_expiry_closes_the_expired_tenants_config(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, ttl_seconds=300.0)
    cache.get("tenant-a")

    clock.advance(301)
    cache.get("tenant-a")

    assert source.closes == ["tenant-a"]


def test_a_failing_close_does_not_prevent_eviction(clock):
    source = RecordingSource(fail_close=True)
    cache = TenantConfigCache(source, clock=clock, max_entries=1)
    cache.get("tenant-a")

    cache.get("tenant-b")  # evicts tenant-a; close() raises but must be swallowed

    assert set(cache.snapshot()["entries"]) == {"tenant-b"}
    assert cache.stats.evictions == 1


def test_close_is_optional_and_does_not_break_eviction(clock):
    # ThreadSafeSource (defined below in this file) never sets `close` —
    # eviction must not blow up on a None close callback.
    source = ThreadSafeSource()
    cache = TenantConfigCache(source, clock=clock, max_entries=1)
    cache.get("tenant-a")

    cache.get("tenant-b")  # evicts tenant-a, whose config.close is None

    assert set(cache.snapshot()["entries"]) == {"tenant-b"}
```

Note: `ThreadSafeSource` is defined later in the same file (after `_aggressive_thread_switching`). Python resolves this fine since `test_close_is_optional_and_does_not_break_eviction` only references it at call time, not at module-definition time — pytest collects the whole module before running any test, so the name exists by the time the test body executes. No reordering needed.

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cache.py -v -k "closes_the or close"`
Expected: FAIL — `TenantConfigCache` doesn't call `close()` on evict/expire yet, so `source.closes` stays empty and the first two new tests fail their assertions. (The third and fourth may pass vacuously since nothing calls `close()` at all yet — that's fine, they'll still be true positives once Step 5 lands, and Step 6 confirms all four together.)

- [ ] **Step 5: Implement the cache-side wiring in `src/mtappconfig/cache.py`**

Add a `_safe_close` method to `TenantConfigCache` and call it from both the TTL-expiry branch and `_evict_over_capacity`:

```python
    def _safe_close(self, entry: _Entry) -> None:
        if entry.config.close is None:
            return
        try:
            entry.config.close()
        except Exception:
            self._logger.warning(
                "closing evicted tenant's config source failed",
                extra={"tenant_id": entry.config.tenant_id, "event": "config.close.failed"},
                exc_info=True,
            )
```

In `get()`, change the TTL-expiry branch from:

```python
            if entry is not None and now - entry.loaded_at >= self._ttl:
                del self._entries[tenant_id]
                self.stats.expirations += 1
                entry = None
```

to:

```python
            if entry is not None and now - entry.loaded_at >= self._ttl:
                self._safe_close(entry)
                del self._entries[tenant_id]
                self.stats.expirations += 1
                entry = None
```

Change `_evict_over_capacity` from:

```python
    def _evict_over_capacity(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1
```

to:

```python
    def _evict_over_capacity(self) -> None:
        while len(self._entries) > self._max_entries:
            _, entry = self._entries.popitem(last=False)
            self._safe_close(entry)
            self.stats.evictions += 1
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cache.py -v`
Expected: PASS — all tests in this file, old and new (4 new tests), pass.

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `uv run pytest`
Expected: PASS, same pre-existing count plus these 4 new tests. `FakeAppConfigurationStore.close` is never called by any existing sample test, so nothing else changes.

- [ ] **Step 8: Commit**

```bash
git add src/mtappconfig/source.py src/mtappconfig/fake.py src/mtappconfig/cache.py tests/test_cache.py
git commit -m "feat: dispose evicted/expired tenants' config sources (R1)"
```

---

### Task 2: `AzureAppConfigurationStore.close` + staleness guard (TDD)

**Files:**
- Modify: `src/mtappconfig/azure_source.py`
- Modify: `tests/test_azure_source.py`

**Interfaces:**
- Produces: `AzureAppConfigurationStore(endpoint, *, refresh_interval_seconds=30.0, startup_timeout_seconds=100, probe_timeout_seconds=5, max_staleness_seconds=None, clock=time.monotonic)`; `.close(key_filter="*", label_filter=None, trim_prefixes=()) -> None`. Task 3's `source_*.py` files call `.close(...)` with this exact signature.

- [ ] **Step 1: Extend the SDK-seam test fixtures in `tests/test_azure_source.py`**

Replace `_FakeProvider` and `_FakeSdk` with versions that support simulating a refresh failure and capturing the `on_refresh_error` callback:

```python
class _FakeProvider(dict):
    """Stands in for AzureAppConfigurationProvider, which is a Mapping."""

    def __init__(self, values=None, on_refresh_error=None):
        super().__init__(values or {"LogLevel": "Warning"})
        self.refresh_calls = 0
        self.closed = False
        self._on_refresh_error = on_refresh_error
        self.fail_next_refresh = False

    def refresh(self):
        self.refresh_calls += 1
        if self.fail_next_refresh and self._on_refresh_error is not None:
            self._on_refresh_error(RuntimeError("refresh failed"))

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
        provider = _FakeProvider(on_refresh_error=kwargs.get("on_refresh_error"))
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
```

(Only additions: `on_refresh_error` param on `_FakeProvider.__init__`, `fail_next_refresh` flag, the `refresh()` body, and `_FakeSdk.load` passing `kwargs.get("on_refresh_error")` through. Everything else — `close`, `selector`, `credential`, `install` — is unchanged from the current file.)

Add a `FakeClock` helper (mirrors the one in `tests/test_cache.py`, but this file doesn't import from there — define it locally):

```python
class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
```

- [ ] **Step 2: Write the failing tests**

Add these tests to `tests/test_azure_source.py` (after the existing `test_select_wraps_a_load_failure`, before the `@pytest.mark.live` test):

```python
def test_close_drops_and_closes_only_the_matching_provider(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")
    store.select(key_filter="tenant-a/*")
    store.select(key_filter="_shared/*")

    store.close(key_filter="tenant-a/*")

    assert sdk.providers[0].closed is True
    assert sdk.providers[1].closed is False, "the shared provider must not be touched"

    # Selecting the same query again creates a brand-new provider, since the
    # old one was dropped from _providers.
    store.select(key_filter="tenant-a/*")
    assert len(sdk.load_calls) == 3


def test_close_on_a_query_with_no_provider_is_a_no_op(sdk):
    store = AzureAppConfigurationStore("https://example.azconfig.io")

    store.close(key_filter="tenant-a/*")  # must not raise


def test_max_staleness_defaults_to_three_times_the_refresh_interval(sdk):
    """Behavioral check of the default (no max_staleness_seconds passed):
    refresh_interval_seconds=10.0 implies a 30s default staleness bound."""
    clock = FakeClock()
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io", refresh_interval_seconds=10.0, clock=clock
    )
    store.select(key_filter="tenant-a/*")
    sdk.providers[0].fail_next_refresh = True

    clock.advance(29.0)
    store.select(key_filter="tenant-a/*")  # under the 30s default: must not raise yet

    clock.advance(2.0)  # now 31s since the last success
    with pytest.raises(ConfigStoreUnavailableError, match="has not refreshed"):
        store.select(key_filter="tenant-a/*")


def test_repeated_successful_refreshes_never_trip_the_staleness_guard(sdk):
    clock = FakeClock()
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io", max_staleness_seconds=60.0, clock=clock
    )
    store.select(key_filter="tenant-a/*")

    for _ in range(5):
        clock.advance(50.0)
        store.select(key_filter="tenant-a/*")  # must not raise


def test_a_permanently_failing_refresh_eventually_raises_unavailable(sdk):
    clock = FakeClock()
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io", max_staleness_seconds=60.0, clock=clock
    )
    store.select(key_filter="tenant-a/*")
    sdk.providers[0].fail_next_refresh = True

    clock.advance(61.0)

    with pytest.raises(ConfigStoreUnavailableError, match="has not refreshed"):
        store.select(key_filter="tenant-a/*")


def test_a_transient_refresh_failure_recovers_before_the_staleness_limit(sdk):
    clock = FakeClock()
    store = AzureAppConfigurationStore(
        "https://example.azconfig.io", max_staleness_seconds=60.0, clock=clock
    )
    store.select(key_filter="tenant-a/*")

    sdk.providers[0].fail_next_refresh = True
    clock.advance(10.0)
    store.select(key_filter="tenant-a/*")  # fails once, but well under the limit

    sdk.providers[0].fail_next_refresh = False
    clock.advance(10.0)
    store.select(key_filter="tenant-a/*")  # succeeds, resets the staleness clock

    clock.advance(55.0)  # would exceed 60s from the first failure, but not from this success
    store.select(key_filter="tenant-a/*")  # must not raise
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_azure_source.py -v`
Expected: FAIL — `close`, `max_staleness_seconds`, `_max_staleness`, and `clock` don't exist yet on `AzureAppConfigurationStore`.

- [ ] **Step 4: Implement the changes in `src/mtappconfig/azure_source.py`**

Replace the file's contents with:

```python
"""Binding to the real Azure App Configuration provider.

This module presents the same surface as `FakeAppConfigurationStore`, so the
three pattern implementations run unchanged against a real store.

Every azure import happens inside a function. The base install deliberately
omits the App Configuration SDK, and a module-scope import would break the
whole application for anyone who has not installed the `azure` extra.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

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


class AzureAppConfigurationStore:
    """One real App Configuration store, behind the fake store's interface."""

    def __init__(
        self,
        endpoint: str,
        *,
        refresh_interval_seconds: float = 30.0,
        startup_timeout_seconds: int = 100,
        probe_timeout_seconds: int = 5,
        max_staleness_seconds: float | None = None,
        clock=time.monotonic,
    ) -> None:
        self.name = endpoint
        self._endpoint = endpoint
        self._refresh_interval = refresh_interval_seconds
        self._startup_timeout = startup_timeout_seconds
        # A readiness probe must fail fast rather than hang for the full
        # startup timeout, so it gets its own, much shorter budget.
        self._probe_timeout = probe_timeout_seconds
        # How long a provider may keep serving cached values after its last
        # successful refresh before select() gives up on it. Defaults to
        # tolerating a couple of consecutive refresh-interval failures
        # before reporting the store unavailable, rather than either
        # failing on the very first blip or never detecting a permanently
        # dead store at all (see docs/reviews/2026-09-06-independent-review.md, R2).
        self._max_staleness = (
            refresh_interval_seconds * 3.0
            if max_staleness_seconds is None
            else max_staleness_seconds
        )
        self._clock = clock
        # One provider per distinct query. The provider holds the connection
        # and its own refresh bookkeeping, so it is worth keeping around —
        # until close() or the owning TenantConfigCache says otherwise.
        self._providers: dict[tuple, object] = {}
        self._last_success_at: dict[tuple, float] = {}
        self._pending_failure: dict[tuple, bool] = {}

    def ping(self) -> None:
        """Probe the store over a fresh connection.

        This deliberately does NOT reuse the providers cached by select().
        A cached provider's refresh() is a no-op inside the refresh interval
        and does not raise when it fails, so probing through the cache would
        report healthy forever after the first success — the exact opposite of
        what a readiness check is for.
        """
        load, SettingSelector, DefaultAzureCredential = _import_sdk()
        try:
            probe = load(
                endpoint=self._endpoint,
                credential=DefaultAzureCredential(),
                selects=[
                    SettingSelector(
                        key_filter=_PROBE_KEY_FILTER,
                        label_filter=_NULL_LABEL,
                    )
                ],
                startup_timeout=self._probe_timeout,
            )
        except Exception as error:
            raise ConfigStoreUnavailableError(
                f"App Configuration store {self._endpoint!r} is unreachable: {error}"
            ) from error
        probe.close()

    def close(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> None:
        """Drop and close the provider for this exact query, if one exists.

        Callers pass the same (key_filter, label_filter, trim_prefixes) they
        used with select() — see source_*.py's close closures, which close
        only their own tenant's provider, never a shared one other tenants
        still use.
        """
        cache_key = (key_filter, label_filter, tuple(trim_prefixes))
        provider = self._providers.pop(cache_key, None)
        self._last_success_at.pop(cache_key, None)
        self._pending_failure.pop(cache_key, None)
        if provider is not None:
            provider.close()

    def select(
        self,
        key_filter: str = "*",
        label_filter: str | None = None,
        trim_prefixes: Sequence[str] = (),
    ) -> dict[str, str]:
        cache_key = (key_filter, label_filter, tuple(trim_prefixes))
        provider = self._providers.get(cache_key)
        if provider is None:
            provider = self._create_provider(key_filter, label_filter, trim_prefixes, cache_key)
            self._providers[cache_key] = provider
            self._last_success_at[cache_key] = self._clock()
        else:
            # Activity-driven refresh: a no-op until refresh_interval elapses,
            # so calling this on every request costs nothing most of the time.
            self._pending_failure[cache_key] = False
            provider.refresh()
            if not self._pending_failure.pop(cache_key):
                self._last_success_at[cache_key] = self._clock()

        staleness = self._clock() - self._last_success_at[cache_key]
        if staleness >= self._max_staleness:
            raise ConfigStoreUnavailableError(
                f"App Configuration store {self._endpoint!r} has not refreshed "
                f"successfully in {staleness:.0f}s (max {self._max_staleness:.0f}s)"
            )
        return dict(provider)

    def _create_provider(
        self,
        key_filter: str,
        label_filter: str | None,
        trim_prefixes: Sequence[str],
        cache_key: tuple,
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
                on_refresh_error=lambda error, key=cache_key: self._on_refresh_error(key, error),
            )
        except Exception as error:
            raise ConfigStoreUnavailableError(
                f"could not load configuration from {self._endpoint!r}: {error}"
            ) from error

    def _on_refresh_error(self, cache_key: tuple, error: Exception) -> None:
        """Log and swallow: the cache keeps serving the last known values
        until max_staleness_seconds decides that's no longer acceptable."""
        _logger.warning(
            "app configuration refresh failed",
            extra={"event": "appconfig.refresh.failed"},
            exc_info=error,
        )
        if cache_key in self._pending_failure:
            self._pending_failure[cache_key] = True
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_azure_source.py -v`
Expected: PASS — all tests in this file, old and new, pass.

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `uv run pytest`
Expected: PASS. No sample's `app.py` passes `max_staleness_seconds` or `clock`, so every existing `AzureAppConfigurationStore(endpoint)` call site is unaffected — this whole file is exercised only by `tests/test_azure_source.py` and (indirectly, if `APPCONFIG_ENDPOINT` is set) each sample's `app.py`, neither of which this task changes.

- [ ] **Step 7: Commit**

```bash
git add src/mtappconfig/azure_source.py tests/test_azure_source.py
git commit -m "feat: close individual providers and enforce a staleness bound (R1, R2)"
```

---

### Task 3: Wire `close` into each sample's source

**Files:**
- Modify: `samples/01-shared-store-key-prefix/source_key_prefix.py`
- Modify: `samples/02-shared-store-label/source_label.py`
- Modify: `samples/03-store-per-tenant/source_store_per_tenant.py`
- Modify: `samples/04-snapshot-references/source_snapshot_references.py`

**Interfaces:**
- Consumes: `TenantConfig.close` (Task 1), `store.close(key_filter=..., label_filter=..., trim_prefixes=...)` on both `FakeAppConfigurationStore` (Task 1) and `AzureAppConfigurationStore` (Task 2) — both stores now expose the identical `close(...)` signature, so every source below can call it the same way regardless of which store backs it.

- [ ] **Step 1: `samples/01-shared-store-key-prefix/source_key_prefix.py`**

Change:

```python
    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            shared = self._store.select(
                key_filter=f"{SHARED_PREFIX}*",
                trim_prefixes=[SHARED_PREFIX],
            )
            # The tenant id reaching this line has already been validated by
            # TenantRegistry.resolve. An unvalidated id here would let a caller
            # pass "*" and read every tenant's settings at once.
            tenant = self._store.select(
                key_filter=f"{tenant_id}/*",
                trim_prefixes=[f"{tenant_id}/"],
            )
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)
```

to:

```python
    def load(self, tenant_id: str) -> TenantConfig:
        def reload() -> dict[str, str]:
            shared = self._store.select(
                key_filter=f"{SHARED_PREFIX}*",
                trim_prefixes=[SHARED_PREFIX],
            )
            # The tenant id reaching this line has already been validated by
            # TenantRegistry.resolve. An unvalidated id here would let a caller
            # pass "*" and read every tenant's settings at once.
            tenant = self._store.select(
                key_filter=f"{tenant_id}/*",
                trim_prefixes=[f"{tenant_id}/"],
            )
            return {**shared, **tenant}

        def close() -> None:
            # Close only this tenant's own provider. The shared-prefix
            # provider is used by every tenant and must outlive any single
            # tenant's eviction.
            self._store.close(
                key_filter=f"{tenant_id}/*",
                trim_prefixes=[f"{tenant_id}/"],
            )

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload, close=close)
```

- [ ] **Step 2: `samples/02-shared-store-label/source_label.py`**

Change:

```python
        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)
```

to:

```python
        def close() -> None:
            # Close only this tenant's own labelled provider. The unlabelled
            # (shared) provider is used by every tenant.
            self._store.close(key_filter="*", label_filter=tenant_id)

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload, close=close)
```

(Add the `close` function above the `return`, right after the `reload` function's closing line, matching the file's existing structure.)

- [ ] **Step 3: `samples/03-store-per-tenant/source_store_per_tenant.py`**

Change:

```python
    def load(self, tenant_id: str) -> TenantConfig:
        store = self._store_for(tenant_id)

        def reload() -> dict[str, str]:
            shared = self._shared_store.select(key_filter="*")
            # No prefix and no label are needed: the store itself is the
            # boundary, which is what makes the isolation strong here.
            tenant = store.select(key_filter="*")
            return {**shared, **tenant}

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload)
```

to:

```python
    def load(self, tenant_id: str) -> TenantConfig:
        store = self._store_for(tenant_id)

        def reload() -> dict[str, str]:
            shared = self._shared_store.select(key_filter="*")
            # No prefix and no label are needed: the store itself is the
            # boundary, which is what makes the isolation strong here.
            tenant = store.select(key_filter="*")
            return {**shared, **tenant}

        def close() -> None:
            # Close the tenant's own dedicated store's provider. The shared
            # store is untouched — it belongs to every tenant, not this one.
            store.close(key_filter="*")

        return TenantConfig(tenant_id=tenant_id, values=reload(), reload=reload, close=close)
```

- [ ] **Step 4: `samples/04-snapshot-references/source_snapshot_references.py`**

Apply the exact same change as Step 1 (this file mirrors sample 01's structure — same `SHARED_PREFIX`, same shared+tenant `select`/`trim_prefixes` shape). Add the same `close` closure and pass `close=close` to the returned `TenantConfig`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, same count as after Task 2 (this task adds no new tests — it wires existing, tested behavior into four files whose own test suites already exercise `load()`). `tests/test_pattern_contract.py` and every sample's own tests must still pass unchanged, since `close` is never invoked in any of those tests (nothing there triggers eviction/expiry) and `FakeAppConfigurationStore.close` is a no-op even if it were.

- [ ] **Step 6: Commit**

```bash
git add samples/01-shared-store-key-prefix/source_key_prefix.py \
        samples/02-shared-store-label/source_label.py \
        samples/03-store-per-tenant/source_store_per_tenant.py \
        samples/04-snapshot-references/source_snapshot_references.py
git commit -m "feat: wire per-tenant provider disposal into every sample's source"
```

---

### Task 4: Root README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the 信頼性 (reliability) bullet about TTL expiry**

Find this text in `README.md`:

```markdown
- **すでにキャッシュ済みのテナントは**、設定ストアが落ちても直近の値で応答を続けます
  （`src/mtappconfig/cache.py` のリフレッシュ失敗はログに記録して握りつぶすだけです）。
  以下のTTL切れ後の503はフェイクで確認した動作で、実Azureでは上記R2の違いがあります。
```

Replace it with:

```markdown
- **すでにキャッシュ済みのテナントは**、設定ストアが落ちても直近の値で応答を続けます
  （`src/mtappconfig/cache.py` のリフレッシュ失敗はログに記録して握りつぶすだけです）。
  実Azure接続では、`AzureAppConfigurationStore` が「最後に成功したリフレッシュからの
  経過時間」(既定で `refresh_interval_seconds` の3倍)を追跡し、これを超えると
  ストアが生きていても `ConfigStoreUnavailableError` を送出します(R2対応、
  `tests/test_azure_source.py` の SDK シームで検証済み。実 Azure 接続そのものでは
  未検証)。以下のTTL切れ後の503はフェイクで確認した動作です。
```

- [ ] **Step 2: Update the パフォーマンス効率 bullet about LRU**

Find:

```markdown
- TTL + LRU で外側キャッシュのエントリー数を制限します。実Azureのprovider保持には上記R1の制約があります。
```

Replace with:

```markdown
- TTL + LRU で外側キャッシュのエントリー数を制限します。エントリーが evict/expire
  されると、そのテナント専用の SDK provider も閉じられます(R1対応、共有プレフィックス/
  ラベル/ストアの provider は他のテナントが使うため対象外)。`tests/test_azure_source.py`
  の SDK シームで検証済みで、実 Azure 接続そのものでは未検証です。
```

- [ ] **Step 3: Update the R1 and R2 rows in "第三者レビューの指摘事項"**

Find the table row starting with `| R1 | P1 |` and change its 対応条件・現在の状態 column
(the text after the last `|`) from:

```
未対応。providerの所有・破棄を外側cacheと連動し、残存数と解放を検証する
```

to:

```
対応済み。`TenantConfig.close` を追加し、`TenantConfigCache` がevict/expire時に呼ぶ。`AzureAppConfigurationStore.close()` が該当providerだけを`_providers`から外して`close()`する。`tests/test_azure_source.py`のSDKシームで、対象providerのみが閉じられ共有providerは影響を受けないことを検証済み。実Azure接続そのものでの検証はしていない
```

Find the table row starting with `| R2 | P1 |` and change its 対応条件・現在の状態 column from:

```
未対応。TTLが管理する期限と許容する古さを定義し、最終成功時刻・期限超過後のHTTP応答を検証する
```

to:

```
対応済み。`AzureAppConfigurationStore`に`max_staleness_seconds`(既定`refresh_interval_seconds`の3倍)を追加し、最後に成功したrefreshからの経過時間がこれを超えると`ConfigStoreUnavailableError`を送出する。`tests/test_azure_source.py`のSDKシームで、恒久的な失敗が最終的に例外化されること、一時的な失敗からの回復で古さの計測がリセットされることを検証済み。実Azure接続そのものでの検証はしていない
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS (README-only change; no test count change).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: record R1/R2 resolution in the third-party review table"
```

---

### Task 5: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite once more from a clean state**

Run: `uv run pytest -v`
Expected: every test passes, including all new tests from Tasks 1 and 2. Confirm the total pass count increased by exactly 10 over the count before this plan started (4 from Task 1, 6 from Task 2), with no other change.

- [ ] **Step 2: Confirm no sample's runtime behavior changed for the fake-store path**

Run each sample's own test file once more, e.g.:
```bash
uv run pytest samples/01-shared-store-key-prefix/ samples/02-shared-store-label/ samples/03-store-per-tenant/ samples/04-snapshot-references/ -v
```
Expected: identical pass/fail results to before this plan — this task only adds a `close` callback that the fake store's no-op implementation never observably acts on.

- [ ] **Step 3: Confirm `git log` shows one commit per task**

Run: `git log --oneline -5`
Expected: 4 commits from Tasks 1-4 (Task 5 makes no commit of its own).
