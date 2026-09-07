"""Per-tenant caching: the guidance's application-side caching, made observable."""

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from mtappconfig.cache import TenantConfigCache
from mtappconfig.source import ConfigStoreUnavailableError, TenantConfig


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingSource:
    """Counts loads and reloads so cache behaviour is directly observable."""

    name = "recording"

    def __init__(self, values=None, fail_reload=False):
        self.values = values or {"LogLevel": "Warning"}
        self.loads = []
        self.reloads = 0
        self.fail_reload = fail_reload

    def load(self, tenant_id):
        self.loads.append(tenant_id)

        def reload():
            self.reloads += 1
            if self.fail_reload:
                raise ConfigStoreUnavailableError("store is down")
            return self.values

        return TenantConfig(tenant_id=tenant_id, values=dict(self.values), reload=reload)

    def ping(self):
        return None


@pytest.fixture
def clock():
    return FakeClock()


def test_first_get_is_a_miss_and_loads_from_the_source(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock)

    config = cache.get("tenant-a")

    assert config.values == {"LogLevel": "Warning"}
    assert source.loads == ["tenant-a"]
    assert cache.stats.misses == 1
    assert cache.stats.hits == 0


def test_second_get_is_a_hit_and_does_not_reload_within_the_interval(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)

    cache.get("tenant-a")
    cache.get("tenant-a")

    assert source.loads == ["tenant-a"]
    assert source.reloads == 0, "refresh within the interval must be a no-op"
    assert cache.stats.hits == 1


def test_refresh_happens_once_the_interval_has_passed(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)
    cache.get("tenant-a")

    clock.advance(31)
    cache.get("tenant-a")

    assert source.reloads == 1
    assert source.loads == ["tenant-a"], "a refresh is not a reload from scratch"


def test_ttl_expiry_reloads_from_the_source(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, ttl_seconds=300.0)
    cache.get("tenant-a")

    clock.advance(301)
    cache.get("tenant-a")

    assert source.loads == ["tenant-a", "tenant-a"]
    assert cache.stats.expirations == 1
    assert cache.stats.misses == 2


def test_lru_evicts_the_least_recently_used_tenant(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, max_entries=2)

    cache.get("tenant-a")
    cache.get("tenant-b")
    cache.get("tenant-a")  # tenant-a is now the most recently used
    cache.get("tenant-c")  # evicts tenant-b

    assert cache.stats.evictions == 1
    assert set(cache.snapshot()["entries"]) == {"tenant-a", "tenant-c"}


def test_each_tenant_is_cached_separately(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock)

    cache.get("tenant-a")
    cache.get("tenant-b")

    assert source.loads == ["tenant-a", "tenant-b"]
    assert cache.stats.misses == 2


def test_a_failing_refresh_keeps_serving_the_cached_values(clock):
    """WAF reliability: an unreachable store degrades, it does not fail."""
    source = RecordingSource(fail_reload=True)
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)
    cache.get("tenant-a")

    clock.advance(31)
    config = cache.get("tenant-a")

    assert config.values == {"LogLevel": "Warning"}
    assert cache.stats.refresh_failures == 1


def test_a_failing_refresh_is_not_retried_until_the_next_interval(clock):
    source = RecordingSource(fail_reload=True)
    cache = TenantConfigCache(source, clock=clock, refresh_interval_seconds=30.0)
    cache.get("tenant-a")
    clock.advance(31)
    cache.get("tenant-a")

    cache.get("tenant-a")

    assert source.reloads == 1


def test_snapshot_reports_configuration_and_stats(clock):
    source = RecordingSource()
    cache = TenantConfigCache(source, clock=clock, max_entries=8, ttl_seconds=120.0)
    cache.get("tenant-a")

    snapshot = cache.snapshot()

    assert snapshot["max_entries"] == 8
    assert snapshot["ttl_seconds"] == 120.0
    assert snapshot["entries"] == ["tenant-a"]
    assert snapshot["stats"]["misses"] == 1


@contextmanager
def _aggressive_thread_switching():
    """Force CPython to interleave threads far more often than its 5ms
    default, so races that depend on two statements landing back-to-back
    without a GIL handoff actually get exercised inside a short test."""
    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(original)


class ThreadSafeSource:
    """A source cheap and safe enough to call from many threads at once, so
    the stress tests below exercise the cache's own locking rather than
    contention inside the source itself."""

    name = "threadsafe"

    def load(self, tenant_id):
        def reload():
            return {"LogLevel": "Warning"}

        return TenantConfig(tenant_id=tenant_id, values={"LogLevel": "Warning"}, reload=reload)

    def ping(self):
        return None


def test_concurrent_get_under_capacity_pressure_does_not_raise():
    """Eviction race (Flask's threaded dev server hits this): thread A misses
    on tenant Y and `_evict_over_capacity()` pops tenant X; thread B is
    mid-`get("X")`, already past `self._entries.get`, and calls
    `move_to_end("X")` on the now-absent key. Without a lock around the body
    of `get()`, that raises KeyError. This needs no TTL expiry at all, only
    `max_entries` pressure across more tenants than the cache can hold.
    """
    source = ThreadSafeSource()
    cache = TenantConfigCache(source, max_entries=8)
    tenant_ids = [f"tenant-{i}" for i in range(40)]
    errors = []
    lock = threading.Lock()

    def hammer(worker):
        try:
            for i in range(300):
                cache.get(tenant_ids[(worker + i) % len(tenant_ids)])
        except Exception as exc:  # noqa: BLE001 - anything escaping is the bug
            with lock:
                errors.append(exc)

    with _aggressive_thread_switching(), ThreadPoolExecutor(max_workers=32) as pool:
        futures = [pool.submit(hammer, worker) for worker in range(32)]
        for future in futures:
            future.result()

    assert errors == [], f"cache.get() raised under concurrent eviction pressure: {errors!r}"
    assert len(cache.snapshot()["entries"]) <= 8


def test_concurrent_get_under_ttl_expiry_does_not_raise():
    """TTL race (Flask's threaded dev server hits this): two requests for the
    same tenant arrive after `ttl_seconds` has elapsed. Both read the entry,
    both find it expired, both `del self._entries[tenant_id]` — the second
    raises KeyError. `ttl_seconds=0.0` makes the entry read as expired on
    essentially every `get()` after the first, so this reliably forces threads
    onto that path together without depending on real-clock timing.
    """
    source = ThreadSafeSource()
    cache = TenantConfigCache(source, ttl_seconds=0.0)
    errors = []
    lock = threading.Lock()
    start = threading.Barrier(16)

    def hammer():
        start.wait()
        try:
            for _ in range(300):
                cache.get("tenant-a")
        except Exception as exc:  # noqa: BLE001 - anything escaping is the bug
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(16)]
    with _aggressive_thread_switching():
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert errors == [], f"cache.get() raised under a TTL expiry race: {errors!r}"
