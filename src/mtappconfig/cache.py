"""Per-tenant configuration cache.

The guidance is explicit that a multitenant application should load each
tenant's settings on demand rather than loading every tenant's settings at
once, and should cache them keyed by tenant id. This is that cache:

* LRU eviction bounds memory, standing in for the .NET cache's ability to
  drop unused entries under memory pressure.
* A TTL bounds staleness per entry.
* Refresh is activity-driven and rate-limited, because the Python provider
  does not refresh in the background the way the .NET provider does.
* A failed refresh is logged and swallowed: the tenant keeps being served
  from cache rather than seeing an error.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from logging import Logger

from .observability import get_logger
from .source import TenantConfig, TenantConfigSource


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    refresh_failures: int = 0


@dataclass
class _Entry:
    config: TenantConfig
    loaded_at: float
    last_refresh_at: float


class TenantConfigCache:
    def __init__(
        self,
        source: TenantConfigSource,
        *,
        max_entries: int = 64,
        ttl_seconds: float = 300.0,
        refresh_interval_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        logger: Logger | None = None,
    ) -> None:
        self._source = source
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._refresh_interval = refresh_interval_seconds
        self._clock = clock
        self._logger = logger or get_logger(__name__)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self.stats = CacheStats()

    def get(self, tenant_id: str) -> TenantConfig:
        """Return this tenant's config, loading or refreshing as needed.

        The tenant id must already have been validated by the registry.
        """
        now = self._clock()
        entry = self._entries.get(tenant_id)

        if entry is not None and now - entry.loaded_at >= self._ttl:
            del self._entries[tenant_id]
            self.stats.expirations += 1
            entry = None

        if entry is None:
            self.stats.misses += 1
            entry = _Entry(
                config=self._source.load(tenant_id),
                loaded_at=now,
                last_refresh_at=now,
            )
            self._entries[tenant_id] = entry
            self._evict_over_capacity()
            return entry.config

        self.stats.hits += 1
        self._entries.move_to_end(tenant_id)
        if now - entry.last_refresh_at >= self._refresh_interval:
            # Mark the attempt before making it, so a failing store is retried
            # on the next interval rather than on every single request.
            entry.last_refresh_at = now
            try:
                entry.config.refresh()
            except Exception:
                self.stats.refresh_failures += 1
                self._logger.warning(
                    "config refresh failed; serving cached values",
                    extra={"tenant_id": tenant_id, "event": "config.refresh.failed"},
                    exc_info=True,
                )
        return entry.config

    def snapshot(self) -> dict[str, object]:
        """A view of the cache, exposed at /_diagnostics/cache."""
        return {
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl,
            "refresh_interval_seconds": self._refresh_interval,
            "entries": list(self._entries),
            "stats": asdict(self.stats),
        }

    def _evict_over_capacity(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1
