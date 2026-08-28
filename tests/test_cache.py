"""Per-tenant caching: the guidance's application-side caching, made observable."""

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
