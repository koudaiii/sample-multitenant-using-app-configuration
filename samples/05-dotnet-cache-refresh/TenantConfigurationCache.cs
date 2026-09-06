namespace MtAppConfig.CacheRefresh;

using Microsoft.Extensions.Configuration;

public sealed record TenantConfigEntry(IConfiguration Configuration, ITenantConfigRefresher Refresher);

/// <summary>
/// Caches one IConfiguration object per tenant, keyed by tenant id, and
/// exposes an explicit refresh trigger — this is the multitenant caching
/// pattern the article describes for .NET applications
/// ("cache the tenant's IConfiguration object and use the tenant
/// identifier as the cache key"; "trigger the refresh by calling
/// TryRefreshAsync"). Compare with src/mtappconfig/cache.py on the Python
/// side of this repo, which caches TenantConfig the same way.
/// </summary>
public sealed class TenantConfigurationCache
{
    private readonly Func<string, TenantConfigEntry> _loader;
    private readonly Dictionary<string, TenantConfigEntry> _entries = new();

    // The lock is held for the whole call, including a cold Get() that
    // blocks on a live network round-trip inside ConfigurationBuilder.Build().
    // For a sample that's an acceptable trade: it collapses concurrent
    // cold loads for the same tenant into one, at the cost of stalling
    // every other already-cached tenant's Get() while one tenant loads.
    // A production service might prefer a per-tenant lock instead.
    // Compare src/mtappconfig/cache.py's TenantConfigCache.get on the
    // Python side of this repo, which documents and makes the same trade-off.
    private readonly object _lock = new();

    public TenantConfigurationCache(Func<string, TenantConfigEntry> loader)
    {
        _loader = loader;
    }

    /// <summary>Loads on first access for this tenant id; returns the cached entry's configuration afterward.</summary>
    public IConfiguration Get(string tenantId)
    {
        lock (_lock)
        {
            if (!_entries.TryGetValue(tenantId, out var entry))
            {
                entry = _loader(tenantId);
                _entries[tenantId] = entry;
            }
            return entry.Configuration;
        }
    }

    /// <summary>
    /// Explicitly triggers a refresh check for tenantId. If the tenant has
    /// never been loaded, this loads it instead and returns true (a
    /// successful load counts as success, the same as a successful
    /// refresh). If the tenant is already cached, this calls through to
    /// its refresher and returns exactly what TryRefreshAsync reports:
    /// true if the attempt succeeded (including a no-op skip before the
    /// refresh interval elapses), false only if the attempt failed.
    /// </summary>
    public async Task<bool> RefreshAsync(string tenantId, CancellationToken cancellationToken = default)
    {
        TenantConfigEntry entry;
        lock (_lock)
        {
            if (!_entries.TryGetValue(tenantId, out entry!))
            {
                entry = _loader(tenantId);
                _entries[tenantId] = entry;
                return true;
            }
        }
        return await entry.Refresher.TryRefreshAsync(cancellationToken);
    }
}
