"""Per-tenant configuration cache.

The guidance is explicit that a multitenant application should load each
tenant's settings on demand rather than loading every tenant's settings at
once, and should cache them keyed by tenant id. This is that cache:

* LRU eviction bounds memory. This is this repo's own explicit bound, not
  a claim about what a bare .NET cache does for free: .NET's IMemoryCache
  does not evict entries under memory pressure automatically either — an
  application has to configure SizeLimit and a per-entry Size itself
  (https://learn.microsoft.com/aspnet/core/performance/caching/memory#use-setsize-size-and-sizelimit-to-limit-cache-size).
* A TTL bounds staleness per entry.
* Refresh is activity-driven and rate-limited. Both the Python and .NET
  providers require an explicit trigger to actually refresh — ConfigureRefresh
  plus TryRefreshAsync or middleware on the .NET side (see
  samples/05-dotnet-cache-refresh/) — neither one refreshes purely in the
  background with no caller involvement.
* A failed refresh is logged and swallowed: the tenant keeps being served
  from cache rather than seeing an error.
"""

from __future__ import annotations

import threading
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
        # Flask's dev server is threaded by default, and every sample's README
        # tells the reader to run exactly that, so concurrent calls to get()
        # for the same or different tenants are the normal case, not an edge
        # case. Without this lock, two threads can both observe an entry as
        # expired and both `del` it (the second raising KeyError), or one
        # thread's eviction can pop an entry another thread is mid-read on
        # (its move_to_end() then raising KeyError on the now-absent key).
        self._lock = threading.Lock()

    def get(self, tenant_id: str) -> TenantConfig:
        """Return this tenant's config, loading or refreshing as needed.

        The tenant id must already have been validated by the registry.

        The lock is held for the whole call, including a cold `source.load()`
        or a due `config.refresh()`. For a sample that is the right trade: it
        collapses a thundering herd of concurrent cold misses for the same
        tenant into a single load, at the cost of serialising unrelated
        tenants' requests behind a slow load. A production service whose
        store can be slow to respond might prefer a per-tenant lock instead,
        so that one tenant's slow load cannot stall every other tenant.
        """
        with self._lock:
            now = self._clock()
            entry = self._entries.get(tenant_id)

            if entry is not None and now - entry.loaded_at >= self._ttl:
                self._safe_close(entry)
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
        with self._lock:
            return {
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "refresh_interval_seconds": self._refresh_interval,
                "entries": list(self._entries),
                "stats": asdict(self.stats),
            }

    def _evict_over_capacity(self) -> None:
        while len(self._entries) > self._max_entries:
            _, entry = self._entries.popitem(last=False)
            self._safe_close(entry)
            self.stats.evictions += 1

    def _safe_close(self, entry: _Entry) -> None:
        if entry.config.close is None:
            return
        try:
            entry.config.close()
        except Exception:
            self._logger.warning(
                "closing tenant's config source failed",
                extra={"tenant_id": entry.config.tenant_id, "event": "config.close.failed"},
                exc_info=True,
            )
